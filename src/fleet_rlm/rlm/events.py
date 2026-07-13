"""Closed transport-neutral Runtime Event delivery records."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType
from typing import ClassVar, Literal, TypeAlias
from uuid import UUID, uuid4

JsonScalar: TypeAlias = None | bool | int | float | str
JsonValue: TypeAlias = JsonScalar | tuple["JsonValue", ...] | Mapping[str, "JsonValue"]


def _freeze_json(value: object) -> JsonValue:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("JSON object keys must be strings")
        return MappingProxyType({str(key): _freeze_json(item) for key, item in value.items()})
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(_freeze_json(item) for item in value)
    raise TypeError("Runtime Event values must be JSON-compatible")


@dataclass(frozen=True, slots=True)
class RunStarted:
    kind: ClassVar[Literal["run.started"]] = "run.started"
    delivery: Literal["live", "replay"]


@dataclass(frozen=True, slots=True)
class Status:
    kind: ClassVar[Literal["status"]] = "status"
    phase: str
    status: str
    message: str | None = None


@dataclass(frozen=True, slots=True)
class StepStarted:
    kind: ClassVar[Literal["step.started"]] = "step.started"
    step: int


@dataclass(frozen=True, slots=True)
class StepFinished:
    kind: ClassVar[Literal["step.finished"]] = "step.finished"
    step: int
    duration_ms: int | None = None


@dataclass(frozen=True, slots=True)
class RLMReasoning:
    kind: ClassVar[Literal["rlm.reasoning"]] = "rlm.reasoning"
    text: str
    step: int | None = None


@dataclass(frozen=True, slots=True)
class RLMCode:
    kind: ClassVar[Literal["rlm.code"]] = "rlm.code"
    code: str
    step: int | None = None


@dataclass(frozen=True, slots=True)
class RLMOutput:
    kind: ClassVar[Literal["rlm.output"]] = "rlm.output"
    output: str
    step: int | None = None


@dataclass(frozen=True, slots=True)
class ToolStarted:
    kind: ClassVar[Literal["tool.started"]] = "tool.started"
    tool_call_id: str
    tool_name: str
    input: JsonValue

    def __post_init__(self) -> None:
        object.__setattr__(self, "input", _freeze_json(self.input))


@dataclass(frozen=True, slots=True)
class ToolCompleted:
    kind: ClassVar[Literal["tool.completed"]] = "tool.completed"
    tool_call_id: str
    tool_name: str
    output: JsonValue

    def __post_init__(self) -> None:
        object.__setattr__(self, "output", _freeze_json(self.output))


@dataclass(frozen=True, slots=True)
class ToolFailed:
    kind: ClassVar[Literal["tool.failed"]] = "tool.failed"
    tool_call_id: str
    tool_name: str
    error: str


@dataclass(frozen=True, slots=True)
class SkillActivated:
    kind: ClassVar[Literal["skill.activated"]] = "skill.activated"
    skill_id: str
    name: str
    version: str
    trust: str
    affordances: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SkillLoaded:
    kind: ClassVar[Literal["skill.loaded"]] = "skill.loaded"
    skill_id: str
    name: str
    version: str


@dataclass(frozen=True, slots=True)
class AttachmentRead:
    kind: ClassVar[Literal["attachment.read"]] = "attachment.read"
    attachment_id: UUID
    filename: str
    byte_size: int


@dataclass(frozen=True, slots=True)
class WarningEvent:
    kind: ClassVar[Literal["warning"]] = "warning"
    message: str
    code: str | None = None


@dataclass(frozen=True, slots=True)
class ArtifactCreated:
    kind: ClassVar[Literal["artifact.created"]] = "artifact.created"
    artifact_id: UUID
    artifact_kind: Literal["text", "markdown", "json"]
    title: str | None
    media_type: str
    byte_size: int
    checksum_sha256: str


@dataclass(frozen=True, slots=True)
class Usage:
    kind: ClassVar[Literal["usage"]] = "usage"
    value: Mapping[str, JsonValue]

    def __post_init__(self) -> None:
        frozen = _freeze_json(self.value)
        if not isinstance(frozen, Mapping):
            raise TypeError("usage must be an object")
        object.__setattr__(self, "value", frozen)


@dataclass(frozen=True, slots=True)
class StructuredResult:
    kind: ClassVar[Literal["structured.result"]] = "structured.result"
    schema_id: str
    schema_version: str
    value: JsonValue

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _freeze_json(self.value))


@dataclass(frozen=True, slots=True)
class TextDelta:
    kind: ClassVar[Literal["text.delta"]] = "text.delta"
    text: str


@dataclass(frozen=True, slots=True)
class TextCompleted:
    kind: ClassVar[Literal["text.completed"]] = "text.completed"
    text: str


@dataclass(frozen=True, slots=True)
class RunCompleted:
    kind: ClassVar[Literal["run.completed"]] = "run.completed"
    checkpoint_version: int
    delivery: Literal["live", "replay"]
    duration_ms: int | None = None


@dataclass(frozen=True, slots=True)
class RunFailed:
    kind: ClassVar[Literal["run.failed"]] = "run.failed"
    code: Literal["preparation_failed", "execution_failed", "commit_failed", "protocol_error", "unavailable"]
    message: Literal["Turn failed", "Turn could not be prepared", "Turn could not be committed"]
    duration_ms: int | None = None


@dataclass(frozen=True, slots=True)
class RunCancelled:
    kind: ClassVar[Literal["run.cancelled"]] = "run.cancelled"
    message: Literal["Turn cancelled"] = "Turn cancelled"
    duration_ms: int | None = None


@dataclass(frozen=True, slots=True)
class RunTimedOut:
    kind: ClassVar[Literal["run.timed_out"]] = "run.timed_out"
    message: Literal["Turn timed out"] = "Turn timed out"
    duration_ms: int | None = None


@dataclass(frozen=True, slots=True)
class RunBudgetExhausted:
    kind: ClassVar[Literal["run.budget_exhausted"]] = "run.budget_exhausted"
    message: Literal["Turn budget exhausted"] = "Turn budget exhausted"
    duration_ms: int | None = None


RuntimeEventDetail: TypeAlias = (
    RunStarted
    | Status
    | StepStarted
    | StepFinished
    | RLMReasoning
    | RLMCode
    | RLMOutput
    | ToolStarted
    | ToolCompleted
    | ToolFailed
    | SkillActivated
    | SkillLoaded
    | AttachmentRead
    | WarningEvent
    | ArtifactCreated
    | Usage
    | StructuredResult
    | TextDelta
    | TextCompleted
    | RunCompleted
    | RunFailed
    | RunCancelled
    | RunTimedOut
    | RunBudgetExhausted
)

RUNTIME_DETAIL_TYPES = (
    RunStarted,
    Status,
    StepStarted,
    StepFinished,
    RLMReasoning,
    RLMCode,
    RLMOutput,
    ToolStarted,
    ToolCompleted,
    ToolFailed,
    SkillActivated,
    SkillLoaded,
    AttachmentRead,
    WarningEvent,
    ArtifactCreated,
    Usage,
    StructuredResult,
    TextDelta,
    TextCompleted,
    RunCompleted,
    RunFailed,
    RunCancelled,
    RunTimedOut,
    RunBudgetExhausted,
)

TERMINAL_DETAIL_TYPES = (RunCompleted, RunFailed, RunCancelled, RunTimedOut, RunBudgetExhausted)


class EventSequenceError(RuntimeError):
    """Raised for a duplicate terminal or any post-terminal detail."""


@dataclass(frozen=True, slots=True)
class RuntimeEvent:
    """Immutable delivery envelope around one closed typed detail."""

    schema_version: Literal[1]
    event_id: UUID
    run_id: UUID
    session_id: UUID
    sequence: int
    timestamp: datetime
    detail: RuntimeEventDetail

    @property
    def kind(self) -> str:
        return self.detail.kind


@dataclass(slots=True)
class EventRecorder:
    """Assign strictly increasing delivery metadata and guard one terminal."""

    run_id: UUID
    session_id: UUID
    start_sequence: int = 0
    _sequence: int = field(init=False, repr=False)
    _terminal_emitted: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        self._sequence = int(self.start_sequence)

    def record(self, detail: RuntimeEventDetail) -> RuntimeEvent:
        if self._terminal_emitted:
            raise EventSequenceError(f"run {self.run_id} already emitted a terminal detail")
        self._sequence += 1
        event = RuntimeEvent(
            schema_version=1,
            event_id=uuid4(),
            run_id=self.run_id,
            session_id=self.session_id,
            sequence=self._sequence,
            timestamp=datetime.now(UTC),
            detail=detail,
        )
        if isinstance(detail, TERMINAL_DETAIL_TYPES):
            self._terminal_emitted = True
        return event
