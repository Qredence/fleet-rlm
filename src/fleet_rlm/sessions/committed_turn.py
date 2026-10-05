"""Closed durable semantic result for one successfully committed Turn."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, assert_never, cast

from fleet_rlm.artifacts.models import ArtifactRef
from fleet_rlm.json_types import JsonScalar as JsonScalar
from fleet_rlm.json_types import JsonValue as JsonValue
from fleet_rlm.observability.tracing import current_turn_trace_id
from fleet_rlm.rlm.events import (
    ArtifactCreated,
    AttachmentRead,
    ChildProgress,
    EventRecorder,
    RLMCode,
    RLMOutput,
    RLMReasoning,
    RuntimeEvent,
    SkillActivated,
    SkillLoaded,
    Status,
    StepFinished,
    StepStarted,
    StructuredResult,
    TextCompleted,
    TextDelta,
    ToolCompleted,
    ToolFailed,
    ToolStarted,
    Usage,
    WarningEvent,
)
from fleet_rlm.rlm.result import RLMOutcome
from fleet_rlm.sessions.assistant_parts import (
    ArtifactPart,
    AttachmentPart,
    ChildProgressPart,
    CodePart,
    CommittedPart,
    CommittedTurnValidationError,
    OutputPart,
    ReasoningPart,
    SkillPart,
    StatusPart,
    StepPart,
    StructuredResultPart,
    TextPart,
    ToolCallPart,
    UsagePart,
    WarningPart,
    assistant_part_from_payload,
    assistant_part_payload,
)
from fleet_rlm.sessions.usage import RLMUsage

_EXECUTION_PARTS = (
    StepPart,
    ReasoningPart,
    CodePart,
    OutputPart,
    ToolCallPart,
    SkillPart,
    AttachmentPart,
    WarningPart,
    StatusPart,
    ChildProgressPart,
)


@dataclass(frozen=True, slots=True)
class CommittedTurn:
    """The sole durable semantic result of one successful Run."""

    schema_version: Literal[1]
    parts: tuple[CommittedPart, ...]
    trace_id: str | None = None

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise CommittedTurnValidationError("unsupported committed Turn schema version")
        bands: list[int] = []
        usage_count = 0
        structured_count = 0
        text_count = 0
        for part in self.parts:
            if isinstance(part, _EXECUTION_PARTS):
                bands.append(0)
            elif isinstance(part, ArtifactPart):
                bands.append(1)
            elif isinstance(part, UsagePart):
                usage_count += 1
                bands.append(2)
            elif isinstance(part, StructuredResultPart):
                structured_count += 1
                bands.append(3)
            elif isinstance(part, TextPart):
                text_count += 1
                bands.append(4)
            else:
                raise CommittedTurnValidationError(f"unsupported committed part: {type(part).__name__}")
        if bands != sorted(bands):
            raise CommittedTurnValidationError("committed parts are not in canonical order")
        if usage_count != 1:
            raise CommittedTurnValidationError("a committed Turn requires exactly one usage part")
        if structured_count > 1:
            raise CommittedTurnValidationError("a committed Turn allows at most one structured result")
        if text_count != 1 or not self.parts or not isinstance(self.parts[-1], TextPart):
            raise CommittedTurnValidationError("a committed Turn requires exactly one final text part")

    @property
    def text(self) -> str:
        return cast(TextPart, self.parts[-1]).text

    @property
    def structured_result(self) -> Any | None:
        for part in self.parts:
            if isinstance(part, StructuredResultPart):
                return part.value
        return None


def _expect_mapping(value: object, path: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise CommittedTurnValidationError(f"{path} must be an object")
    return cast(Mapping[str, object], value)


class CommittedTurnCodec:
    """Strict JSON codec for the versioned aggregate."""

    @staticmethod
    def encode(committed: CommittedTurn) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": committed.schema_version,
            "parts": [assistant_part_payload(part) for part in committed.parts],
        }
        if committed.trace_id:
            payload["trace_id"] = committed.trace_id
        return payload

    @staticmethod
    def decode(value: object) -> CommittedTurn:
        data = _expect_mapping(value, "committed Turn")
        keys = set(data)
        if not {"schema_version", "parts"} <= keys or keys - {"schema_version", "parts", "trace_id"}:
            raise CommittedTurnValidationError("committed part has missing or unknown fields")
        if data.get("schema_version") != 1:
            raise CommittedTurnValidationError("unsupported committed Turn schema version")
        raw_parts = data.get("parts")
        if not isinstance(raw_parts, Sequence) or isinstance(raw_parts, (str, bytes, bytearray)):
            raise CommittedTurnValidationError("committed Turn parts must be an array")
        trace_id = data.get("trace_id")
        if trace_id is not None and not isinstance(trace_id, str):
            raise CommittedTurnValidationError("trace_id must be a string or null")
        parts = tuple(assistant_part_from_payload(part) for part in raw_parts)
        return CommittedTurn(schema_version=1, parts=parts, trace_id=trace_id)


CANCELLED_TOMBSTONE_TEXT = "Turn cancelled"


def commit_cancelled_tombstone(usage: RLMUsage) -> CommittedTurn:
    """Build the bounded D2 tombstone committed for one cancelled Run."""
    return CommittedTurn(
        schema_version=1,
        parts=(
            StatusPart(phase="cancelled", status="cancelled"),
            UsagePart(value=usage),
            TextPart(text=CANCELLED_TOMBSTONE_TEXT),
        ),
    )


ProjectionMode = Literal["replay", "live_suffix"]
_SUFFIX_TYPES = (ArtifactPart, UsagePart, StructuredResultPart, TextPart)


class CommittedTurnProjectionError(ValueError):
    """Raised when durable data cannot be projected without semantic loss."""


def _project_part_details(part: CommittedPart):
    if isinstance(part, StepPart):
        if part.state == "started":
            return (StepStarted(step=part.step),)
        return (StepFinished(step=part.step, duration_ms=part.duration_ms),)
    if isinstance(part, ReasoningPart):
        return (RLMReasoning(text=part.text, step=part.step),)
    if isinstance(part, CodePart):
        return (RLMCode(code=part.code, step=part.step),)
    if isinstance(part, OutputPart):
        return (RLMOutput(output=part.output, step=part.step),)
    if isinstance(part, ToolCallPart):
        started = ToolStarted(
            tool_call_id=part.tool_call_id,
            tool_name=part.tool_name,
            input=part.input,
        )
        if part.state == "completed":
            return (
                started,
                ToolCompleted(
                    tool_call_id=part.tool_call_id,
                    tool_name=part.tool_name,
                    output=part.output,
                ),
            )
        return (
            started,
            ToolFailed(
                tool_call_id=part.tool_call_id,
                tool_name=part.tool_name,
                error=part.error or "Tool failed",
            ),
        )
    if isinstance(part, SkillPart):
        if part.phase == "activated":
            if part.version is None or part.trust is None:
                raise CommittedTurnProjectionError("activated skill metadata is incomplete")
            return (
                SkillActivated(
                    skill_id=part.skill_id,
                    name=part.name,
                    version=part.version,
                    trust=part.trust,
                    affordances=part.affordances,
                ),
            )
        if part.version is None:
            raise CommittedTurnProjectionError("loaded skill version is missing")
        return (SkillLoaded(skill_id=part.skill_id, name=part.name, version=part.version),)
    if isinstance(part, AttachmentPart):
        if part.phase != "read" or part.filename is None or part.byte_size is None:
            raise CommittedTurnProjectionError("only complete Attachment reads are replayable")
        return (
            AttachmentRead(
                attachment_id=part.attachment_id,
                filename=part.filename,
                byte_size=part.byte_size,
            ),
        )
    if isinstance(part, WarningPart):
        return (WarningEvent(message=part.message, code=part.code),)
    if isinstance(part, StatusPart):
        return (Status(phase=part.phase, status=part.status, message=part.message),)
    if isinstance(part, ChildProgressPart):
        return (
            ChildProgress(
                child_id=part.child_id,
                task_label=part.task_label,
                state=part.state,
                elapsed_ms=part.elapsed_ms,
                outcome=part.outcome,
                evidence=part.evidence,
                gaps=part.gaps,
                result_file_count=part.result_file_count,
                code_excerpt=part.code_excerpt,
                output_excerpt=part.output_excerpt,
                cleanup_state=part.cleanup_state,
                parent_run_id=part.parent_run_id,
            ),
        )
    if isinstance(part, ArtifactPart):
        return (
            ArtifactCreated(
                artifact_id=part.artifact_id,
                artifact_kind=part.kind,
                title=part.title,
                media_type=part.media_type,
                byte_size=part.byte_size,
                checksum_sha256=part.checksum_sha256,
            ),
        )
    if isinstance(part, UsagePart):
        return (Usage(value=cast(RLMUsage, dict(part.value))),)
    if isinstance(part, StructuredResultPart):
        return (
            StructuredResult(
                schema_id=part.schema_id,
                schema_version=part.schema_version,
                value=part.value,
            ),
        )
    if isinstance(part, TextPart):
        return (TextDelta(text=part.text), TextCompleted(text=part.text))
    assert_never(part)


class CommittedTurnEventProjector:
    """Project replay or the post-commit live suffix using caller delivery state."""

    def project(
        self,
        turn: CommittedTurn,
        recorder: EventRecorder,
        *,
        mode: ProjectionMode,
    ) -> tuple[RuntimeEvent, ...]:
        events: list[RuntimeEvent] = []
        for part in turn.parts:
            if mode == "live_suffix" and not isinstance(part, _SUFFIX_TYPES):
                continue
            events.extend(recorder.record(detail) for detail in _project_part_details(part))
        return tuple(events)


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
    child_progress: dict[str, int]


def _append_output_detail(detail: RLMOutput, state: _NormalizeState) -> None:
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


def _append_tool_terminal_detail(detail: ToolCompleted | ToolFailed, state: _NormalizeState) -> None:
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


def _normalize_execution(outcome: RLMOutcome) -> list[CommittedPart]:
    state = _NormalizeState(parts=[], pending={}, streaming_outputs={}, child_progress={})

    for detail in outcome.execution_details:
        if isinstance(detail, StepStarted):
            state.parts.append(StepPart(state="started", step=detail.step))
        elif isinstance(detail, StepFinished):
            state.parts.append(StepPart(state="finished", step=detail.step, duration_ms=detail.duration_ms))
        elif isinstance(detail, RLMReasoning):
            state.parts.append(ReasoningPart(text=detail.text, step=detail.step))
        elif isinstance(detail, RLMCode):
            state.parts.append(CodePart(code=detail.code, step=detail.step))
        elif isinstance(detail, RLMOutput):
            _append_output_detail(detail, state)
        elif isinstance(detail, ToolStarted):
            if detail.tool_call_id in state.pending:
                raise TurnDetailPolicyError("duplicate tool call start")
            state.pending[detail.tool_call_id] = _PendingToolCall(detail, len(state.parts))
            state.parts.append(None)
        elif isinstance(detail, (ToolCompleted, ToolFailed)):
            _append_tool_terminal_detail(detail, state)
        elif isinstance(detail, SkillActivated):
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
        elif isinstance(detail, SkillLoaded):
            state.parts.append(
                SkillPart(
                    skill_id=detail.skill_id,
                    name=detail.name,
                    phase="loaded",
                    version=detail.version,
                )
            )
        elif isinstance(detail, AttachmentRead):
            state.parts.append(
                AttachmentPart(
                    attachment_id=detail.attachment_id,
                    phase="read",
                    filename=detail.filename,
                    byte_size=detail.byte_size,
                )
            )
        elif isinstance(detail, WarningEvent):
            state.parts.append(WarningPart(message=detail.message, code=detail.code))
        elif isinstance(detail, ChildProgress):
            if detail.state != "running":
                part = ChildProgressPart(
                    child_id=detail.child_id,
                    task_label=detail.task_label,
                    state=detail.state,
                    elapsed_ms=detail.elapsed_ms,
                    outcome=detail.outcome,
                    evidence=detail.evidence,
                    gaps=detail.gaps,
                    result_file_count=detail.result_file_count,
                    code_excerpt=detail.code_excerpt,
                    output_excerpt=detail.output_excerpt,
                    cleanup_state=detail.cleanup_state,
                    parent_run_id=detail.parent_run_id,
                )
                pos = state.child_progress.get(detail.child_id)
                if pos is None:
                    state.child_progress[detail.child_id] = len(state.parts)
                    state.parts.append(part)
                else:
                    state.parts[pos] = part
        else:
            raise TurnDetailPolicyError(f"unsupported execution detail: {type(detail).__name__}")

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


__all__ = [
    "CANCELLED_TOMBSTONE_TEXT",
    "ArtifactPart",
    "AttachmentPart",
    "ChildProgressPart",
    "CodePart",
    "CommittedPart",
    "CommittedTurn",
    "CommittedTurnCodec",
    "CommittedTurnEventProjector",
    "CommittedTurnProjectionError",
    "CommittedTurnValidationError",
    "OutputPart",
    "ProjectionMode",
    "ReasoningPart",
    "SkillPart",
    "StatusPart",
    "StepPart",
    "StructuredResultPart",
    "TextPart",
    "ToolCallPart",
    "TurnDetailPolicyError",
    "UsagePart",
    "WarningPart",
    "commit_cancelled_tombstone",
    "commit_success",
]
