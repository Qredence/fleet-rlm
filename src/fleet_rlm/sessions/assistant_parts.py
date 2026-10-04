"""Canonical Pydantic vocabulary for durable assistant Result content.

These models are the authoritative semantic contracts for committed assistant
parts. `CommittedTurn` remains the small runtime aggregate; this module owns
wire-shape validation and conversion so reload projection, persistence codecs,
and future transport adapters share one canonical part vocabulary without
reusing durable models as live SSE transport chunks.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Annotated, Any, Literal, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

from fleet_rlm.rlm.result import RLMUsage, validate_rlm_usage
from fleet_rlm.sessions.committed_turn import (
    ArtifactPart,
    AttachmentPart,
    ChildProgressPart,
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


class ChildProgressAssistantPart(AssistantPartModel):
    type: Literal["child_progress"] = "child_progress"
    child_id: str = Field(min_length=1, max_length=128)
    task_label: str = Field(min_length=1, max_length=240)
    state: Literal["not_started", "running", "completed", "failed", "cancelled", "timed_out"]
    elapsed_ms: int = Field(ge=0)
    outcome: str | None = Field(default=None, max_length=500)
    evidence: tuple[str, ...] = Field(default=(), max_length=8)
    gaps: tuple[str, ...] = Field(default=(), max_length=8)
    result_file_count: int = Field(default=0, ge=0, le=16)
    code_excerpt: str | None = Field(default=None, max_length=800)
    output_excerpt: str | None = Field(default=None, max_length=800)
    cleanup_state: Literal["pending", "complete", "failed", "not_required"] = "not_required"
    parent_run_id: str | None = Field(default=None, min_length=1, max_length=128)

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
    | ChildProgressAssistantPart
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
    ChildProgressAssistantPart,
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


_COMMITTED_TO_MODEL: dict[type[CommittedPart], Any] = {
    StepPart: lambda p: StepAssistantPart(state=p.state, step=p.step, duration_ms=p.duration_ms),
    ReasoningPart: lambda p: ReasoningAssistantPart(text=p.text, step=p.step),
    CodePart: lambda p: CodeAssistantPart(code=p.code, step=p.step),
    OutputPart: lambda p: OutputAssistantPart(output=p.output, step=p.step),
    ToolCallPart: lambda p: ToolCallAssistantPart(
        tool_call_id=p.tool_call_id,
        tool_name=p.tool_name,
        state=p.state,
        input=_plain_json(p.input),
        output=_plain_json(p.output),
        error=p.error,
    ),
    SkillPart: lambda p: SkillAssistantPart(
        skill_id=p.skill_id,
        name=p.name,
        phase=p.phase,
        version=p.version,
        trust=p.trust,
        affordances=list(p.affordances),
    ),
    AttachmentPart: lambda p: AttachmentAssistantPart(
        attachment_id=p.attachment_id,
        phase=p.phase,
        filename=p.filename,
        byte_size=p.byte_size,
    ),
    WarningPart: lambda p: WarningAssistantPart(message=p.message, code=p.code),
    StatusPart: lambda p: StatusAssistantPart(phase=p.phase, status=p.status, message=p.message),
    ChildProgressPart: lambda p: ChildProgressAssistantPart(
        child_id=p.child_id,
        task_label=p.task_label,
        state=p.state,
        elapsed_ms=p.elapsed_ms,
        outcome=p.outcome,
        evidence=p.evidence,
        gaps=p.gaps,
        result_file_count=p.result_file_count,
        code_excerpt=p.code_excerpt,
        output_excerpt=p.output_excerpt,
        cleanup_state=p.cleanup_state,
        parent_run_id=p.parent_run_id,
    ),
    ArtifactPart: lambda p: ArtifactAssistantPart(
        artifact_id=p.artifact_id,
        kind=p.kind,
        title=p.title,
        media_type=p.media_type,
        byte_size=p.byte_size,
        checksum_sha256=p.checksum_sha256,
    ),
    UsagePart: lambda p: UsageAssistantPart(value=validate_rlm_usage(dict(p.value))),
    StructuredResultPart: lambda p: StructuredResultAssistantPart(
        schema_id=p.schema_id,
        schema_version=p.schema_version,
        value=_plain_json(p.value),
    ),
    TextPart: lambda p: TextAssistantPart(text=p.text),
}


def assistant_part_to_model(part: CommittedPart) -> AssistantPart:
    """Project a runtime committed part into its canonical Pydantic contract."""
    return _COMMITTED_TO_MODEL[type(part)](part)


_MODEL_TO_COMMITTED: dict[type[AssistantPartModel], Any] = {
    StepAssistantPart: lambda p: StepPart(state=p.state, step=p.step, duration_ms=p.duration_ms),
    ReasoningAssistantPart: lambda p: ReasoningPart(text=p.text, step=p.step),
    CodeAssistantPart: lambda p: CodePart(code=p.code, step=p.step),
    OutputAssistantPart: lambda p: OutputPart(output=p.output, step=p.step),
    ToolCallAssistantPart: lambda p: ToolCallPart(
        tool_call_id=p.tool_call_id,
        tool_name=p.tool_name,
        state=p.state,
        input=_plain_json(p.input),
        output=_plain_json(p.output),
        error=p.error,
    ),
    SkillAssistantPart: lambda p: SkillPart(
        skill_id=p.skill_id,
        name=p.name,
        phase=p.phase,
        version=p.version,
        trust=p.trust,
        affordances=tuple(p.affordances),
    ),
    AttachmentAssistantPart: lambda p: AttachmentPart(
        attachment_id=p.attachment_id,
        phase=p.phase,
        filename=p.filename,
        byte_size=p.byte_size,
    ),
    WarningAssistantPart: lambda p: WarningPart(message=p.message, code=p.code),
    StatusAssistantPart: lambda p: StatusPart(phase=p.phase, status=p.status, message=p.message),
    ChildProgressAssistantPart: lambda p: ChildProgressPart(
        child_id=p.child_id,
        task_label=p.task_label,
        state=p.state,
        elapsed_ms=p.elapsed_ms,
        outcome=p.outcome,
        evidence=p.evidence,
        gaps=p.gaps,
        result_file_count=p.result_file_count,
        code_excerpt=p.code_excerpt,
        output_excerpt=p.output_excerpt,
        cleanup_state=p.cleanup_state,
        parent_run_id=p.parent_run_id,
    ),
    ArtifactAssistantPart: lambda p: ArtifactPart(
        artifact_id=p.artifact_id,
        kind=p.kind,
        title=p.title,
        media_type=p.media_type,
        byte_size=p.byte_size,
        checksum_sha256=p.checksum_sha256,
    ),
    UsageAssistantPart: lambda p: UsagePart(value=cast(RLMUsage, dict(p.value))),
    StructuredResultAssistantPart: lambda p: StructuredResultPart(
        schema_id=p.schema_id,
        schema_version=p.schema_version,
        value=p.value,
    ),
    TextAssistantPart: lambda p: TextPart(text=p.text),
}


def assistant_part_from_model(part: AssistantPart) -> CommittedPart:
    """Convert a validated canonical part into the runtime committed aggregate."""
    return _MODEL_TO_COMMITTED[type(part)](part)


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
    "ChildProgressAssistantPart",
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
