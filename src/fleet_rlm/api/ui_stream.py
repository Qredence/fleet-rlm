"""Typed AI SDK UI transport chunks for live Fleet SSE.

These discriminated models are the bounded live transport contract. They are
intentionally separate from the durable `AssistantPart` vocabulary: durable
parts describe one committed assistant Result, while these chunks describe the
ordered live/replay SSE frames consumed by the TUI.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from fleet_rlm.api.json_util import to_plain_json

_JsonData = dict[str, Any]


class FleetUIChunkModel(BaseModel):
    """Strict base for one live transport chunk frame."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class FleetUIDataModel(BaseModel):
    """Bounded data payload model with declared compatibility fields."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)


class StatusData(FleetUIDataModel):
    phase: str
    status: str | None = None
    detail: str | None = None
    message: str | None = None


class SkillData(FleetUIDataModel):
    skill_id: str
    name: str
    version: str
    phase: Literal["activated", "loaded"] | None = None
    trust: str | None = None
    affordances: list[str] | None = None
    skill_id_compat: str | None = Field(default=None, alias="skillId")


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
    media_type_compat: str | None = Field(default=None, alias="mediaType")
    byte_size: int | None = None
    byte_size_compat: int | None = Field(default=None, alias="byteSize")
    checksum_sha256: str | None = None
    checksum_sha256_compat: str | None = Field(default=None, alias="checksumSha256")
    artifact_id_compat: str | None = Field(default=None, alias="artifactId")


class UsageData(FleetUIDataModel):
    usage: _JsonData


class StructuredResultData(FleetUIDataModel):
    schema_id: str
    schema_version: str
    value: Any
    schema_id_compat: str | None = Field(default=None, alias="schemaId")
    schema_version_compat: str | None = Field(default=None, alias="schemaVersion")


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


FleetUIMessageChunk = Annotated[
    StartChunk
    | StartStepChunk
    | FinishStepChunk
    | ReasoningStartChunk
    | ReasoningDeltaChunk
    | ReasoningEndChunk
    | DataStatusChunk
    | DataSkillChunk
    | DataRLMCodeChunk
    | DataRLMOutputChunk
    | ToolInputAvailableChunk
    | ToolOutputAvailableChunk
    | ToolOutputErrorChunk
    | DataAttachmentChunk
    | DataWarningChunk
    | DataArtifactChunk
    | DataUsageChunk
    | DataStructuredResultChunk
    | TextStartChunk
    | TextDeltaChunk
    | TextEndChunk
    | FinishChunk
    | AbortChunk
    | ErrorChunk,
    Field(discriminator="type"),
]

FleetUIMessageChunkAdapter: TypeAdapter[FleetUIMessageChunk] = TypeAdapter(FleetUIMessageChunk)


def fleet_ui_chunk_payload(value: object) -> dict[str, Any]:
    """Validate one live transport frame and return its exact JSON payload."""
    payload = to_plain_json(value)
    FleetUIMessageChunkAdapter.validate_python(payload, strict=False)
    return payload


def fleet_ui_message_chunk_json_schema() -> dict[str, Any]:
    """Return the typed discriminated schema used by OpenAPI generation."""
    return FleetUIMessageChunkAdapter.json_schema(mode="serialization")


__all__ = [
    "AbortChunk",
    "ArtifactData",
    "AttachmentData",
    "DataArtifactChunk",
    "DataAttachmentChunk",
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
    "FleetUIChunkModel",
    "FleetUIMessageChunk",
    "FleetUIMessageChunkAdapter",
    "RLMCodeData",
    "RLMOutputData",
    "ReasoningDeltaChunk",
    "ReasoningEndChunk",
    "ReasoningStartChunk",
    "SkillData",
    "StartChunk",
    "StartStepChunk",
    "StatusData",
    "StructuredResultData",
    "TextDeltaChunk",
    "TextEndChunk",
    "TextStartChunk",
    "ToolInputAvailableChunk",
    "ToolOutputAvailableChunk",
    "ToolOutputErrorChunk",
    "UsageData",
    "WarningData",
    "fleet_ui_chunk_payload",
    "fleet_ui_message_chunk_json_schema",
]
