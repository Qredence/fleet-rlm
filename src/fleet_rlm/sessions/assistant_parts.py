"""Canonical Pydantic vocabulary for durable assistant Result content.

These models are the authoritative semantic contracts for committed assistant
parts. `CommittedTurn` remains the small runtime aggregate; this module owns
wire-shape validation and serialization.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

from fleet_rlm.rlm.result import validate_rlm_usage


class CommittedTurnValidationError(ValueError):
    """Raised when durable committed data is unknown, malformed, or noncanonical."""


class AssistantPartModel(BaseModel):
    """Strict base for every durable assistant semantic part."""

    model_config = ConfigDict(extra="forbid", strict=False, frozen=True)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        if args:
            field_names = [f for f in type(self).model_fields if f != "type"]
            for name, arg in zip(field_names, args, strict=False):
                kwargs[name] = arg
        try:
            super().__init__(**kwargs)
        except (ValidationError, ValueError, TypeError) as exc:
            raise CommittedTurnValidationError(str(exc)) from exc


def _require_nonblank(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} is required")
    return value


class StepPart(AssistantPartModel):
    type: Literal["step"] = "step"
    state: Literal["started", "finished"] = "started"
    step: int = Field(default=1, ge=1)
    duration_ms: int | None = Field(default=None, ge=0)


class ReasoningPart(AssistantPartModel):
    type: Literal["reasoning"] = "reasoning"
    text: str
    step: int | None = Field(default=None, ge=1)


class CodePart(AssistantPartModel):
    type: Literal["code"] = "code"
    code: str
    step: int | None = Field(default=None, ge=1)


class OutputPart(AssistantPartModel):
    type: Literal["output"] = "output"
    output: str
    step: int | None = Field(default=None, ge=1)


class ToolCallPart(AssistantPartModel):
    type: Literal["tool_call"] = "tool_call"
    tool_call_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    state: Literal["completed", "failed"]
    input: Any
    output: Any = None
    error: str | None = None

    @field_validator("tool_call_id", "tool_name")
    @classmethod
    def _validate_identity(cls, value: str) -> str:
        return _require_nonblank(value, "tool call identity")

    @model_validator(mode="after")
    def _validate_state(self) -> ToolCallPart:
        if self.state == "completed" and self.error is not None:
            raise ValueError("completed tool calls cannot contain an error")
        if self.state == "failed" and (self.error is None or not self.error.strip()):
            raise ValueError("failed tool calls require a non-blank error")
        return self


class SkillPart(AssistantPartModel):
    type: Literal["skill"] = "skill"
    skill_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    phase: Literal["activated", "loaded"]
    version: str | None = None
    trust: str | None = None
    affordances: tuple[str, ...] = Field(default=())

    @field_validator("skill_id", "name")
    @classmethod
    def _validate_identity(cls, value: str) -> str:
        return _require_nonblank(value, "skill identity")

    @model_validator(mode="after")
    def _validate_phase(self) -> SkillPart:
        if self.phase == "activated" and (self.trust is None or not self.trust.strip()):
            raise ValueError("activated skills require non-blank trust metadata")
        if self.phase == "loaded" and (self.trust is not None or bool(self.affordances)):
            raise ValueError("loaded skills cannot contain activation metadata")
        return self


class AttachmentPart(AssistantPartModel):
    type: Literal["attachment"] = "attachment"
    attachment_id: UUID
    phase: Literal["selected", "read"]
    filename: str | None = None
    byte_size: int | None = Field(default=None, ge=0)


class WarningPart(AssistantPartModel):
    type: Literal["warning"] = "warning"
    message: str = Field(min_length=1)
    code: str | None = None

    @field_validator("message")
    @classmethod
    def _validate_message(cls, value: str) -> str:
        return _require_nonblank(value, "warning message")


class StatusPart(AssistantPartModel):
    type: Literal["status"] = "status"
    phase: str = Field(min_length=1)
    status: str = Field(min_length=1)
    message: str | None = None

    @field_validator("phase", "status")
    @classmethod
    def _validate_state(cls, value: str) -> str:
        return _require_nonblank(value, "status semantics")


class ChildProgressPart(AssistantPartModel):
    type: Literal["child_progress"] = "child_progress"
    child_id: str = Field(min_length=1, max_length=128)
    task_label: str = Field(min_length=1, max_length=240)
    state: Literal["not_started", "running", "completed", "failed", "cancelled", "timed_out"]
    elapsed_ms: int = Field(ge=0)
    outcome: str | None = Field(default=None, max_length=500)
    cleanup_state: Literal["pending", "complete", "failed", "not_required"] = "not_required"
    parent_run_id: str | None = Field(default=None, min_length=1, max_length=128)
    evidence: tuple[str, ...] = Field(default=(), max_length=8)
    gaps: tuple[str, ...] = Field(default=(), max_length=8)
    result_file_count: int = Field(default=0, ge=0, le=16)
    code_excerpt: str | None = Field(default=None, max_length=800)
    output_excerpt: str | None = Field(default=None, max_length=800)

    @field_validator("parent_run_id")
    @classmethod
    def _valid_parent_run_id(cls, value: str | None) -> str | None:
        return None if value is None else _require_nonblank(value, "parent_run_id")

    @field_validator("evidence", "gaps")
    @classmethod
    def _bounded_details(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(len(item) > 200 for item in value):
            raise ValueError("child progress detail exceeds 200 characters")
        return value


class ArtifactPart(AssistantPartModel):
    type: Literal["artifact"] = "artifact"
    artifact_id: UUID
    kind: Literal["text", "markdown", "json"]
    title: str | None = None
    media_type: str = Field(min_length=1)
    byte_size: int = Field(ge=0)
    checksum_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("media_type")
    @classmethod
    def _validate_media_type(cls, value: str) -> str:
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


class UsagePart(AssistantPartModel):
    type: Literal["usage"] = "usage"
    value: Mapping[str, Any]

    @field_validator("value")
    @classmethod
    def _validate_usage(cls, value: Mapping[str, Any]) -> Mapping[str, Any]:
        try:
            return validate_rlm_usage(value)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc


class StructuredResultPart(AssistantPartModel):
    type: Literal["structured_result"] = "structured_result"
    schema_id: str = Field(min_length=1)
    schema_version: str = Field(min_length=1)
    value: Any

    @field_validator("schema_id", "schema_version")
    @classmethod
    def _validate_schema_identity(cls, value: str) -> str:
        return _require_nonblank(value, "structured result schema identity")


class TextPart(AssistantPartModel):
    type: Literal["text"] = "text"
    text: str

    @field_validator("text", mode="before")
    @classmethod
    def _validate_final_text(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("a committed Turn requires non-blank final text")
        return value


# Canonical vocabulary aliases
StepAssistantPart = StepPart
ReasoningAssistantPart = ReasoningPart
CodeAssistantPart = CodePart
OutputAssistantPart = OutputPart
ToolCallAssistantPart = ToolCallPart
SkillAssistantPart = SkillPart
AttachmentAssistantPart = AttachmentPart
WarningAssistantPart = WarningPart
StatusAssistantPart = StatusPart
ChildProgressAssistantPart = ChildProgressPart
ArtifactAssistantPart = ArtifactPart
UsageAssistantPart = UsagePart
StructuredResultAssistantPart = StructuredResultPart
TextAssistantPart = TextPart

CommittedPart = (
    StepPart
    | ReasoningPart
    | CodePart
    | OutputPart
    | ToolCallPart
    | SkillPart
    | AttachmentPart
    | WarningPart
    | StatusPart
    | ChildProgressPart
    | ArtifactPart
    | UsagePart
    | StructuredResultPart
    | TextPart
)

AssistantPart = Annotated[CommittedPart, Field(discriminator="type")]

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
    ChildProgressAssistantPart,
    ArtifactAssistantPart,
    UsageAssistantPart,
    StructuredResultAssistantPart,
    TextAssistantPart,
)

_ASSISTANT_PART_ADAPTER: TypeAdapter[AssistantPart] = TypeAdapter(AssistantPart)


def assistant_part_to_model(part: CommittedPart) -> AssistantPart:
    """Project a runtime committed part into its canonical Pydantic contract."""
    return part


def assistant_part_from_model(part: AssistantPart) -> CommittedPart:
    """Convert a validated canonical part into the runtime committed aggregate."""
    return part


def assistant_part_payload(part: CommittedPart) -> dict[str, Any]:
    """Serialize one runtime part through the canonical discriminated contract."""
    return part.model_dump(mode="json")


def assistant_part_from_payload(payload: object) -> CommittedPart:
    """Validate a durable part payload and convert it to the runtime aggregate."""
    try:
        return _ASSISTANT_PART_ADAPTER.validate_python(payload, strict=False)
    except (ValidationError, ValueError) as exc:
        message = str(exc)
        if "final text" in message:
            message = "a committed Turn requires non-blank final text"
        raise CommittedTurnValidationError(message) from exc


__all__ = [
    "ArtifactAssistantPart",
    "ArtifactPart",
    "AssistantPart",
    "AssistantPartModel",
    "AssistantPartModelUnion",
    "AttachmentAssistantPart",
    "AttachmentPart",
    "ChildProgressAssistantPart",
    "ChildProgressPart",
    "CodeAssistantPart",
    "CodePart",
    "CommittedPart",
    "CommittedTurnValidationError",
    "OutputAssistantPart",
    "OutputPart",
    "ReasoningAssistantPart",
    "ReasoningPart",
    "SkillAssistantPart",
    "SkillPart",
    "StatusAssistantPart",
    "StatusPart",
    "StepAssistantPart",
    "StepPart",
    "StructuredResultAssistantPart",
    "StructuredResultPart",
    "TextAssistantPart",
    "TextPart",
    "ToolCallAssistantPart",
    "ToolCallPart",
    "UsageAssistantPart",
    "UsagePart",
    "WarningAssistantPart",
    "WarningPart",
    "assistant_part_from_model",
    "assistant_part_from_payload",
    "assistant_part_payload",
    "assistant_part_to_model",
]
