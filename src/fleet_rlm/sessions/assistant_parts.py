"""Canonical Pydantic vocabulary for durable assistant Result content.

These models are the authoritative semantic contracts for committed assistant
parts. `CommittedTurn` remains the small runtime aggregate; this module owns
wire-shape validation and conversion so reload projection, persistence codecs,
and future transport adapters share one canonical part vocabulary without
reusing durable models as live SSE transport chunks.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Annotated, Any, Literal, assert_never, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

from fleet_rlm.rlm.result import RLMUsage
from fleet_rlm.sessions.committed_turn import (
    ArtifactPart,
    AttachmentPart,
    CodePart,
    CommittedPart,
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
    _freeze_json,
)


class AssistantPartModel(BaseModel):
    """Strict base for every durable assistant semantic part."""

    model_config = ConfigDict(extra="forbid", strict=True)


def _require_nonblank(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} is required")
    return value


def _validated_json_value(value: object, *, path: str) -> Any:
    # The canonical Pydantic boundary admits exactly the DTO payloads the
    # defensive runtime aggregate already accepts, then returns normal JSON
    # containers for inexpensive round-trip serialization.
    frozen = _freeze_json(value, path=path)
    if isinstance(frozen, Mapping):
        return {key: _validated_json_value(item, path=path) for key, item in frozen.items()}
    if isinstance(frozen, tuple):
        return [_validated_json_value(item, path=path) for item in frozen]
    return frozen


class StepAssistantPart(AssistantPartModel):
    type: Literal["step"] = "step"
    state: Literal["started", "finished"]
    step: int = Field(ge=1)
    duration_ms: int | None = Field(default=None, ge=0)


class ReasoningAssistantPart(AssistantPartModel):
    type: Literal["reasoning"] = "reasoning"
    text: str
    step: int | None = Field(default=None, ge=1)


class CodeAssistantPart(AssistantPartModel):
    type: Literal["code"] = "code"
    code: str
    step: int | None = Field(default=None, ge=1)


class OutputAssistantPart(AssistantPartModel):
    type: Literal["output"] = "output"
    output: str
    step: int | None = Field(default=None, ge=1)


class ToolCallAssistantPart(AssistantPartModel):
    type: Literal["tool_call"] = "tool_call"
    tool_call_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    state: Literal["completed", "failed"]
    input: Any
    output: Any = None
    error: str | None = None

    @field_validator("tool_call_id", "tool_name")
    @classmethod
    def _required_identity(cls, value: str) -> str:
        return _require_nonblank(value, "tool call identity")

    @field_validator("input")
    @classmethod
    def _required_json_input(cls, value: Any) -> Any:
        return _validated_json_value(value, path="tool_call.input")

    @field_validator("output")
    @classmethod
    def _optional_json_output(cls, value: Any) -> Any:
        if value is None:
            return None
        return _validated_json_value(value, path="tool_call.output")

    @model_validator(mode="after")
    def _validate_terminal_state(self) -> ToolCallAssistantPart:
        if self.state == "completed" and self.error is not None:
            raise ValueError("completed tool calls cannot contain an error")
        if self.state == "failed" and (self.error is None or not self.error.strip()):
            raise ValueError("failed tool calls require a non-blank error")
        return self


class SkillAssistantPart(AssistantPartModel):
    type: Literal["skill"] = "skill"
    skill_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    phase: Literal["activated", "loaded"]
    version: str | None = None
    trust: str | None = None
    affordances: list[str] = Field(default_factory=list)

    @field_validator("skill_id", "name")
    @classmethod
    def _required_identity(cls, value: str) -> str:
        return _require_nonblank(value, "skill identity")

    @model_validator(mode="after")
    def _validate_phase_semantics(self) -> SkillAssistantPart:
        if self.phase == "activated" and (self.trust is None or not self.trust.strip()):
            raise ValueError("activated skills require non-blank trust metadata")
        if self.phase == "loaded" and (self.trust is not None or bool(self.affordances)):
            raise ValueError("loaded skills cannot contain activation metadata")
        return self


class AttachmentAssistantPart(AssistantPartModel):
    type: Literal["attachment"] = "attachment"
    attachment_id: UUID
    phase: Literal["selected", "read"]
    filename: str | None = None
    byte_size: int | None = Field(default=None, ge=0)


class WarningAssistantPart(AssistantPartModel):
    type: Literal["warning"] = "warning"
    message: str = Field(min_length=1)
    code: str | None = None

    @field_validator("message")
    @classmethod
    def _required_message(cls, value: str) -> str:
        return _require_nonblank(value, "warning message")


class StatusAssistantPart(AssistantPartModel):
    type: Literal["status"] = "status"
    phase: str = Field(min_length=1)
    status: str = Field(min_length=1)
    message: str | None = None

    @field_validator("phase", "status")
    @classmethod
    def _required_state(cls, value: str) -> str:
        return _require_nonblank(value, "status semantics")


class ArtifactAssistantPart(AssistantPartModel):
    type: Literal["artifact"] = "artifact"
    artifact_id: UUID
    kind: Literal["text", "markdown", "json"]
    title: str | None
    media_type: str = Field(min_length=1)
    byte_size: int = Field(ge=0)
    checksum_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("media_type")
    @classmethod
    def _required_media_type(cls, value: str) -> str:
        return _require_nonblank(value, "artifact media_type")

    @field_validator("checksum_sha256", mode="before")
    @classmethod
    def _normalize_checksum(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("checksum_sha256 must be a string")
        candidate = value.lower()
        if len(candidate) != 64 or any(character not in "0123456789abcdef" for character in candidate):
            raise ValueError("checksum_sha256 must contain 64 hexadecimal characters")
        return candidate


class UsageAssistantPart(AssistantPartModel):
    type: Literal["usage"] = "usage"
    value: Mapping[str, Any]

    @field_validator("value")
    @classmethod
    def _validate_usage(cls, value: Mapping[str, Any]) -> Mapping[str, Any]:
        try:
            from fleet_rlm.rlm.result import validate_rlm_usage

            usage = validate_rlm_usage(value)
            return _validated_json_value(usage, path="usage.value")
        except ValueError as exc:
            raise ValueError(str(exc)) from exc


class StructuredResultAssistantPart(AssistantPartModel):
    type: Literal["structured_result"] = "structured_result"
    schema_id: str = Field(min_length=1)
    schema_version: str = Field(min_length=1)
    value: Any

    @field_validator("schema_id", "schema_version")
    @classmethod
    def _required_schema_identity(cls, value: str) -> str:
        return _require_nonblank(value, "structured result schema identity")

    @field_validator("value")
    @classmethod
    def _validated_result_value(cls, value: Any) -> Any:
        return _validated_json_value(value, path="structured_result.value")


class TextAssistantPart(AssistantPartModel):
    type: Literal["text"] = "text"
    text: str = Field(min_length=1)

    @field_validator("text")
    @classmethod
    def _validated_final_text(cls, value: str) -> str:
        return _require_nonblank(value, "final text")


AssistantPart = Annotated[
    StepAssistantPart
    | ReasoningAssistantPart
    | CodeAssistantPart
    | OutputAssistantPart
    | ToolCallAssistantPart
    | SkillAssistantPart
    | AttachmentAssistantPart
    | WarningAssistantPart
    | StatusAssistantPart
    | ArtifactAssistantPart
    | UsageAssistantPart
    | StructuredResultAssistantPart
    | TextAssistantPart,
    Field(discriminator="type"),
]

_ASSISTANT_PART_ADAPTER: TypeAdapter[AssistantPart] = TypeAdapter(AssistantPart)

AssistantPartModelUnion = (
    StepAssistantPart,
    ReasoningAssistantPart,
    CodeAssistantPart,
    OutputAssistantPart,
    ToolCallAssistantPart,
    SkillAssistantPart,
    AttachmentAssistantPart,
    WarningAssistantPart,
    StatusAssistantPart,
    ArtifactAssistantPart,
    UsageAssistantPart,
    StructuredResultAssistantPart,
    TextAssistantPart,
)


def _plain_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain_json(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_plain_json(item) for item in value]
    return value


def assistant_part_to_model(part: CommittedPart) -> AssistantPart:
    """Project a runtime committed part into its canonical Pydantic contract."""
    if isinstance(part, StepPart):
        return StepAssistantPart(state=part.state, step=part.step, duration_ms=part.duration_ms)
    if isinstance(part, ReasoningPart):
        return ReasoningAssistantPart(text=part.text, step=part.step)
    if isinstance(part, CodePart):
        return CodeAssistantPart(code=part.code, step=part.step)
    if isinstance(part, OutputPart):
        return OutputAssistantPart(output=part.output, step=part.step)
    if isinstance(part, ToolCallPart):
        return ToolCallAssistantPart(
            tool_call_id=part.tool_call_id,
            tool_name=part.tool_name,
            state=part.state,
            input=_plain_json(part.input),
            output=_plain_json(part.output),
            error=part.error,
        )
    if isinstance(part, SkillPart):
        return SkillAssistantPart(
            skill_id=part.skill_id,
            name=part.name,
            phase=part.phase,
            version=part.version,
            trust=part.trust,
            affordances=list(part.affordances),
        )
    if isinstance(part, AttachmentPart):
        return AttachmentAssistantPart(
            attachment_id=part.attachment_id,
            phase=part.phase,
            filename=part.filename,
            byte_size=part.byte_size,
        )
    if isinstance(part, WarningPart):
        return WarningAssistantPart(message=part.message, code=part.code)
    if isinstance(part, StatusPart):
        return StatusAssistantPart(phase=part.phase, status=part.status, message=part.message)
    if isinstance(part, ArtifactPart):
        return ArtifactAssistantPart(
            artifact_id=part.artifact_id,
            kind=part.kind,
            title=part.title,
            media_type=part.media_type,
            byte_size=part.byte_size,
            checksum_sha256=part.checksum_sha256,
        )
    if isinstance(part, UsagePart):
        value = dict(part.value)
        from fleet_rlm.rlm.result import validate_rlm_usage

        return UsageAssistantPart(value=validate_rlm_usage(value))
    if isinstance(part, StructuredResultPart):
        return StructuredResultAssistantPart(
            schema_id=part.schema_id,
            schema_version=part.schema_version,
            value=_plain_json(part.value),
        )
    if isinstance(part, TextPart):
        return TextAssistantPart(text=part.text)
    assert_never(part)


def assistant_part_from_model(part: AssistantPart) -> CommittedPart:
    """Convert a validated canonical part into the runtime committed aggregate."""
    if isinstance(part, StepAssistantPart):
        return StepPart(state=part.state, step=part.step, duration_ms=part.duration_ms)
    if isinstance(part, ReasoningAssistantPart):
        return ReasoningPart(text=part.text, step=part.step)
    if isinstance(part, CodeAssistantPart):
        return CodePart(code=part.code, step=part.step)
    if isinstance(part, OutputAssistantPart):
        return OutputPart(output=part.output, step=part.step)
    if isinstance(part, ToolCallAssistantPart):
        return ToolCallPart(
            tool_call_id=part.tool_call_id,
            tool_name=part.tool_name,
            state=part.state,
            input=_plain_json(part.input),
            output=_plain_json(part.output),
            error=part.error,
        )
    if isinstance(part, SkillAssistantPart):
        return SkillPart(
            skill_id=part.skill_id,
            name=part.name,
            phase=part.phase,
            version=part.version,
            trust=part.trust,
            affordances=tuple(part.affordances),
        )
    if isinstance(part, AttachmentAssistantPart):
        return AttachmentPart(
            attachment_id=part.attachment_id,
            phase=part.phase,
            filename=part.filename,
            byte_size=part.byte_size,
        )
    if isinstance(part, WarningAssistantPart):
        return WarningPart(message=part.message, code=part.code)
    if isinstance(part, StatusAssistantPart):
        return StatusPart(phase=part.phase, status=part.status, message=part.message)
    if isinstance(part, ArtifactAssistantPart):
        return ArtifactPart(
            artifact_id=part.artifact_id,
            kind=part.kind,
            title=part.title,
            media_type=part.media_type,
            byte_size=part.byte_size,
            checksum_sha256=part.checksum_sha256,
        )
    if isinstance(part, UsageAssistantPart):
        return UsagePart(value=cast(RLMUsage, dict(part.value)))
    if isinstance(part, StructuredResultAssistantPart):
        return StructuredResultPart(
            schema_id=part.schema_id,
            schema_version=part.schema_version,
            value=part.value,
        )
    if isinstance(part, TextAssistantPart):
        return TextPart(text=part.text)
    assert_never(part)


def assistant_part_payload(part: CommittedPart) -> dict[str, Any]:
    """Serialize one runtime part through the canonical discriminated contract."""
    model = assistant_part_to_model(part)
    return model.model_dump(mode="json")


def assistant_part_from_payload(payload: object) -> CommittedPart:
    """Validate a durable part payload and convert it to the runtime aggregate."""
    return assistant_part_from_model(_ASSISTANT_PART_ADAPTER.validate_python(payload, strict=False))


__all__ = [
    "ArtifactAssistantPart",
    "AssistantPart",
    "AssistantPartModel",
    "AssistantPartModelUnion",
    "AttachmentAssistantPart",
    "CodeAssistantPart",
    "OutputAssistantPart",
    "ReasoningAssistantPart",
    "SkillAssistantPart",
    "StatusAssistantPart",
    "StepAssistantPart",
    "StructuredResultAssistantPart",
    "TextAssistantPart",
    "ToolCallAssistantPart",
    "UsageAssistantPart",
    "WarningAssistantPart",
    "assistant_part_from_model",
    "assistant_part_from_payload",
    "assistant_part_payload",
    "assistant_part_to_model",
]
