"""Closed durable semantic result for one successfully committed Turn."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal, TypeAlias, cast
from uuid import UUID

from pydantic import ValidationError

from fleet_rlm.json_types import JsonScalar as JsonScalar
from fleet_rlm.json_types import JsonValue as JsonValue
from fleet_rlm.rlm.dspy_contract import RLMUsage, validate_rlm_usage


class CommittedTurnValidationError(ValueError):
    """Raised when durable committed data is unknown, malformed, or noncanonical."""


def _freeze_json(value: object, *, path: str) -> JsonValue:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise CommittedTurnValidationError(f"{path} object keys must be strings")
            frozen[key] = _freeze_json(item, path=f"{path}.{key}")
        return MappingProxyType(frozen)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(_freeze_json(item, path=f"{path}[]") for item in value)
    raise CommittedTurnValidationError(f"{path} must be a JSON value")


def _thaw_json(value: JsonValue) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json(cast(JsonValue, item)) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _require_nonnegative(value: int, name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise CommittedTurnValidationError(f"{name} must be a non-negative integer")


def _require_optional_step(value: int | None) -> None:
    if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 1):
        raise CommittedTurnValidationError("step must be a positive integer when present")


@dataclass(frozen=True, slots=True)
class StepPart:
    type: Literal["step"] = "step"
    state: Literal["started", "finished"] = "started"
    step: int = 1
    duration_ms: int | None = None

    def __post_init__(self) -> None:
        _require_optional_step(self.step)
        if self.duration_ms is not None:
            _require_nonnegative(self.duration_ms, "duration_ms")


@dataclass(frozen=True, slots=True)
class ReasoningPart:
    text: str
    step: int | None = None
    type: Literal["reasoning"] = "reasoning"

    def __post_init__(self) -> None:
        _require_optional_step(self.step)


@dataclass(frozen=True, slots=True)
class CodePart:
    code: str
    step: int | None = None
    type: Literal["code"] = "code"

    def __post_init__(self) -> None:
        _require_optional_step(self.step)


@dataclass(frozen=True, slots=True)
class OutputPart:
    output: str
    step: int | None = None
    type: Literal["output"] = "output"

    def __post_init__(self) -> None:
        _require_optional_step(self.step)


@dataclass(frozen=True, slots=True)
class ToolCallPart:
    tool_call_id: str
    tool_name: str
    state: Literal["completed", "failed"]
    input: JsonValue
    output: JsonValue | None = None
    error: str | None = None
    type: Literal["tool_call"] = "tool_call"

    def __post_init__(self) -> None:
        if not self.tool_call_id or not self.tool_name:
            raise CommittedTurnValidationError("tool call id and name are required")
        if self.state == "completed" and self.error is not None:
            raise CommittedTurnValidationError("completed tool calls cannot contain an error")
        if self.state == "failed" and not self.error:
            raise CommittedTurnValidationError("failed tool calls require an error")
        object.__setattr__(self, "input", _freeze_json(self.input, path="tool_call.input"))
        if self.output is not None:
            object.__setattr__(self, "output", _freeze_json(self.output, path="tool_call.output"))


@dataclass(frozen=True, slots=True)
class SkillPart:
    skill_id: str
    name: str
    phase: Literal["activated", "loaded"]
    version: str | None = None
    trust: str | None = None
    affordances: tuple[str, ...] = ()
    type: Literal["skill"] = "skill"

    def __post_init__(self) -> None:
        if not self.skill_id or not self.name:
            raise CommittedTurnValidationError("skill id and name are required")
        if self.phase == "activated" and not self.trust:
            raise CommittedTurnValidationError("activated skills require trust metadata")
        if self.phase == "loaded" and (self.trust is not None or self.affordances):
            raise CommittedTurnValidationError("loaded skills cannot contain activation metadata")


@dataclass(frozen=True, slots=True)
class AttachmentPart:
    attachment_id: UUID
    phase: Literal["selected", "read"]
    filename: str | None = None
    byte_size: int | None = None
    type: Literal["attachment"] = "attachment"

    def __post_init__(self) -> None:
        if self.byte_size is not None:
            _require_nonnegative(self.byte_size, "byte_size")


@dataclass(frozen=True, slots=True)
class WarningPart:
    message: str
    code: str | None = None
    type: Literal["warning"] = "warning"

    def __post_init__(self) -> None:
        if not self.message:
            raise CommittedTurnValidationError("warning message is required")


@dataclass(frozen=True, slots=True)
class StatusPart:
    """Bounded terminal status marker (used only by cancellation tombstones)."""

    phase: str
    status: str
    message: str | None = None
    type: Literal["status"] = "status"

    def __post_init__(self) -> None:
        if not self.phase or not self.status:
            raise CommittedTurnValidationError("status phase and status are required")


@dataclass(frozen=True, slots=True)
class ArtifactPart:
    artifact_id: UUID
    kind: Literal["text", "markdown", "json"]
    title: str | None
    media_type: str
    byte_size: int
    checksum_sha256: str
    type: Literal["artifact"] = "artifact"

    def __post_init__(self) -> None:
        _require_nonnegative(self.byte_size, "byte_size")
        checksum = self.checksum_sha256.lower()
        if len(checksum) != 64 or any(char not in "0123456789abcdef" for char in checksum):
            raise CommittedTurnValidationError("checksum_sha256 must contain 64 hexadecimal characters")
        if not self.media_type:
            raise CommittedTurnValidationError("artifact media_type is required")
        object.__setattr__(self, "checksum_sha256", checksum)


@dataclass(frozen=True, slots=True)
class UsagePart:
    value: RLMUsage
    type: Literal["usage"] = "usage"

    def __post_init__(self) -> None:
        try:
            usage = validate_rlm_usage(self.value)
        except ValueError as exc:
            raise CommittedTurnValidationError(str(exc)) from exc
        value = _freeze_json(usage, path="usage.value")
        if not isinstance(value, Mapping):
            raise CommittedTurnValidationError("usage value must be a JSON object")
        object.__setattr__(self, "value", value)


@dataclass(frozen=True, slots=True)
class StructuredResultPart:
    schema_id: str
    schema_version: str
    value: JsonValue
    type: Literal["structured_result"] = "structured_result"

    def __post_init__(self) -> None:
        if not self.schema_id or not self.schema_version:
            raise CommittedTurnValidationError("structured result schema id and version are required")
        object.__setattr__(self, "value", _freeze_json(self.value, path="structured_result.value"))


@dataclass(frozen=True, slots=True)
class TextPart:
    text: str
    type: Literal["text"] = "text"

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise CommittedTurnValidationError("a committed Turn requires non-blank final text")


CommittedPart: TypeAlias = (
    StepPart
    | ReasoningPart
    | CodePart
    | OutputPart
    | ToolCallPart
    | SkillPart
    | AttachmentPart
    | WarningPart
    | StatusPart
    | ArtifactPart
    | UsagePart
    | StructuredResultPart
    | TextPart
)

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
                return _thaw_json(part.value)
        return None


def _expect_mapping(value: object, path: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise CommittedTurnValidationError(f"{path} must be an object")
    return cast(Mapping[str, object], value)


class CommittedTurnCodec:
    """Strict JSON codec for the versioned aggregate.

    Assistant-part payloads are validated by the canonical discriminated
    Pydantic vocabulary in `sessions.assistant_parts`; this codec only adds the
    committed envelope and canonical band-order validation.
    """

    @staticmethod
    def encode(committed: CommittedTurn) -> dict[str, Any]:
        from fleet_rlm.sessions.assistant_parts import assistant_part_payload

        payload: dict[str, Any] = {
            "schema_version": committed.schema_version,
            "parts": [assistant_part_payload(part) for part in committed.parts],
        }
        if committed.trace_id:
            payload["trace_id"] = committed.trace_id
        return payload

    @staticmethod
    def decode(value: object) -> CommittedTurn:
        from fleet_rlm.sessions.assistant_parts import assistant_part_from_payload

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
        try:
            parts = tuple(assistant_part_from_payload(part) for part in raw_parts)
        except (ValidationError, ValueError) as exc:
            message = str(exc)
            if "String should have at least 1 character" in message and any(
                isinstance(part, Mapping) and part.get("type") == "text" for part in raw_parts
            ):
                message = "a committed Turn requires non-blank final text"
            raise CommittedTurnValidationError(message) from exc
        return CommittedTurn(schema_version=1, parts=parts, trace_id=trace_id)
