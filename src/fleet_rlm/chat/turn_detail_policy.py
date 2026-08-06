"""Normalize one successful runner outcome into the durable Turn contract."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fleet_rlm.artifacts.models import ArtifactRef
from fleet_rlm.observability.turn_tracing import current_turn_trace_id
from fleet_rlm.rlm.events import (
    AttachmentRead,
    RLMCode,
    RLMOutput,
    RLMReasoning,
    SkillActivated,
    SkillLoaded,
    StepFinished,
    StepStarted,
    ToolCompleted,
    ToolFailed,
    ToolStarted,
    WarningEvent,
)
from fleet_rlm.rlm.outcome import RLMOutcome
from fleet_rlm.sessions.committed_turn import (
    ArtifactPart,
    AttachmentPart,
    CodePart,
    CommittedPart,
    CommittedTurn,
    OutputPart,
    ReasoningPart,
    SkillPart,
    StepPart,
    StructuredResultPart,
    TextPart,
    ToolCallPart,
    UsagePart,
    WarningPart,
)


class TurnDetailPolicyError(ValueError):
    """Raised when an outcome cannot be represented by the durable contract."""


@dataclass(frozen=True, slots=True)
class _PendingToolCall:
    detail: ToolStarted
    position: int


@dataclass(slots=True)
class _NormalizeState:
    parts: list[CommittedPart | None]
    pending: dict[str, _PendingToolCall]
    streaming_outputs: dict[str, int]


def _append_step_started(detail: StepStarted, state: _NormalizeState) -> None:
    state.parts.append(StepPart(state="started", step=detail.step))


def _append_step_finished(detail: StepFinished, state: _NormalizeState) -> None:
    state.parts.append(StepPart(state="finished", step=detail.step, duration_ms=detail.duration_ms))


def _append_reasoning(detail: RLMReasoning, state: _NormalizeState) -> None:
    state.parts.append(ReasoningPart(text=detail.text, step=detail.step))


def _append_code(detail: RLMCode, state: _NormalizeState) -> None:
    state.parts.append(CodePart(code=detail.code, step=detail.step))


def _append_output(detail: RLMOutput, state: _NormalizeState) -> None:
    stream_id = detail.stream_id
    position = state.streaming_outputs.get(stream_id) if stream_id else None
    if detail.is_delta and stream_id:
        if position is None:
            state.streaming_outputs[stream_id] = len(state.parts)
            state.parts.append(OutputPart(output=detail.output, step=detail.step))
            return
        prior = state.parts[position]
        if not isinstance(prior, OutputPart):
            raise TurnDetailPolicyError("streaming output position is not an output part")
        state.parts[position] = OutputPart(output=prior.output + detail.output, step=detail.step)
        return

    part = OutputPart(output=detail.output, step=detail.step)
    if position is not None:
        state.parts[position] = part
        return
    if stream_id:
        state.streaming_outputs[stream_id] = len(state.parts)
    state.parts.append(part)


def _append_tool_started(detail: ToolStarted, state: _NormalizeState) -> None:
    if detail.tool_call_id in state.pending:
        raise TurnDetailPolicyError("duplicate tool call start")
    state.pending[detail.tool_call_id] = _PendingToolCall(detail, len(state.parts))
    state.parts.append(None)


def _append_tool_terminal(detail: ToolCompleted | ToolFailed, state: _NormalizeState) -> None:
    started = state.pending.pop(detail.tool_call_id, None)
    if started is None:
        raise TurnDetailPolicyError("tool completion has no matching start")
    if started.detail.tool_name != detail.tool_name:
        raise TurnDetailPolicyError("tool completion name does not match its start")
    if isinstance(detail, ToolCompleted):
        part = ToolCallPart(
            tool_call_id=detail.tool_call_id,
            tool_name=detail.tool_name,
            state="completed",
            input=started.detail.input,
            output=detail.output,
        )
    else:
        part = ToolCallPart(
            tool_call_id=detail.tool_call_id,
            tool_name=detail.tool_name,
            state="failed",
            input=started.detail.input,
            error=detail.error,
        )
    state.parts[started.position] = part


def _append_skill_activated(detail: SkillActivated, state: _NormalizeState) -> None:
    state.parts.append(
        SkillPart(
            skill_id=detail.skill_id,
            name=detail.name,
            phase="activated",
            version=detail.version,
            trust=detail.trust,
            affordances=detail.affordances,
        )
    )


def _append_skill_loaded(detail: SkillLoaded, state: _NormalizeState) -> None:
    state.parts.append(
        SkillPart(
            skill_id=detail.skill_id,
            name=detail.name,
            phase="loaded",
            version=detail.version,
        )
    )


def _append_attachment_read(detail: AttachmentRead, state: _NormalizeState) -> None:
    state.parts.append(
        AttachmentPart(
            attachment_id=detail.attachment_id,
            phase="read",
            filename=detail.filename,
            byte_size=detail.byte_size,
        )
    )


def _append_warning(detail: WarningEvent, state: _NormalizeState) -> None:
    state.parts.append(WarningPart(message=detail.message, code=detail.code))


_DetailHandler = Callable[[Any, _NormalizeState], None]

_DETAIL_HANDLERS: dict[type, _DetailHandler] = {
    StepStarted: _append_step_started,
    StepFinished: _append_step_finished,
    RLMReasoning: _append_reasoning,
    RLMCode: _append_code,
    RLMOutput: _append_output,
    ToolStarted: _append_tool_started,
    ToolCompleted: _append_tool_terminal,
    ToolFailed: _append_tool_terminal,
    SkillActivated: _append_skill_activated,
    SkillLoaded: _append_skill_loaded,
    AttachmentRead: _append_attachment_read,
    WarningEvent: _append_warning,
}


def _normalize_execution(outcome: RLMOutcome) -> list[CommittedPart]:
    state = _NormalizeState(parts=[], pending={}, streaming_outputs={})

    for detail in outcome.execution_details:
        handler = _DETAIL_HANDLERS.get(type(detail))
        if handler is None:
            raise TurnDetailPolicyError(f"unsupported execution detail: {type(detail).__name__}")
        handler(detail, state)

    if state.pending:
        raise TurnDetailPolicyError("tool call start has no terminal observation")
    return [part for part in state.parts if part is not None]


def commit_success(outcome: RLMOutcome, artifacts: tuple[ArtifactRef, ...]) -> CommittedTurn:
    """Build the sole canonical durable representation of a successful Run."""

    if not outcome.succeeded:
        raise TurnDetailPolicyError("only successful outcomes can be committed")

    parts = _normalize_execution(outcome)
    parts.extend(
        ArtifactPart(
            artifact_id=artifact.id,
            kind=artifact.kind,
            title=artifact.title,
            media_type=artifact.media_type,
            byte_size=artifact.byte_size,
            checksum_sha256=artifact.checksum_sha256,
        )
        for artifact in artifacts
    )
    parts.append(UsagePart(value=outcome.usage))

    prediction = outcome.prediction
    if prediction is None:
        raise TurnDetailPolicyError("successful outcome requires a prediction")
    if len(prediction.outputs) > 1:
        parts.append(
            StructuredResultPart(
                schema_id=prediction.schema_id,
                schema_version=prediction.schema_version,
                value=prediction.outputs,
            )
        )
    parts.append(TextPart(text=prediction.display_text))
    return CommittedTurn(schema_version=1, parts=tuple(parts), trace_id=current_turn_trace_id())
