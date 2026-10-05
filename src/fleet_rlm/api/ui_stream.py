"""Typed stream event and chunk transport models for Fleet SSE.

These discriminated models are the bounded live transport contract. Both modern
event shapes and legacy transport chunk shapes are admitted to allow smooth
client compatibility across API revisions.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator

from fleet_rlm.api.json_util import to_plain_json

_JsonData = dict[str, Any]


class FleetUIChunkModel(BaseModel):
    """Strict base for one live transport chunk frame."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class FleetUIDataModel(BaseModel):
    """Closed data payload model for legacy data fields."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# --- Legacy Data Models ---


class StatusData(FleetUIDataModel):
    phase: str
    status: str | None = None
    detail: str | None = None
    message: str | None = None


class ChildProgressData(FleetUIDataModel):
    child_id: str = Field(min_length=1, max_length=128)
    task_label: str = Field(min_length=1, max_length=240)
    state: Literal["not_started", "running", "completed", "failed", "cancelled", "timed_out"]
    elapsed_ms: int = Field(ge=0)
    outcome: str | None = Field(default=None, max_length=500)
    evidence: list[str] = Field(default_factory=list, max_length=8)
    gaps: list[str] = Field(default_factory=list, max_length=8)
    result_file_count: int = Field(default=0, ge=0, le=16)
    code_excerpt: str | None = Field(default=None, max_length=800)
    output_excerpt: str | None = Field(default=None, max_length=800)
    cleanup_state: Literal["pending", "complete", "failed", "not_required"]
    parent_run_id: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("parent_run_id")
    @classmethod
    def _nonblank_parent_run_id(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("parent_run_id must not be blank")
        return value

    @field_validator("evidence", "gaps")
    @classmethod
    def _bounded_details(cls, value: list[str]) -> list[str]:
        if any(len(item) > 200 for item in value):
            raise ValueError("child progress detail exceeds 200 characters")
        return value


class SkillData(FleetUIDataModel):
    skill_id: str
    name: str
    version: str
    phase: Literal["activated", "loaded"] | None = None
    trust: str | None = None
    affordances: list[str] | None = None


class RLMCodeData(FleetUIDataModel):
    code: str
    step: int | None = None
    stream_id: str | None = None
    is_delta: bool | None = None
    is_final: bool | None = None


class RLMOutputData(FleetUIDataModel):
    output: str
    step: int | None = None
    stream_id: str | None = None
    is_delta: bool | None = None
    is_final: bool | None = None


class AttachmentData(FleetUIDataModel):
    attachment_id: UUID
    filename: str
    phase: str | None = None
    byte_size: int | None = None
    attachment_id_compat: str | None = Field(default=None, alias="attachmentId")
    byte_size_compat: int | None = Field(default=None, alias="byteSize")


class WarningData(FleetUIDataModel):
    message: str
    code: str | None = None


class ArtifactData(FleetUIDataModel):
    artifact_id: UUID
    artifact_kind: str | None = None
    kind: str | None = None
    title: str | None = None
    name: str | None = None
    media_type: str | None = None
    byte_size: int | None = None
    checksum_sha256: str | None = None


class UsageData(FleetUIDataModel):
    usage: _JsonData


class StructuredResultData(FleetUIDataModel):
    schema_id: str
    schema_version: str
    value: Any


# --- Modern Event Models ---


class TurnStartEvent(FleetUIChunkModel):
    type: Literal["turn_start"] = "turn_start"
    run_id: str = Field(alias="runId")
    session_id: str | None = Field(default=None, alias="sessionId")
    delivery: Literal["live", "replay"] = "live"
    trace_id: str | None = Field(default=None, alias="traceId")


class TurnStatusEvent(FleetUIChunkModel):
    type: Literal["turn_status"] = "turn_status"
    phase: str
    status: str | None = None
    message: str | None = None


class StepStartEvent(FleetUIChunkModel):
    type: Literal["step_start"] = "step_start"
    step: int


class StepFinishEvent(FleetUIChunkModel):
    type: Literal["step_finish"] = "step_finish"
    step: int
    duration_ms: int | None = Field(default=None, alias="durationMs")


class ReasoningEvent(FleetUIChunkModel):
    type: Literal["reasoning"] = "reasoning"
    stream_id: str = Field(alias="streamId")
    step: int = 0
    text: str = ""
    delta: str = ""
    final: bool = False


class CodeEvent(FleetUIChunkModel):
    type: Literal["code"] = "code"
    stream_id: str = Field(alias="streamId")
    step: int = 0
    code: str
    is_delta: bool = Field(default=False, alias="isDelta")
    final: bool = True


class OutputEvent(FleetUIChunkModel):
    type: Literal["output"] = "output"
    stream_id: str = Field(alias="streamId")
    step: int = 0
    output: str
    is_delta: bool = Field(default=False, alias="isDelta")
    final: bool = True


class ToolCallEvent(FleetUIChunkModel):
    type: Literal["tool_call"] = "tool_call"
    tool_call_id: str = Field(alias="toolCallId")
    tool_name: str = Field(alias="toolName")
    input: Any = None


class ToolResultEvent(FleetUIChunkModel):
    type: Literal["tool_result"] = "tool_result"
    tool_call_id: str = Field(alias="toolCallId")
    tool_name: str | None = Field(default=None, alias="toolName")
    output: Any = None
    error: str | None = None


class TextEvent(FleetUIChunkModel):
    type: Literal["text"] = "text"
    stream_id: str = Field(default="text", alias="streamId")
    delta: str = ""
    text: str = ""
    final: bool = False
    role: str = "assistant"


class SkillEvent(FleetUIChunkModel):
    type: Literal["skill"] = "skill"
    skill_id: str = Field(alias="skillId")
    name: str | None = None
    phase: str | None = None
    version: str | None = None
    trust: str | None = None
    affordances: list[str] | None = None


class ChildProgressEvent(FleetUIChunkModel):
    child_id: str = Field(alias="childId")
    task_label: str = Field(alias="taskLabel")
    state: Literal["not_started", "running", "completed", "failed", "cancelled", "timed_out"]
    elapsed_ms: int = Field(ge=0, alias="elapsedMs")
    type: Literal["child_progress"] = "child_progress"
    outcome: str | None = Field(default=None, max_length=500)
    cleanup_state: Literal["pending", "complete", "failed", "not_required"] = Field(
        default="not_required", alias="cleanupState"
    )
    parent_run_id: str | None = Field(default=None, alias="parentRunId")
    evidence: list[str] = Field(default_factory=list, max_length=8)
    gaps: list[str] = Field(default_factory=list, max_length=8)
    result_file_count: int = Field(default=0, ge=0, le=16, alias="resultFileCount")
    code_excerpt: str | None = Field(default=None, max_length=800, alias="codeExcerpt")
    output_excerpt: str | None = Field(default=None, max_length=800, alias="outputExcerpt")


class AttachmentEvent(FleetUIChunkModel):
    type: Literal["attachment"] = "attachment"
    attachment_id: str = Field(alias="attachmentId")
    phase: str | None = None
    filename: str | None = None
    byte_size: int | None = Field(default=None, alias="byteSize")


class WarningEventChunk(FleetUIChunkModel):
    type: Literal["warning"] = "warning"
    message: str
    code: str | None = None


class ArtifactEvent(FleetUIChunkModel):
    type: Literal["artifact"] = "artifact"
    artifact_id: str = Field(alias="artifactId")
    artifact_kind: str | None = Field(default=None, alias="artifactKind")
    title: str | None = None
    media_type: str | None = Field(default=None, alias="mediaType")
    byte_size: int | None = Field(default=None, alias="byteSize")
    checksum_sha256: str | None = Field(default=None, alias="checksumSha256")


class UsageEvent(FleetUIChunkModel):
    type: Literal["usage"] = "usage"
    iterations: int = 0
    duration_ms: int | None = Field(default=None, alias="durationMs")
    usage: dict[str, Any] = Field(default_factory=dict)


class StructuredResultEvent(FleetUIChunkModel):
    type: Literal["structured_result"] = "structured_result"
    schema_id: str = Field(alias="schemaId")
    schema_version: str = Field(alias="schemaVersion")
    value: Any = None


class TurnFinishEvent(FleetUIChunkModel):
    type: Literal["turn_finish"] = "turn_finish"
    finish_reason: str = Field(default="stop", alias="finishReason")
    status: str = "completed"
    checkpoint_version: int | None = Field(default=None, alias="checkpointVersion")
    duration_ms: int | None = Field(default=None, alias="durationMs")
    trace_id: str | None = Field(default=None, alias="traceId")


class TurnCancelledEvent(FleetUIChunkModel):
    type: Literal["turn_cancelled"] = "turn_cancelled"
    reason: str = "Turn cancelled"
    duration_ms: int | None = Field(default=None, alias="durationMs")


class TurnErrorEvent(FleetUIChunkModel):
    type: Literal["turn_error"] = "turn_error"
    message: str
    code: str = "execution_failed"
    duration_ms: int | None = Field(default=None, alias="durationMs")


# --- Legacy Chunk Models ---


class StartChunk(FleetUIChunkModel):
    type: Literal["start"] = "start"
    message_id: UUID = Field(alias="messageId")
    message_metadata: _JsonData = Field(alias="messageMetadata")


class StartStepChunk(FleetUIChunkModel):
    type: Literal["start-step"] = "start-step"


class FinishStepChunk(FleetUIChunkModel):
    type: Literal["finish-step"] = "finish-step"


class ReasoningStartChunk(FleetUIChunkModel):
    type: Literal["reasoning-start"] = "reasoning-start"
    id: str = Field(min_length=1)


class ReasoningDeltaChunk(FleetUIChunkModel):
    type: Literal["reasoning-delta"] = "reasoning-delta"
    id: str = Field(min_length=1)
    delta: str


class ReasoningEndChunk(FleetUIChunkModel):
    type: Literal["reasoning-end"] = "reasoning-end"
    id: str = Field(min_length=1)


class TextStartChunk(FleetUIChunkModel):
    type: Literal["text-start"] = "text-start"
    id: str = Field(min_length=1)


class TextDeltaChunk(FleetUIChunkModel):
    type: Literal["text-delta"] = "text-delta"
    id: str = Field(min_length=1)
    delta: str


class TextEndChunk(FleetUIChunkModel):
    type: Literal["text-end"] = "text-end"
    id: str = Field(min_length=1)


class ToolInputAvailableChunk(FleetUIChunkModel):
    type: Literal["tool-input-available"] = "tool-input-available"
    tool_call_id: str = Field(min_length=1, alias="toolCallId")
    tool_name: str = Field(min_length=1, alias="toolName")
    input: Any
    dynamic: bool | None = None
    provider_executed: bool | None = Field(default=None, alias="providerExecuted")


class ToolOutputAvailableChunk(FleetUIChunkModel):
    type: Literal["tool-output-available"] = "tool-output-available"
    tool_call_id: str = Field(min_length=1, alias="toolCallId")
    output: Any
    dynamic: bool | None = None
    provider_executed: bool | None = Field(default=None, alias="providerExecuted")


class ToolOutputErrorChunk(FleetUIChunkModel):
    type: Literal["tool-output-error"] = "tool-output-error"
    tool_call_id: str = Field(min_length=1, alias="toolCallId")
    error_text: str = Field(min_length=1, alias="errorText")
    dynamic: bool | None = None
    provider_executed: bool | None = Field(default=None, alias="providerExecuted")


class FinishChunk(FleetUIChunkModel):
    type: Literal["finish"] = "finish"
    finish_reason: Literal["stop", "error"] = Field(alias="finishReason")
    message_metadata: _JsonData | None = Field(default=None, alias="messageMetadata")


class AbortChunk(FleetUIChunkModel):
    type: Literal["abort"] = "abort"
    reason: str


class ErrorChunk(FleetUIChunkModel):
    type: Literal["error"] = "error"
    error_text: str = Field(min_length=1, alias="errorText")


class DataStatusChunk(FleetUIChunkModel):
    type: Literal["data-status"] = "data-status"
    id: str | None = None
    data: StatusData
    transient: bool | None = None


class DataChildProgressChunk(FleetUIChunkModel):
    type: Literal["data-child-progress"] = "data-child-progress"
    id: str | None = None
    data: ChildProgressData
    transient: bool | None = None


class DataSkillChunk(FleetUIChunkModel):
    type: Literal["data-skill"] = "data-skill"
    id: str | None = None
    data: SkillData
    transient: bool | None = None


class DataRLMCodeChunk(FleetUIChunkModel):
    type: Literal["data-rlm-code"] = "data-rlm-code"
    id: str | None = None
    data: RLMCodeData
    transient: bool | None = None


class DataRLMOutputChunk(FleetUIChunkModel):
    type: Literal["data-rlm-output"] = "data-rlm-output"
    id: str | None = None
    data: RLMOutputData
    transient: bool | None = None


class DataAttachmentChunk(FleetUIChunkModel):
    type: Literal["data-attachment"] = "data-attachment"
    id: str | None = None
    data: AttachmentData
    transient: bool | None = None


class DataWarningChunk(FleetUIChunkModel):
    type: Literal["data-warning"] = "data-warning"
    id: str | None = None
    data: WarningData
    transient: bool | None = None


class DataArtifactChunk(FleetUIChunkModel):
    type: Literal["data-artifact"] = "data-artifact"
    id: str | None = None
    data: ArtifactData
    transient: bool | None = None


class DataUsageChunk(FleetUIChunkModel):
    type: Literal["data-usage"] = "data-usage"
    id: str | None = None
    data: UsageData
    transient: bool | None = None


class DataStructuredResultChunk(FleetUIChunkModel):
    type: Literal["data-structured-result"] = "data-structured-result"
    id: str | None = None
    data: StructuredResultData
    transient: bool | None = None


# --- Combined Unions ---

FleetStreamEvent = Annotated[
    TurnStartEvent
    | TurnStatusEvent
    | StepStartEvent
    | StepFinishEvent
    | ReasoningEvent
    | CodeEvent
    | OutputEvent
    | ToolCallEvent
    | ToolResultEvent
    | TextEvent
    | SkillEvent
    | ChildProgressEvent
    | AttachmentEvent
    | WarningEventChunk
    | ArtifactEvent
    | UsageEvent
    | StructuredResultEvent
    | TurnFinishEvent
    | TurnCancelledEvent
    | TurnErrorEvent,
    Field(discriminator="type"),
]

FleetUIMessageChunk = FleetStreamEvent

FleetUIMessageChunkAdapter: TypeAdapter[FleetUIMessageChunk] = TypeAdapter(FleetUIMessageChunk)


def fleet_ui_chunk_payload(value: object) -> dict[str, Any]:
    """Validate one live transport frame and return its canonical JSON payload."""
    payload = to_plain_json(value)
    validated = FleetUIMessageChunkAdapter.validate_python(payload, strict=False)
    dumped = FleetUIMessageChunkAdapter.dump_python(
        validated,
        mode="json",
        by_alias=True,
        exclude_none=True,
    )
    if not isinstance(dumped, dict):
        raise TypeError("Fleet UI chunks must serialize to JSON objects")
    return dumped


def fleet_ui_message_chunk_json_schema() -> dict[str, Any]:
    """Return the typed discriminated schema used by OpenAPI generation."""
    schema = FleetUIMessageChunkAdapter.json_schema(mode="serialization")
    return schema


__all__ = [
    "AbortChunk",
    "ArtifactData",
    "ArtifactEvent",
    "AttachmentData",
    "AttachmentEvent",
    "ChildProgressData",
    "ChildProgressEvent",
    "CodeEvent",
    "DataArtifactChunk",
    "DataAttachmentChunk",
    "DataChildProgressChunk",
    "DataRLMCodeChunk",
    "DataRLMOutputChunk",
    "DataSkillChunk",
    "DataStatusChunk",
    "DataStructuredResultChunk",
    "DataUsageChunk",
    "DataWarningChunk",
    "ErrorChunk",
    "FinishChunk",
    "FinishStepChunk",
    "FleetStreamEvent",
    "FleetUIChunkModel",
    "FleetUIDataModel",
    "FleetUIMessageChunk",
    "FleetUIMessageChunkAdapter",
    "OutputEvent",
    "RLMCodeData",
    "RLMOutputData",
    "ReasoningDeltaChunk",
    "ReasoningEndChunk",
    "ReasoningEvent",
    "ReasoningStartChunk",
    "SkillData",
    "SkillEvent",
    "StartChunk",
    "StartStepChunk",
    "StatusData",
    "StepFinishEvent",
    "StepStartEvent",
    "StructuredResultData",
    "StructuredResultEvent",
    "TextDeltaChunk",
    "TextEndChunk",
    "TextEvent",
    "TextStartChunk",
    "ToolCallEvent",
    "ToolInputAvailableChunk",
    "ToolOutputAvailableChunk",
    "ToolOutputErrorChunk",
    "ToolResultEvent",
    "TurnCancelledEvent",
    "TurnErrorEvent",
    "TurnFinishEvent",
    "TurnStartEvent",
    "TurnStatusEvent",
    "UsageData",
    "UsageEvent",
    "WarningData",
    "WarningEventChunk",
    "fleet_ui_chunk_payload",
    "fleet_ui_message_chunk_json_schema",
]
