"""Normalize one successful runner outcome into the durable Turn contract."""

from __future__ import annotations

from dataclasses import dataclass

from fleet_rlm.artifacts.models import ArtifactRef
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


def _normalize_execution(outcome: RLMOutcome) -> list[CommittedPart]:
    parts: list[CommittedPart | None] = []
    pending: dict[str, _PendingToolCall] = {}

    for detail in outcome.execution_details:
        if isinstance(detail, StepStarted):
            parts.append(StepPart(state="started", step=detail.step))
        elif isinstance(detail, StepFinished):
            parts.append(StepPart(state="finished", step=detail.step, duration_ms=detail.duration_ms))
        elif isinstance(detail, RLMReasoning):
            parts.append(ReasoningPart(text=detail.text, step=detail.step))
        elif isinstance(detail, RLMCode):
            parts.append(CodePart(code=detail.code, step=detail.step))
        elif isinstance(detail, RLMOutput):
            parts.append(OutputPart(output=detail.output, step=detail.step))
        elif isinstance(detail, ToolStarted):
            if detail.tool_call_id in pending:
                raise TurnDetailPolicyError("duplicate tool call start")
            pending[detail.tool_call_id] = _PendingToolCall(detail, len(parts))
            parts.append(None)
        elif isinstance(detail, (ToolCompleted, ToolFailed)):
            started = pending.pop(detail.tool_call_id, None)
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
            parts[started.position] = part
        elif isinstance(detail, SkillActivated):
            parts.append(
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
            parts.append(
                SkillPart(
                    skill_id=detail.skill_id,
                    name=detail.name,
                    phase="loaded",
                    version=detail.version,
                )
            )
        elif isinstance(detail, AttachmentRead):
            parts.append(
                AttachmentPart(
                    attachment_id=detail.attachment_id,
                    phase="read",
                    filename=detail.filename,
                    byte_size=detail.byte_size,
                )
            )
        elif isinstance(detail, WarningEvent):
            parts.append(WarningPart(message=detail.message, code=detail.code))
        else:
            raise TurnDetailPolicyError(f"unsupported execution detail: {type(detail).__name__}")

    if pending:
        raise TurnDetailPolicyError("tool call start has no terminal observation")
    return [part for part in parts if part is not None]


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
    return CommittedTurn(schema_version=1, parts=tuple(parts))
