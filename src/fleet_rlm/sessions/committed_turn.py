"""Closed durable semantic result for one successfully committed Turn."""

from __future__ import annotations

from collections.abc import Mapping, Sequence, Set
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal, TypeAlias, cast
from uuid import UUID

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


def _expect_exact(data: Mapping[str, object], required: Set[str], optional: Set[str] = frozenset()) -> None:
    keys = set(data)
    if not required <= keys or keys - required - optional:
        raise CommittedTurnValidationError("committed part has missing or unknown fields")


def _optional_int(data: Mapping[str, object], name: str) -> int | None:
    value = data.get(name)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise CommittedTurnValidationError(f"{name} must be an integer")
    return value


def _required_str(data: Mapping[str, object], name: str) -> str:
    value = data.get(name)
    if not isinstance(value, str):
        raise CommittedTurnValidationError(f"{name} must be a string")
    return value


def _optional_str(data: Mapping[str, object], name: str) -> str | None:
    value = data.get(name)
    if value is not None and not isinstance(value, str):
        raise CommittedTurnValidationError(f"{name} must be a string or null")
    return value


def _required_str_list(data: Mapping[str, object], name: str) -> list[str]:
    value = data.get(name)
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise CommittedTurnValidationError(f"{name} must be an array of strings")
    return cast(list[str], value)


def _required_uuid(data: Mapping[str, object], name: str) -> UUID:
    try:
        return UUID(_required_str(data, name))
    except ValueError as exc:
        raise CommittedTurnValidationError(f"invalid {name}") from exc


def _decode_part(value: object) -> CommittedPart:
    data = _expect_mapping(value, "committed part")
    part_type = data.get("type")
    if part_type == "reasoning":
        _expect_exact(data, {"type", "text"}, {"step"})
        return ReasoningPart(text=_required_str(data, "text"), step=_optional_int(data, "step"))
    if part_type == "code":
        _expect_exact(data, {"type", "code"}, {"step"})
        return CodePart(code=_required_str(data, "code"), step=_optional_int(data, "step"))
    if part_type == "output":
        _expect_exact(data, {"type", "output"}, {"step"})
        return OutputPart(output=_required_str(data, "output"), step=_optional_int(data, "step"))
    if part_type == "step":
        _expect_exact(data, {"type", "state", "step"}, {"duration_ms"})
        state = _required_str(data, "state")
        if state not in {"started", "finished"}:
            raise CommittedTurnValidationError("invalid step state")
        step = _optional_int(data, "step")
        assert step is not None
        return StepPart(state=cast(Any, state), step=step, duration_ms=_optional_int(data, "duration_ms"))
    if part_type == "tool_call":
        _expect_exact(
            data,
            {"type", "tool_call_id", "tool_name", "state", "input", "output", "error"},
        )
        state = _required_str(data, "state")
        if state not in {"completed", "failed"}:
            raise CommittedTurnValidationError("invalid tool call state")
        raw_output = data.get("output")
        return ToolCallPart(
            tool_call_id=_required_str(data, "tool_call_id"),
            tool_name=_required_str(data, "tool_name"),
            state=cast(Any, state),
            input=_freeze_json(data["input"], path="tool_call.input"),
            output=(_freeze_json(raw_output, path="tool_call.output") if raw_output is not None else None),
            error=_optional_str(data, "error"),
        )
    if part_type == "skill":
        _expect_exact(
            data,
            {"type", "skill_id", "name", "phase", "version", "trust", "affordances"},
        )
        phase = _required_str(data, "phase")
        if phase not in {"activated", "loaded"}:
            raise CommittedTurnValidationError("invalid skill phase")
        return SkillPart(
            skill_id=_required_str(data, "skill_id"),
            name=_required_str(data, "name"),
            phase=cast(Any, phase),
            version=_optional_str(data, "version"),
            trust=_optional_str(data, "trust"),
            affordances=tuple(_required_str_list(data, "affordances")),
        )
    if part_type == "attachment":
        _expect_exact(data, {"type", "attachment_id", "phase", "filename", "byte_size"})
        phase = _required_str(data, "phase")
        if phase not in {"selected", "read"}:
            raise CommittedTurnValidationError("invalid attachment phase")
        return AttachmentPart(
            attachment_id=_required_uuid(data, "attachment_id"),
            phase=cast(Any, phase),
            filename=_optional_str(data, "filename"),
            byte_size=_optional_int(data, "byte_size"),
        )
    if part_type == "warning":
        _expect_exact(data, {"type", "message", "code"})
        return WarningPart(
            message=_required_str(data, "message"),
            code=_optional_str(data, "code"),
        )
    if part_type == "status":
        _expect_exact(data, {"type", "phase", "status", "message"})
        return StatusPart(
            phase=_required_str(data, "phase"),
            status=_required_str(data, "status"),
            message=_optional_str(data, "message"),
        )
    if part_type == "artifact":
        _expect_exact(
            data,
            {"type", "artifact_id", "kind", "title", "media_type", "byte_size", "checksum_sha256"},
        )
        kind = _required_str(data, "kind")
        if kind not in {"text", "markdown", "json"}:
            raise CommittedTurnValidationError("invalid artifact kind")
        title = data.get("title")
        if title is not None and not isinstance(title, str):
            raise CommittedTurnValidationError("artifact title must be a string or null")
        byte_size = _optional_int(data, "byte_size")
        assert byte_size is not None
        return ArtifactPart(
            artifact_id=_required_uuid(data, "artifact_id"),
            kind=cast(Any, kind),
            title=title,
            media_type=_required_str(data, "media_type"),
            byte_size=byte_size,
            checksum_sha256=_required_str(data, "checksum_sha256"),
        )
    if part_type == "usage":
        _expect_exact(data, {"type", "value"})
        return UsagePart(value=cast(RLMUsage, _expect_mapping(data["value"], "usage.value")))
    if part_type == "structured_result":
        _expect_exact(data, {"type", "schema_id", "schema_version", "value"})
        return StructuredResultPart(
            schema_id=_required_str(data, "schema_id"),
            schema_version=_required_str(data, "schema_version"),
            value=_freeze_json(data["value"], path="structured_result.value"),
        )
    if part_type == "text":
        _expect_exact(data, {"type", "text"})
        return TextPart(text=_required_str(data, "text"))
    raise CommittedTurnValidationError(f"unknown committed part type: {part_type!r}")


def _encode_part(part: CommittedPart) -> dict[str, Any]:
    if isinstance(part, ReasoningPart):
        return {"type": part.type, "text": part.text, **({"step": part.step} if part.step is not None else {})}
    if isinstance(part, CodePart):
        return {"type": part.type, "code": part.code, **({"step": part.step} if part.step is not None else {})}
    if isinstance(part, OutputPart):
        return {"type": part.type, "output": part.output, **({"step": part.step} if part.step is not None else {})}
    if isinstance(part, StepPart):
        result = {"type": part.type, "state": part.state, "step": part.step}
        if part.duration_ms is not None:
            result["duration_ms"] = part.duration_ms
        return result
    if isinstance(part, ToolCallPart):
        return {
            "type": part.type,
            "tool_call_id": part.tool_call_id,
            "tool_name": part.tool_name,
            "state": part.state,
            "input": _thaw_json(part.input),
            "output": _thaw_json(part.output) if part.output is not None else None,
            "error": part.error,
        }
    if isinstance(part, SkillPart):
        return {
            "type": part.type,
            "skill_id": part.skill_id,
            "name": part.name,
            "phase": part.phase,
            "version": part.version,
            "trust": part.trust,
            "affordances": list(part.affordances),
        }
    if isinstance(part, AttachmentPart):
        return {
            "type": part.type,
            "attachment_id": str(part.attachment_id),
            "phase": part.phase,
            "filename": part.filename,
            "byte_size": part.byte_size,
        }
    if isinstance(part, WarningPart):
        return {"type": part.type, "message": part.message, "code": part.code}
    if isinstance(part, StatusPart):
        return {"type": part.type, "phase": part.phase, "status": part.status, "message": part.message}
    if isinstance(part, ArtifactPart):
        return {
            "type": part.type,
            "artifact_id": str(part.artifact_id),
            "kind": part.kind,
            "title": part.title,
            "media_type": part.media_type,
            "byte_size": part.byte_size,
            "checksum_sha256": part.checksum_sha256,
        }
    if isinstance(part, UsagePart):
        return {"type": part.type, "value": _thaw_json(part.value)}
    if isinstance(part, StructuredResultPart):
        return {
            "type": part.type,
            "schema_id": part.schema_id,
            "schema_version": part.schema_version,
            "value": _thaw_json(part.value),
        }
    if isinstance(part, TextPart):
        return {"type": part.type, "text": part.text}
    raise AssertionError(f"unhandled committed part: {type(part).__name__}")


class CommittedTurnCodec:
    """Strict JSON codec for the versioned aggregate."""

    @staticmethod
    def encode(committed: CommittedTurn) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": committed.schema_version,
            "parts": [_encode_part(part) for part in committed.parts],
        }
        if committed.trace_id:
            payload["trace_id"] = committed.trace_id
        return payload

    @staticmethod
    def decode(value: object) -> CommittedTurn:
        data = _expect_mapping(value, "committed Turn")
        _expect_exact(data, {"schema_version", "parts"}, optional={"trace_id"})
        if data.get("schema_version") != 1:
            raise CommittedTurnValidationError("unsupported committed Turn schema version")
        raw_parts = data.get("parts")
        if not isinstance(raw_parts, Sequence) or isinstance(raw_parts, (str, bytes, bytearray)):
            raise CommittedTurnValidationError("committed Turn parts must be an array")
        trace_id = _optional_str(data, "trace_id")
        return CommittedTurn(schema_version=1, parts=tuple(_decode_part(part) for part in raw_parts), trace_id=trace_id)
