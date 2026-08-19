"""Authoritative canonical semantic event schema for one Turn (P24/QRE-168).

One closed, discriminated vocabulary covering text, reasoning, code, output,
tool lifecycle, Skill lifecycle, Attachments, Artifacts, warnings, usage,
structured results, steps/status, and Run lifecycle edges. Both wire
projections (live SSE chunks, durable reload parts) adapt INTO it — casing
and wrapper-shape compatibility lives only in the adapters, never downstream.
"""

from __future__ import annotations

from typing import Annotated, Any, ClassVar, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field

JsonValue: TypeAlias = Any


class CanonicalEventModel(BaseModel):
    """Strict base: closed vocabulary, no unknown fields."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    event: ClassVar[str]


class TurnStart(CanonicalEventModel):
    event: ClassVar[Literal["turn_start"]] = "turn_start"
    type: Literal["turn_start"] = "turn_start"
    run_id: str
    delivery: Literal["live", "replay"] | None = None
    trace_id: str | None = None


class TurnContext(CanonicalEventModel):
    """Adapter-provided run identity for message scoping; NOT a lifecycle event."""

    event: ClassVar[Literal["turn_context"]] = "turn_context"
    type: Literal["turn_context"] = "turn_context"
    run_id: str


class TurnStatus(CanonicalEventModel):
    event: ClassVar[Literal["turn_status"]] = "turn_status"
    type: Literal["turn_status"] = "turn_status"
    phase: str
    detail: str | None = None


class TurnFinish(CanonicalEventModel):
    event: ClassVar[Literal["turn_finish"]] = "turn_finish"
    type: Literal["turn_finish"] = "turn_finish"
    finish_reason: str
    error: str | None = None
    duration_ms: int | None = None
    checkpoint_version: int | None = None
    trace_id: str | None = None


class TurnCancelled(CanonicalEventModel):
    event: ClassVar[Literal["turn_cancelled"]] = "turn_cancelled"
    type: Literal["turn_cancelled"] = "turn_cancelled"
    reason: str | None = None


class TurnError(CanonicalEventModel):
    event: ClassVar[Literal["error"]] = "error"
    type: Literal["error"] = "error"
    text: str


class StepStart(CanonicalEventModel):
    event: ClassVar[Literal["step_start"]] = "step_start"
    type: Literal["step_start"] = "step_start"
    step: int | None = None


class StepFinish(CanonicalEventModel):
    event: ClassVar[Literal["step_finish"]] = "step_finish"
    type: Literal["step_finish"] = "step_finish"
    step: int | None = None
    duration_ms: int | None = None


class ReasoningPartEvent(CanonicalEventModel):
    event: ClassVar[Literal["reasoning"]] = "reasoning"
    type: Literal["reasoning"] = "reasoning"
    stream_id: str
    step: int = 0
    text: str = ""
    final: bool = False
    message_id: str | None = None


class TextPartEvent(CanonicalEventModel):
    event: ClassVar[Literal["text"]] = "text"
    type: Literal["text"] = "text"
    stream_id: str
    text_delta: str = ""
    final: bool = False
    role: Literal["user", "assistant"] = "assistant"
    message_id: str | None = None


class CodePartEvent(CanonicalEventModel):
    event: ClassVar[Literal["code"]] = "code"
    type: Literal["code"] = "code"
    stream_id: str
    step: int = 0
    code_delta: str = ""
    is_delta: bool = False
    final: bool = True
    message_id: str | None = None


class OutputPartEvent(CanonicalEventModel):
    event: ClassVar[Literal["output"]] = "output"
    type: Literal["output"] = "output"
    stream_id: str
    step: int = 0
    output_delta: str = ""
    is_delta: bool = False
    final: bool = True
    message_id: str | None = None


class ToolCallEvent(CanonicalEventModel):
    event: ClassVar[Literal["tool_call"]] = "tool_call"
    type: Literal["tool_call"] = "tool_call"
    tool_call_id: str
    tool_name: str
    input: JsonValue = None
    message_id: str | None = None


class ToolResultEvent(CanonicalEventModel):
    event: ClassVar[Literal["tool_result"]] = "tool_result"
    type: Literal["tool_result"] = "tool_result"
    tool_call_id: str
    tool_name: str | None = None
    output: JsonValue = None
    error: str | None = None
    message_id: str | None = None


class SkillEvent(CanonicalEventModel):
    event: ClassVar[Literal["skill"]] = "skill"
    type: Literal["skill"] = "skill"
    stream_id: str | None = None
    message_id: str | None = None
    skill_id: str | None = None
    phase: str | None = None
    name: str | None = None
    version: str | None = None
    trust: str | None = None
    affordances: list[str] | None = None


class AttachmentEvent(CanonicalEventModel):
    event: ClassVar[Literal["attachment"]] = "attachment"
    type: Literal["attachment"] = "attachment"
    stream_id: str | None = None
    message_id: str | None = None
    attachment_id: str | None = None
    phase: str | None = None
    filename: str | None = None
    byte_size: int | None = None


class WarningEvent(CanonicalEventModel):
    event: ClassVar[Literal["warning"]] = "warning"
    type: Literal["warning"] = "warning"
    stream_id: str | None = None
    message_id: str | None = None
    code: str
    message: str


class ArtifactEvent(CanonicalEventModel):
    event: ClassVar[Literal["artifact"]] = "artifact"
    type: Literal["artifact"] = "artifact"
    stream_id: str | None = None
    message_id: str | None = None
    artifact_id: str | None = None
    artifact_kind: str | None = None
    title: str | None = None
    media_type: str | None = None
    byte_size: int | None = None
    checksum_sha256: str | None = None


class UsageEvent(CanonicalEventModel):
    event: ClassVar[Literal["usage"]] = "usage"
    type: Literal["usage"] = "usage"
    stream_id: str | None = None
    message_id: str | None = None
    iterations: int = 0
    duration_ms: int | None = None
    usage: JsonValue = None


class StructuredResultEvent(CanonicalEventModel):
    event: ClassVar[Literal["structured_result"]] = "structured_result"
    type: Literal["structured_result"] = "structured_result"
    stream_id: str | None = None
    message_id: str | None = None
    schema_id: str | None = None
    schema_version: str | None = None
    value: JsonValue = None


CanonicalEvent: TypeAlias = Annotated[
    TurnStart
    | TurnContext
    | TurnStatus
    | TurnFinish
    | TurnCancelled
    | TurnError
    | StepStart
    | StepFinish
    | ReasoningPartEvent
    | TextPartEvent
    | CodePartEvent
    | OutputPartEvent
    | ToolCallEvent
    | ToolResultEvent
    | SkillEvent
    | AttachmentEvent
    | WarningEvent
    | ArtifactEvent
    | UsageEvent
    | StructuredResultEvent,
    Field(discriminator="type"),
]

CANONICAL_EVENT_KINDS: tuple[str, ...] = (
    "turn_start",
    "turn_context",
    "turn_status",
    "turn_finish",
    "turn_cancelled",
    "error",
    "step_start",
    "step_finish",
    "reasoning",
    "text",
    "code",
    "output",
    "tool_call",
    "tool_result",
    "skill",
    "attachment",
    "warning",
    "artifact",
    "usage",
    "structured_result",
)

_KIND_MODELS = {
    model.event: model
    for model in (
        TurnStart,
        TurnContext,
        TurnStatus,
        TurnFinish,
        TurnCancelled,
        TurnError,
        StepStart,
        StepFinish,
        ReasoningPartEvent,
        TextPartEvent,
        CodePartEvent,
        OutputPartEvent,
        ToolCallEvent,
        ToolResultEvent,
        SkillEvent,
        AttachmentEvent,
        WarningEvent,
        ArtifactEvent,
        UsageEvent,
        StructuredResultEvent,
    )
}

assert frozenset(_KIND_MODELS) == frozenset(CANONICAL_EVENT_KINDS)


def canonical_event_from_json(payload: dict[str, Any]) -> CanonicalEvent:
    """Parse and strictly validate one canonical event JSON object."""
    kind = payload.get("type")
    model = _KIND_MODELS.get(kind)
    if model is None:
        raise ValueError(f"unknown canonical event type: {kind!r}")
    return model.model_validate(payload)  # type: ignore[return-value]


def canonical_event_to_json(event: CanonicalEvent) -> dict[str, Any]:
    """Serialize one canonical event with top-level Nones omitted."""
    dumped = event.model_dump(mode="json")
    return {key: value for key, value in dumped.items() if value is not None}


__all__ = [
    "CANONICAL_EVENT_KINDS",
    "CanonicalEvent",
    "CanonicalEventModel",
    "canonical_event_from_json",
    "canonical_event_to_json",
]
