"""Closed durable semantic result for one successfully committed Turn."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal, TypeAlias, assert_never, cast
from uuid import UUID

from pydantic import ValidationError

from fleet_rlm.artifacts.models import ArtifactRef
from fleet_rlm.json_types import JsonScalar as JsonScalar
from fleet_rlm.json_types import JsonValue as JsonValue
from fleet_rlm.observability.tracing import current_turn_trace_id
from fleet_rlm.rlm.events import (
    ArtifactCreated,
    AttachmentRead,
    ChildProgress,
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
from fleet_rlm.rlm.result import RLMOutcome, validate_rlm_usage
from fleet_rlm.sessions.usage import RLMUsage


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
class ChildProgressPart:
    child_id: str
    task_label: str
    state: Literal["not_started", "running", "completed", "failed", "cancelled", "timed_out"]
    elapsed_ms: int
    outcome: str | None = None
    cleanup_state: Literal["pending", "complete", "failed", "not_required"] = "not_required"
    parent_run_id: str | None = None
    evidence: tuple[str, ...] = ()
    gaps: tuple[str, ...] = ()
    result_file_count: int = 0
    code_excerpt: str | None = None
    output_excerpt: str | None = None
    type: Literal["child_progress"] = "child_progress"

    def __post_init__(self) -> None:
        if not self.child_id.strip() or len(self.child_id) > 128:
            raise CommittedTurnValidationError("child_id must contain 1 to 128 non-blank characters")
        if not self.task_label.strip() or len(self.task_label) > 240:
            raise CommittedTurnValidationError("task_label must contain 1 to 240 non-blank characters")
        _require_nonnegative(self.elapsed_ms, "elapsed_ms")
        if (
            not isinstance(self.result_file_count, int)
            or isinstance(self.result_file_count, bool)
            or not 0 <= self.result_file_count <= 16
        ):
            raise CommittedTurnValidationError("result_file_count must be between 0 and 16")
        if self.outcome is not None and len(self.outcome) > 500:
            raise CommittedTurnValidationError("outcome must not exceed 500 characters")
        if any(value is not None and len(value) > 800 for value in (self.code_excerpt, self.output_excerpt)):
            raise CommittedTurnValidationError("child code and output excerpts must not exceed 800 characters")
        if (
            len(self.evidence) > 8
            or len(self.gaps) > 8
            or any(not isinstance(item, str) or len(item) > 200 for item in (*self.evidence, *self.gaps))
        ):
            raise CommittedTurnValidationError("child progress details exceed their bounds")
        if self.parent_run_id is not None and (not self.parent_run_id.strip() or len(self.parent_run_id) > 128):
            raise CommittedTurnValidationError("parent_run_id must contain 1 to 128 non-blank characters")


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
    | ChildProgressPart
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
    ChildProgressPart,
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


CANCELLED_TOMBSTONE_TEXT = "Turn cancelled"


def commit_cancelled_tombstone(usage: RLMUsage) -> CommittedTurn:
    """Build the bounded D2 tombstone committed for one cancelled Run.

    The mark is deliberately closed: one status part with ``phase="cancelled"``,
    the observed usage, and a constant final text. No evidence parts (reasoning,
    code, output, tools) ever enter the durable cancellation record.
    """
    return CommittedTurn(
        schema_version=1,
        parts=(
            StatusPart(phase="cancelled", status="cancelled"),
            UsagePart(value=usage),
            TextPart(text=CANCELLED_TOMBSTONE_TEXT),
        ),
    )


ProjectionMode = Literal["replay", "live_suffix"]
_SUFFIX_TYPES = (ArtifactPart, UsagePart, StructuredResultPart, TextPart)


class CommittedTurnProjectionError(ValueError):
    """Raised when durable data cannot be projected without semantic loss."""


def _project_part_details(part: CommittedPart):
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
    if isinstance(part, ChildProgressPart):
        return (
            ChildProgress(
                child_id=part.child_id,
                task_label=part.task_label,
                state=part.state,
                elapsed_ms=part.elapsed_ms,
                outcome=part.outcome,
                evidence=part.evidence,
                gaps=part.gaps,
                result_file_count=part.result_file_count,
                code_excerpt=part.code_excerpt,
                output_excerpt=part.output_excerpt,
                cleanup_state=part.cleanup_state,
                parent_run_id=part.parent_run_id,
            ),
        )
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
            events.extend(recorder.record(detail) for detail in _project_part_details(part))
        return tuple(events)


class TurnDetailPolicyError(ValueError):
    """Raised when an outcome cannot be represented by the durable contract."""


@dataclass(frozen=True, slots=True)
class _PendingToolCall:
    detail: ToolStarted
    position: int


@dataclass(slots=True)
class _NormalizeState:
    parts: list[CommittedPart | None]
    pending: dict[str, _PendingToolCall]
    streaming_outputs: dict[str, int]
    child_progress: dict[str, int]


def _append_output_detail(detail: RLMOutput, state: _NormalizeState) -> None:
    stream_id = detail.stream_id
    position = state.streaming_outputs.get(stream_id) if stream_id else None
    if detail.is_delta and stream_id:
        if position is None:
            state.streaming_outputs[stream_id] = len(state.parts)
            state.parts.append(OutputPart(output=detail.output, step=detail.step))
            return
        prior = state.parts[position]
        if not isinstance(prior, OutputPart):
            raise TurnDetailPolicyError("streaming output position is not an output part")
        state.parts[position] = OutputPart(output=prior.output + detail.output, step=detail.step)
        return

    part = OutputPart(output=detail.output, step=detail.step)
    if position is not None:
        state.parts[position] = part
        return
    if stream_id:
        state.streaming_outputs[stream_id] = len(state.parts)
    state.parts.append(part)


def _append_tool_terminal_detail(detail: ToolCompleted | ToolFailed, state: _NormalizeState) -> None:
    started = state.pending.pop(detail.tool_call_id, None)
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
    state.parts[started.position] = part


def _normalize_execution(outcome: RLMOutcome) -> list[CommittedPart]:
    state = _NormalizeState(parts=[], pending={}, streaming_outputs={}, child_progress={})

    for detail in outcome.execution_details:
        if isinstance(detail, StepStarted):
            state.parts.append(StepPart(state="started", step=detail.step))
        elif isinstance(detail, StepFinished):
            state.parts.append(StepPart(state="finished", step=detail.step, duration_ms=detail.duration_ms))
        elif isinstance(detail, RLMReasoning):
            state.parts.append(ReasoningPart(text=detail.text, step=detail.step))
        elif isinstance(detail, RLMCode):
            state.parts.append(CodePart(code=detail.code, step=detail.step))
        elif isinstance(detail, RLMOutput):
            _append_output_detail(detail, state)
        elif isinstance(detail, ToolStarted):
            if detail.tool_call_id in state.pending:
                raise TurnDetailPolicyError("duplicate tool call start")
            state.pending[detail.tool_call_id] = _PendingToolCall(detail, len(state.parts))
            state.parts.append(None)
        elif isinstance(detail, (ToolCompleted, ToolFailed)):
            _append_tool_terminal_detail(detail, state)
        elif isinstance(detail, SkillActivated):
            state.parts.append(
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
            state.parts.append(
                SkillPart(
                    skill_id=detail.skill_id,
                    name=detail.name,
                    phase="loaded",
                    version=detail.version,
                )
            )
        elif isinstance(detail, AttachmentRead):
            state.parts.append(
                AttachmentPart(
                    attachment_id=detail.attachment_id,
                    phase="read",
                    filename=detail.filename,
                    byte_size=detail.byte_size,
                )
            )
        elif isinstance(detail, WarningEvent):
            state.parts.append(WarningPart(message=detail.message, code=detail.code))
        elif isinstance(detail, ChildProgress):
            if detail.state != "running":
                part = ChildProgressPart(
                    child_id=detail.child_id,
                    task_label=detail.task_label,
                    state=detail.state,
                    elapsed_ms=detail.elapsed_ms,
                    outcome=detail.outcome,
                    evidence=detail.evidence,
                    gaps=detail.gaps,
                    result_file_count=detail.result_file_count,
                    code_excerpt=detail.code_excerpt,
                    output_excerpt=detail.output_excerpt,
                    cleanup_state=detail.cleanup_state,
                    parent_run_id=detail.parent_run_id,
                )
                pos = state.child_progress.get(detail.child_id)
                if pos is None:
                    state.child_progress[detail.child_id] = len(state.parts)
                    state.parts.append(part)
                else:
                    state.parts[pos] = part
        else:
            raise TurnDetailPolicyError(f"unsupported execution detail: {type(detail).__name__}")

    if state.pending:
        raise TurnDetailPolicyError("tool call start has no terminal observation")
    return [part for part in state.parts if part is not None]


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
    return CommittedTurn(schema_version=1, parts=tuple(parts), trace_id=current_turn_trace_id())
