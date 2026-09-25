"""Exhaustive projection from durable semantic parts to Runtime Events."""

from __future__ import annotations

from typing import Literal, assert_never

from fleet_rlm.rlm.events import (
    ArtifactCreated,
    AttachmentRead,
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
from fleet_rlm.sessions.committed_turn import (
    ArtifactPart,
    AttachmentPart,
    CodePart,
    CommittedPart,
    CommittedTurn,
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
)

ProjectionMode = Literal["replay", "live_suffix"]
_SUFFIX_TYPES = (ArtifactPart, UsagePart, StructuredResultPart, TextPart)


class CommittedTurnProjectionError(ValueError):
    """Raised when durable data cannot be projected without semantic loss."""


def _details(part: CommittedPart):
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
        return (Usage(value=part.value),)
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
            events.extend(recorder.record(detail) for detail in _details(part))
        return tuple(events)
