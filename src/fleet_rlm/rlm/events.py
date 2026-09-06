"""Canonical Runtime Events, tool observation, trajectory projection, and trace assembly.

This module is the P46.3 events entry point. It consolidates event definitions,
observation sessions, tool event interception, trajectory reconciliation,
and execution trace assembly for one native DSPy RLM execution.
"""

from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from functools import wraps
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal, Protocol, TypeAlias, cast
from uuid import UUID, uuid4

import dspy

from fleet_rlm.json_types import JsonValue, validate_json_value
from fleet_rlm.observability.diagnostics import trace_failure_category
from fleet_rlm.observability.tracing import turn_phase_span
from fleet_rlm.rlm.compat_3_3_1 import FleetJSONAdapter, _RLMTraceCallback
from fleet_rlm.rlm.result import (
    ExecutionDetail,
    RLMConfigError,
    RLMUsage,
    RunCancelledError,
    RunNoProgressError,
    TrajectoryStep,
    observed_usage,
    rlm_termination_mode,
    truncate_public_text,
    validate_rlm_usage,
)
from fleet_rlm.tool_events import (
    ToolAfterResult,
    ToolEventView,
    ToolInputProjection,
    ToolOutputProjection,
    bound_event_text,
)

if TYPE_CHECKING:
    from fleet_rlm.rlm.recursion import RecursiveCallSummary, RecursiveRLMExecutor
    from fleet_rlm.rlm.runtime import RLMExecutionContext, RLMWorkerHandle, RunToolGuards


# ---------------------------------------------------------------------------
# Canonical Event Types & Event Recorder
# ---------------------------------------------------------------------------

from typing import ClassVar

from fleet_rlm.json_types import JsonScalar as JsonScalar

RunFailedMessage: TypeAlias = Literal[
    "Turn failed",
    "Provider endpoint not found; check model and base URL",
    "Turn output is invalid",
    "Turn output is too large",
    "Turn could not be prepared",
    "Turn could not be committed",
]

PROVIDER_ENDPOINT_NOT_FOUND_MESSAGE = "Provider endpoint not found; check model and base URL"


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
    trace_id: str | None = None


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
    stream_id: str | None = None
    is_delta: bool = False
    is_final: bool = True


@dataclass(frozen=True, slots=True)
class RLMCode:
    kind: ClassVar[Literal["rlm.code"]] = "rlm.code"
    code: str
    step: int | None = None
    stream_id: str | None = None
    is_delta: bool = False
    is_final: bool = True


@dataclass(frozen=True, slots=True)
class RLMOutput:
    kind: ClassVar[Literal["rlm.output"]] = "rlm.output"
    output: str
    step: int | None = None
    stream_id: str | None = None
    is_delta: bool = False
    is_final: bool = True


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


ObservationDetail: TypeAlias = (
    StepStarted | StepFinished | RLMReasoning | RLMCode | RLMOutput | ToolStarted | ToolCompleted | ToolFailed
)
ObservationObserver: TypeAlias = Callable[[ObservationDetail], None]


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
    value: RLMUsage

    def __post_init__(self) -> None:
        try:
            usage = validate_rlm_usage(self.value)
        except ValueError as exc:
            raise TypeError(str(exc)) from exc
        frozen = _freeze_json(usage)
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
    trace_id: str | None = None


@dataclass(frozen=True, slots=True)
class RunFailed:
    kind: ClassVar[Literal["run.failed"]] = "run.failed"
    code: Literal["preparation_failed", "execution_failed", "commit_failed", "protocol_error", "unavailable"]
    message: RunFailedMessage
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
)

TERMINAL_DETAIL_TYPES = (RunCompleted, RunFailed, RunCancelled, RunTimedOut)


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


# ---------------------------------------------------------------------------
# Tool Observation & Event Views
# ---------------------------------------------------------------------------

ToolObserver: TypeAlias = Callable[[ObservationDetail | Status | WarningEvent], None]


class AsyncToolBridge(Protocol):
    """Composition-owned bridge for awaiting async host Tools from DSPy sync code."""

    def run(self, awaitable: Any) -> Any:
        """Await one host operation on the bridge's persistent event loop."""
        ...


class ToolResultSerializationError(TypeError):
    """Closed failure for a host Tool result outside Fleet's JSON contract."""

    public_message = "Tool result is invalid"

    def __init__(self) -> None:
        super().__init__(self.public_message)


def _validation_tool(source: dspy.Tool) -> dspy.Tool:
    def validate_only(**values: Any) -> dict[str, Any]:
        return values

    return dspy.Tool(
        validate_only,
        name=source.name,
        desc=source.desc,
        args=source.args,
        arg_types=source.arg_types,
        arg_desc=source.arg_desc,
    )


@dataclass(slots=True)
class _ToolTrace:
    source: dspy.Tool
    call_id: str
    span: Any | None = None

    def start(self, input_value: JsonValue) -> None:
        try:
            from fleet_rlm.observability.tracing import start_turn_span

            self.span = start_turn_span(
                f"tool.{self.source.name}",
                span_type="TOOL",
                inputs={
                    "tool_name": str(self.source.name),
                    "tool_call_id": self.call_id,
                    "input": input_value,
                },
            )
        except Exception:
            self.span = None

    def finish(self, *, status: str, output: Mapping[str, Any] | None = None) -> None:
        if self.span is None:
            return
        try:
            self.span.finish(phase_status=status, outputs=output)
        except Exception:
            return


def _reject_unauthorized(
    source: dspy.Tool,
    observer: ToolObserver,
    event_view: ToolEventView,
    trace: _ToolTrace,
    is_authorized: Callable[[], bool] | None,
) -> None:
    if is_authorized is None or is_authorized():
        return
    trace.start({})
    observer(ToolStarted(trace.call_id, str(source.name), {}))
    observer(ToolFailed(trace.call_id, str(source.name), event_view.error(validation=False)))
    trace.finish(status="failed", output={"tool_status": "failed", "failure_category": "unauthorized"})
    raise RuntimeError("Turn is no longer authorized")


def _bind_tool_arguments(
    signature: Any,
    args: tuple[Any, ...],
    kwargs: Mapping[str, Any],
    source: dspy.Tool,
    observer: ToolObserver,
    event_view: ToolEventView,
    trace: _ToolTrace,
) -> dict[str, Any]:
    try:
        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()
    except TypeError:
        trace.start({})
        observer(ToolStarted(trace.call_id, str(source.name), {}))
        observer(ToolFailed(trace.call_id, str(source.name), event_view.error(validation=True)))
        trace.finish(status="failed", output={"tool_status": "failed", "failure_category": "invalid_arguments"})
        raise
    return dict(bound.arguments)


def _validate_tool_arguments(
    validator: dspy.Tool,
    arguments: Mapping[str, Any],
    source: dspy.Tool,
    observer: ToolObserver,
    event_view: ToolEventView,
    trace: _ToolTrace,
) -> dict[str, Any]:
    try:
        return validator(**arguments)
    except Exception:
        observer(ToolFailed(trace.call_id, str(source.name), event_view.error(validation=True)))
        trace.finish(status="failed", output={"tool_status": "failed", "failure_category": "invalid_arguments"})
        raise


def _resolve_awaitable_result(result: Any, *, async_bridge: AsyncToolBridge | None = None) -> Any:
    """Resolve an async host Tool through the composition-owned async bridge.

    Native DSPy invokes interpreter Tools synchronously.  Outside an event loop
    the credential-free direct path can own a short-lived loop; while DSPy is
    running on a loop, the host operation must be submitted to the persistent
    composition loop.  Creating a private loop/thread per Tool would break
    loop-affine host resources and leak work past the Turn ownership boundary.
    """
    if not inspect.isawaitable(result):
        return result

    async def await_result() -> Any:
        return await result

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(await_result())
    if async_bridge is None:
        close = getattr(result, "close", None)
        if callable(close):
            close()
        raise RuntimeError("async Tool requires a persistent async bridge")
    return async_bridge.run(await_result())


def _execute_observed_tool(
    source: dspy.Tool,
    validated: Mapping[str, Any],
    arguments: Mapping[str, Any],
    observer: ToolObserver,
    event_view: ToolEventView,
    trace: _ToolTrace,
    after_result: ToolAfterResult | None,
    guards: RunToolGuards | None,
    async_bridge: AsyncToolBridge | None,
) -> Any:
    try:
        if guards is not None:
            guards.reserve_tool()
        result = _resolve_awaitable_result(source.func(**validated), async_bridge=async_bridge)
        try:
            validate_json_value(result, path=f"Tool {source.name} result")
        except (TypeError, ValueError):
            raise ToolResultSerializationError from None
        if after_result is not None:
            after_result(result)
    except Exception as exc:
        if guards is not None:
            guards.failed(str(source.name), arguments)
        observer(ToolFailed(trace.call_id, str(source.name), event_view.error(validation=False, exception=exc)))
        trace.finish(status="failed", output={"tool_status": "failed", "failure_category": "tool_error"})
        raise
    return result


def _check_tool_progress(
    source: dspy.Tool,
    result: Any,
    arguments: Mapping[str, Any],
    observer: ToolObserver,
    event_view: ToolEventView,
    trace: _ToolTrace,
    guards: RunToolGuards | None,
) -> None:
    if guards is None:
        return
    warning = guards.completed(str(source.name), arguments, result)
    if warning is None:
        return
    observer(WarningEvent(warning, "tool_no_progress"))
    if event_view.allow_repeated_identical:
        return
    # Close the tool observation before failing the turn: the durable turn
    # detail policy rejects a ToolStarted without a terminal observation and
    # would roll back the entire commit (RC-2). The guard warning is a fixed
    # bounded public message, reused here as the failure detail.
    observer(ToolFailed(trace.call_id, str(source.name), bound_event_text(warning)))
    trace.finish(status="failed", output={"tool_status": "failed", "failure_category": "no_progress"})
    raise RunNoProgressError


def _run_observed_tool(
    source: dspy.Tool,
    signature: Any,
    validator: dspy.Tool,
    observer: ToolObserver,
    event_view: ToolEventView,
    after_result: ToolAfterResult | None,
    is_authorized: Callable[[], bool] | None,
    guards: RunToolGuards | None,
    async_bridge: AsyncToolBridge | None,
    args: tuple[Any, ...],
    kwargs: Mapping[str, Any],
) -> Any:
    trace = _ToolTrace(source, str(uuid4()))
    _reject_unauthorized(source, observer, event_view, trace, is_authorized)
    arguments = _bind_tool_arguments(signature, args, kwargs, source, observer, event_view, trace)
    projected_input = event_view.input(arguments)
    trace.start(projected_input)
    observer(ToolStarted(trace.call_id, str(source.name), projected_input))
    validated = _validate_tool_arguments(validator, arguments, source, observer, event_view, trace)
    result = _execute_observed_tool(
        source,
        validated,
        arguments,
        observer,
        event_view,
        trace,
        after_result,
        guards,
        async_bridge,
    )
    _check_tool_progress(source, result, arguments, observer, event_view, trace, guards)
    projected_output = event_view.output(result)
    observer(ToolCompleted(trace.call_id, str(source.name), projected_output))
    trace.finish(status="completed", output={"tool_status": "completed", "output": projected_output})
    return result


def _permissive_schema(schema: object) -> dict[str, Any]:
    value = dict(schema) if isinstance(schema, Mapping) else {}
    # DSPy skips JSON-schema validation for ``Any`` while retaining all
    # source metadata (defaults, descriptions, and provider-facing fields).
    value["type"] = "Any"
    return value


def _permissive_tool_args(source: dspy.Tool) -> dict[str, dict[str, Any]]:
    source_args = source.args or {}
    source_arg_types = source.arg_types or {}
    permissive_args = {name: _permissive_schema(schema) for name, schema in source_args.items()}
    for name in source_arg_types:
        permissive_args.setdefault(name, {"type": "Any"})
    return permissive_args


def observe_tool(
    tool: dspy.Tool,
    observer: ToolObserver,
    event_view: ToolEventView,
    *,
    after_result: ToolAfterResult | None = None,
    is_authorized: Callable[[], bool] | None = None,
    guards: RunToolGuards | None = None,
    async_bridge: AsyncToolBridge | None = None,
) -> dspy.Tool:
    """Return a fresh Tool whose extracted ``func`` preserves DSPy validation."""
    if not isinstance(tool, dspy.Tool):
        raise TypeError("observe_tool requires a dspy.Tool")
    source = tool
    signature = inspect.signature(source.func)
    validator = _validation_tool(source)

    @wraps(source.func)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        return _run_observed_tool(
            source,
            signature,
            validator,
            observer,
            event_view,
            after_result,
            is_authorized,
            guards,
            async_bridge,
            args,
            kwargs,
        )

    permissive_args = _permissive_tool_args(source)
    return dspy.Tool(
        wrapped,
        name=source.name,
        desc=source.desc,
        # The final DSPy RLM validates the normalized Tool against ``args``
        # before invoking it. Use unconstrained schemas here so Fleet's
        # source validator below remains the single validation/observation
        # point and can report invalid values publicly.
        args=permissive_args,
        # DSPy 3.3.x validates the normalized Tool before invoking its
        # function. Keep that outer adapter permissive so Fleet's wrapped
        # validator can emit the public ToolStarted/ToolFailed events and
        # preserve the original source validation contract.
        arg_types={name: Any for name in permissive_args},
        arg_desc=source.arg_desc,
    )


# ---------------------------------------------------------------------------
# Trajectory Projection & Reconciliation
# ---------------------------------------------------------------------------

_StreamDetail = RLMReasoning | RLMCode | RLMOutput


def trajectory_details(steps: Sequence[TrajectoryStep], *, max_chars: int) -> list[ObservationDetail]:
    """Project strictly normalized DSPy trajectory steps into public details."""
    details: list[ObservationDetail] = []
    for step in steps:
        output = step.output
        if output.startswith("FINAL:"):
            output = "FINAL submitted"
        details.extend(
            (
                StepStarted(step.index),
                RLMReasoning(truncate_public_text(step.reasoning, max_len=max_chars), step.index),
                RLMCode(truncate_public_text(step.code, max_len=max_chars), step.index),
                RLMOutput(truncate_public_text(output, max_len=max_chars), step.index),
                StepFinished(step.index),
            )
        )
    return details


def _stream_text(detail: ExecutionDetail) -> str:
    if isinstance(detail, RLMReasoning):
        return detail.text
    if isinstance(detail, RLMCode):
        return detail.code
    if isinstance(detail, RLMOutput):
        return detail.output
    return ""


def _stream_step(detail: ExecutionDetail) -> int | None:
    if isinstance(detail, (RLMReasoning, RLMCode, RLMOutput)):
        return detail.step
    return None


def _stream_id(detail: ExecutionDetail) -> str | None:
    if isinstance(detail, (RLMReasoning, RLMCode, RLMOutput)):
        return detail.stream_id or None
    return None


def _is_delta(detail: ExecutionDetail) -> bool:
    return isinstance(detail, (RLMReasoning, RLMCode, RLMOutput)) and detail.is_delta


def _preserve_stream_id(target: ObservationDetail, details: Sequence[ExecutionDetail], step: int) -> ObservationDetail:
    """Keep one live stream identity when canonical trajectory data is emitted."""
    if not isinstance(target, (RLMReasoning, RLMCode, RLMOutput)):
        return target
    stream_id = next(
        (
            detail.stream_id
            for detail in details
            if isinstance(detail, _StreamDetail)
            and type(detail) is type(target)
            and detail.step == step
            and isinstance(detail.stream_id, str)
            and bool(detail.stream_id)
        ),
        None,
    )
    if stream_id is None:
        return target
    return replace(target, stream_id=stream_id, is_delta=False, is_final=True)


def _align_trajectory_detail(
    details: Sequence[ExecutionDetail],
    target: ObservationDetail,
    *,
    used_positions: set[int],
) -> ObservationDetail:
    """Align canonical text with a live observation when setup consumed a step.

    The interpreter may execute a host context/bootstrap capsule before DSPy's
    first trajectory action.  That setup observation owns an earlier step number
    even though DSPy's canonical trajectory starts at action one.  Matching the
    exact public payload across steps lets reconciliation update the real live
    action rather than emitting a duplicate canonical action stream.
    """
    text = _stream_text(target)
    if not text:
        return target
    for index, detail in enumerate(details):
        if index in used_positions or type(detail) is not type(target) or _stream_text(detail) != text:
            continue
        observed_step = _stream_step(detail)
        target_step = _stream_step(target)
        if isinstance(observed_step, int) and observed_step != target_step:
            target = replace(target, step=observed_step)
        used_positions.add(index)
        return target
    return target


def _same_stream_payload(
    details: Sequence[ExecutionDetail],
    positions: Sequence[int],
    target: ObservationDetail,
) -> bool:
    """Payload identity between live rows and one canonical trajectory detail.

    Live deltas and the canonical full-text trajectory row describe the same
    stream content when (type, step, stream_id, public text) match, ignoring
    ``is_delta``/``is_final`` flag drift (RC-4a). The live public text is the
    in-order stream projection: delta rows concatenate; a non-delta row
    replaces the content accumulated so far.
    """
    text = ""
    stream_id: str | None = None
    target_step = _stream_step(target)
    for position in positions:
        detail = details[position]
        if type(detail) is not type(target) or _stream_step(detail) != target_step:
            return False
        row_stream_id = _stream_id(detail)
        if stream_id is None:
            stream_id = row_stream_id
        elif row_stream_id is not None and row_stream_id != stream_id:
            return False
        value = _stream_text(detail)
        text = text + value if _is_delta(detail) else value
    return stream_id == _stream_id(target) and text == _stream_text(target)


def _detail_position(details: Sequence[ExecutionDetail], detail_type: type[object], step: int) -> int | None:
    return next(
        (
            index
            for index, detail in enumerate(details)
            if isinstance(detail, detail_type) and getattr(detail, "step", None) == step
        ),
        None,
    )


def _trajectory_insertion(details: Sequence[ExecutionDetail], target: ObservationDetail, step: int, finish: int) -> int:
    if isinstance(target, RLMReasoning):
        start = _detail_position(details, StepStarted, step)
        assert start is not None
        return start + 1
    if isinstance(target, RLMCode):
        reasoning = _detail_position(details, RLMReasoning, step)
        if reasoning is not None:
            return reasoning + 1
        start = _detail_position(details, StepStarted, step)
        assert start is not None
        return start + 1
    return finish


def _missing_step_insertion(details: Sequence[ExecutionDetail], step: int) -> int:
    """Place a missing canonical step before the next live step."""
    return next(
        (index for index, detail in enumerate(details) if isinstance(detail, StepStarted) and detail.step > step),
        len(details),
    )


def has_reasoning(details: Sequence[ExecutionDetail], text: str, max_chars: int) -> bool:
    """True when durable details already contain this truncated public reasoning."""
    return any(
        isinstance(detail, RLMReasoning) and truncate_public_text(detail.text, max_len=max_chars) == text
        for detail in details
    )


def reconcile_trajectory(
    details: list[ExecutionDetail],
    trajectory: Sequence[TrajectoryStep],
    *,
    max_chars: int,
) -> list[ObservationDetail]:
    """Reconcile completed DSPy trajectory details with live observations.

    Observations with an identical public payload (type, step, stream_id, and
    projected text) keep their position: the durable row is upserted to the
    canonical flags without any re-emission. A differing same-step RLM detail
    is replaced in the durable list and re-emitted with the same stable step
    ID so live TUI projection upserts it rather than appending a second card.

    Step-marker positions are indexed once and maintained under local
    insert/delete shifts instead of re-scanning the list per step (P33: one
    derivation per bounded collection).
    """
    step_starts: dict[int, int] = {}
    step_finishes: dict[int, int] = {}
    reasoning_first: dict[int, int] = {}
    for index, detail in enumerate(details):
        if isinstance(detail, StepStarted):
            step_starts.setdefault(detail.step, index)
        elif isinstance(detail, StepFinished):
            step_finishes.setdefault(detail.step, index)
        elif isinstance(detail, RLMReasoning):
            reasoning_step = _stream_step(detail)
            if reasoning_step is not None:
                reasoning_first.setdefault(reasoning_step, index)

    def shift_positions(removed_asc: Sequence[int], *, inserted_at: int | None = None) -> None:
        """Keep marker maps exact across local deletions/insertions."""
        if not removed_asc and inserted_at is None:
            return
        for mapping in (step_starts, step_finishes, reasoning_first):
            for step_index, position in list(mapping.items()):
                adjusted = position - sum(1 for removed in removed_asc if removed < position)
                if inserted_at is not None and adjusted >= inserted_at:
                    adjusted += 1
                mapping[step_index] = adjusted

    emissions: list[ObservationDetail] = []
    aligned_positions: set[int] = set()
    for trajectory_step in trajectory:
        step = trajectory_step.index
        step_details = trajectory_details((trajectory_step,), max_chars=max_chars)
        start = step_starts.get(step)
        finish = step_finishes.get(step)
        if start is None or finish is None or start >= finish:
            insertion = _missing_step_insertion(details, step)
            for detail in step_details:
                details.insert(insertion, detail)
                shift_positions((), inserted_at=insertion)
                insertion += 1
            emissions.extend(step_details)
            continue

        canonical = step_details[1:-1]
        for raw_target in canonical:
            target = _align_trajectory_detail(details, raw_target, used_positions=aligned_positions)
            target_step = _stream_step(target)
            target = _preserve_stream_id(target, details, target_step if isinstance(target_step, int) else step)
            target_step = _stream_step(target)
            if isinstance(target_step, int) and target_step != step:
                aligned_start = step_starts.get(target_step)
                aligned_finish = step_finishes.get(target_step)
                if aligned_start is not None and aligned_finish is not None and aligned_start < aligned_finish:
                    step = target_step
                    start, finish = aligned_start, aligned_finish
            target_type = type(target)
            existing_positions = [
                index
                for index in range(start + 1, finish)
                if isinstance(details[index], target_type) and _stream_step(details[index]) == step
            ]
            if existing_positions:
                first = existing_positions[0]
                # Identical public payload upserts the canonical flags
                # (is_delta=False, is_final=True) into the durable row without
                # re-emitting already-delivered content; a true correction is
                # still re-emitted so the TUI upserts the same stream.
                if not _same_stream_payload(details, existing_positions, target):
                    emissions.append(target)
                details[first] = target
                removed = existing_positions[1:]
                for duplicate in reversed(removed):
                    del details[duplicate]
                shift_positions(sorted(removed))
                start = step_starts[step]
                finish = step_finishes[step]
                assert start < finish
                continue

            # Live observation may publish reasoning before interpreter StepStarted.
            if isinstance(target, RLMReasoning):
                outside = reasoning_first.get(step)
                if outside is not None:
                    if not _same_stream_payload(details, (outside,), target):
                        emissions.append(target)
                    details[outside] = target
                    continue
            insertion = _trajectory_insertion(details, target, step, finish)
            details.insert(insertion, target)
            emissions.append(target)
            shift_positions((), inserted_at=insertion)
            start = step_starts[step]
            finish = step_finishes[step]
            assert start < finish
    return emissions


__all__ = [
    "has_reasoning",
    "reconcile_trajectory",
    "trajectory_details",
]


# ---------------------------------------------------------------------------
# Trace Assembly & Invocation
# ---------------------------------------------------------------------------


async def invoke_native_rlm(
    rlm: Any,
    context: RLMExecutionContext,
    kwargs: Mapping[str, Any],
) -> Any:
    """Invoke the RLM operation using the caller-owned interpreter when required."""
    native_call_args: tuple[Any, ...] = ()
    if type(rlm) is dspy.RLM:
        if context.execution.interpreter is None:
            raise RLMConfigError("native RLM execution requires a caller-owned interpreter")
        native_call_args = (context.execution.interpreter,)
    return await rlm.acall(*native_call_args, **dict(kwargs))


def recursive_summary(executor: RecursiveRLMExecutor | None, metrics: Any | None = None) -> RecursiveCallSummary:
    """
    Summarize recursive execution metrics for an executor or metrics collector.

    Parameters:
        executor (RecursiveRLMExecutor | None): Executor providing recursive metrics, if available.
        metrics (Any | None): Optional metrics collector used when no executor is available.

    Returns:
        RecursiveCallSummary: Recursive execution metrics, a snapshot-derived
            summary, or zero-valued metrics when no source is available.
    """
    from fleet_rlm.rlm.recursion import RecursiveCallSummary

    if executor is not None:
        return executor.summary()
    if metrics is not None and callable(getattr(metrics, "snapshot", None)):
        snapshot = metrics.snapshot()
        return RecursiveCallSummary.from_snapshot(snapshot, depth_fallback_count=snapshot.depth_fallback_calls)
    return RecursiveCallSummary(0, 0, 0, 0, 0, ())


def record_phase_failure(
    phase: Any,
    started: float,
    recursive_executor: RecursiveRLMExecutor | None,
    metrics: Any,
    exc: BaseException,
    *,
    last_lm_call: Mapping[str, object] | None = None,
    wrap_up: Mapping[str, object] | None = None,
) -> None:
    """
    Record failure status, timing, recursive-call statistics, and delegation metrics for a trace phase.

    Parameters:
        phase (Any): Trace phase receiving the failure outputs.
        started (float): Monotonic timestamp captured when the phase started.
        recursive_executor (RecursiveRLMExecutor | None): Recursive executor associated with the phase.
        metrics (Any): Metrics snapshot used when recursive execution is unavailable.
        exc (BaseException): Exception that caused the phase to fail.
        last_lm_call (Mapping[str, object] | None): Optional details of the most recent language-model call.
        wrap_up (Mapping[str, object] | None): Bounded final-answer reserve diagnostics from the Root adapter.
    """
    summary = recursive_summary(recursive_executor, metrics)
    outputs: dict[str, object] = {
        "elapsed_ms": int((time.perf_counter() - started) * 1000),
        "request_status": "failed",
        "failure_category": trace_failure_category(exc),
        "recursive_call_count": summary.call_count,
        "recursive_prompt_chars": summary.delegated_prompt_chars,
        "recursive_depth_fallback_count": summary.depth_fallback_count,
        "delegation_metrics": summary.delegation_metrics.as_dict(),
        "token_usage_status": summary.delegation_metrics.token_usage_status,
    }
    if last_lm_call:
        outputs["last_lm_call"] = dict(last_lm_call)
    if wrap_up:
        outputs.update(dict(wrap_up))
    output_diag = getattr(exc, "output_chars", None)
    if isinstance(output_diag, int):
        outputs["output_diagnostic"] = {
            "output_chars": output_diag,
            "output_preview": getattr(exc, "output_preview", None),
        }
    phase.set_outputs(outputs)


def record_phase_success(
    phase: Any,
    prediction: Any,
    started: float,
    recursive_executor: RecursiveRLMExecutor | None,
    metrics: Any,
    *,
    wrap_up: Mapping[str, object] | None = None,
) -> Any:
    """
    Record successful completion details and recursive delegation metrics for a trace phase.

    Parameters:
        phase (Any): Trace phase whose outputs are updated.
        prediction (Any): Completed RLM prediction used to derive usage and termination details.
        started (float): Monotonic start time used to calculate elapsed duration.
        recursive_executor (RecursiveRLMExecutor | None): Executor providing recursive-call metrics.
        metrics (Any): Execution metrics used when recursive metrics are unavailable.
        wrap_up (Mapping[str, object] | None): Bounded final-answer reserve diagnostics from the Root adapter.

    Returns:
        Any: The original prediction.
    """
    termination_mode = rlm_termination_mode(prediction)
    usage = observed_usage(prediction, duration_ms=int((time.perf_counter() - started) * 1000))
    summary = recursive_summary(recursive_executor, metrics)
    # Token telemetry is truthful: "observed" only when a Prediction carries
    # normalized token fields or an LM callback actually saw token usage;
    # "unavailable" otherwise. A cost-only or cache-only usage mapping reports
    # "unavailable" rather than a misleading zero-token "observed". Never an
    # estimate, and an all-zero total still counts as observed.
    from fleet_rlm.rlm.recursion import normalize_lm_token_usage

    prediction_has_tokens = any(normalize_lm_token_usage(entry) for entry in usage["observed_lm_usage"].values())
    token_usage_status = "observed" if prediction_has_tokens else summary.delegation_metrics.token_usage_status
    outputs: dict[str, object] = {
        "iterations": usage["iterations"],
        "observed_lm_usage": usage["observed_lm_usage"],
        "termination_mode": termination_mode,
        "elapsed_ms": usage["duration_ms"],
        "request_status": "completed",
        "recursive_call_count": summary.call_count,
        "recursive_prompt_chars": summary.delegated_prompt_chars,
        "recursive_depth_fallback_count": summary.depth_fallback_count,
        "delegation_metrics": summary.delegation_metrics.as_dict(),
        "token_usage_status": token_usage_status,
    }
    if wrap_up:
        outputs.update(dict(wrap_up))
    phase.set_outputs(outputs)
    return prediction


@dataclass(slots=True)
class ExecutionTraceAssembler:
    """Own the trace phase, DSPy context, and execution-metric projection."""

    recursive_executor: RecursiveRLMExecutor | None

    async def execute(
        self,
        rlm: Any,
        context: RLMExecutionContext,
        kwargs: Mapping[str, Any],
    ) -> Any:
        """
        Execute one native RLM invocation within a traced turn phase.

        Parameters:
                rlm (Any): The native RLM instance to invoke.
                context (RLMExecutionContext): Execution settings, models, delegation metrics, and interpreter state.
                kwargs (Mapping[str, Any]): Keyword arguments passed to the RLM invocation.

        Returns:
                Any: The RLM prediction.
        """
        started = time.perf_counter()
        trace_callback = _RLMTraceCallback(
            root_lm=context.execution.models.root_lm,
            sub_lm=context.execution.models.sub_lm,
            metrics=context.delegation.metrics,
            deadline=context.execution.deadline,
        )
        adapter = FleetJSONAdapter(
            deadline=context.execution.deadline,
            wrap_up_seconds=context.execution.wrap_up_seconds,
            budget=getattr(context.execution.models, "budget", None),
        )
        with (
            turn_phase_span(
                "RLM.execute",
                inputs={
                    "max_iters": context.execution.options.max_iters,
                    "max_llm_calls": context.execution.options.max_llm_calls,
                    "max_output_chars": context.execution.options.max_output_chars,
                },
            ) as phase,
            dspy.context(
                lm=context.execution.models.root_lm,
                # DSPy 3.3.x combines context callbacks with instance callbacks
                # around LM requests (dspy/utils/callback.py:258-288).
                callbacks=[trace_callback],
                # Keep the pinned DSPy JSON action protocol authoritative. A
                # provider-native token stream is an adapter failure, not a
                # second grammar that Fleet should reinterpret. FleetJSONAdapter
                # adds only the bounded corrective re-ask, so one empty or
                # unparseable action response cannot discard the whole Turn.
                adapter=adapter,
                track_usage=True,
            ),
        ):
            try:
                prediction = await invoke_native_rlm(rlm, context, kwargs)
                if self.recursive_executor is not None:
                    self.recursive_executor.raise_if_cleanup_failed()
            except BaseException as exc:
                record_phase_failure(
                    phase,
                    started,
                    self.recursive_executor,
                    context.delegation.metrics,
                    exc,
                    last_lm_call=trace_callback.last_call_summary(),
                    wrap_up=adapter.wrap_up_summary(),
                )
                raise
            finally:
                self._record_attachment_accesses(context)
            return record_phase_success(
                phase,
                prediction,
                started,
                self.recursive_executor,
                context.delegation.metrics,
                wrap_up=adapter.wrap_up_summary(),
            )

    @staticmethod
    def _record_attachment_accesses(context: RLMExecutionContext) -> None:
        """Record interpreter attachment accesses in the execution capabilities when supported."""
        drain_accesses = getattr(context.execution.interpreter, "drain_context_accesses", None)
        record_accesses = getattr(context.capabilities, "record_attachment_accesses", None)
        if callable(drain_accesses) and callable(record_accesses):
            record_accesses(tuple(drain_accesses()))


# ---------------------------------------------------------------------------
# Observation Session
# ---------------------------------------------------------------------------

MAX_DETAIL_EVENTS = 1024
_RETAINED_DETAIL_TYPES = (
    SkillActivated,
    SkillLoaded,
    StepStarted,
    StepFinished,
    ToolStarted,
    ToolCompleted,
    ToolFailed,
)


class DetailRelay:
    """Thread-safe bounded relay retaining lifecycle-critical details."""

    def __init__(self, *, maxsize: int = MAX_DETAIL_EVENTS) -> None:
        """
        Initialize a bounded relay for runtime event details.

        Parameters:
            maxsize (int): Maximum number of ordinary details to retain. Values
                below zero are treated as zero; lifecycle-critical details remain
                retainable.
        """
        try:
            self._loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None
        # Step and Tool lifecycle are durable protocol signals, not optional
        # diagnostic detail. Keep them even while normal observation traffic is capped.
        self._queue: asyncio.Queue[RuntimeEventDetail] = asyncio.Queue()
        self._maxsize = max(0, maxsize)
        self._ordinary_count = 0
        self.overflowed = False

    def publish(self, detail: RuntimeEventDetail) -> None:
        """Publish a runtime detail to the relay from the current or another thread."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if self._loop is None and loop is not None:
            self._loop = loop
        if loop is not None and loop is self._loop:
            self._put(detail)
        elif self._loop is not None and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._put, detail)
        else:
            self._put(detail)

    def _put(self, detail: RuntimeEventDetail) -> None:
        if self._is_retained(detail):
            self._queue.put_nowait(detail)
            return
        if self._ordinary_count >= self._maxsize:
            self.overflowed = True
            return
        self._ordinary_count += 1
        self._queue.put_nowait(detail)

    @staticmethod
    def _is_retained(detail: RuntimeEventDetail) -> bool:
        """Determine whether a runtime detail is retained regardless of the ordinary event capacity."""
        return isinstance(detail, _RETAINED_DETAIL_TYPES)

    async def get(self) -> RuntimeEventDetail:
        """Retrieve the next retained runtime event detail.

        Returns:
                RuntimeEventDetail: The next available runtime event detail.
        """
        detail = await self._queue.get()
        if not self._is_retained(detail):
            self._ordinary_count -= 1
        return detail

    def drain(self) -> list[RuntimeEventDetail]:
        """
        Remove and return all currently queued runtime event details.

        Returns:
            list[RuntimeEventDetail]: The queued details in queue order.
        """
        values: list[RuntimeEventDetail] = []
        while True:
            try:
                detail = self._queue.get_nowait()
                if not self._is_retained(detail):
                    self._ordinary_count -= 1
                values.append(detail)
            except asyncio.QueueEmpty:
                return values


class WorkerMonitor:
    """Bound polling, cancellation, and deadline policy for one worker."""

    def __init__(
        self,
        worker: RLMWorkerHandle[Any],
        relay: DetailRelay,
        context: RLMExecutionContext,
        drain_capabilities: Callable[[], tuple[ExecutionDetail, ...]],
    ) -> None:
        """Initialize monitoring state for a worker execution.

        Parameters:
                worker: The worker whose execution is monitored.
                relay: The relay that provides runtime details.
                context: The execution context containing cancellation and deadline state.
                drain_capabilities: A callback that retrieves pending capability-generated details.
        """
        self.worker = worker
        self.relay = relay
        self.context = context
        self.drain_capabilities = drain_capabilities
        self.intended_stop: BaseException | None = None
        self.caller_cancelled = False

    async def stream(self) -> AsyncIterator[RuntimeEventDetail]:
        """Stream runtime event details while monitoring worker completion, cancellation, and deadlines.

        Yields:
            RuntimeEventDetail: A detail emitted by the worker or its capabilities.
        """
        pending: asyncio.Task[RuntimeEventDetail] | None = None
        completion = asyncio.create_task(self.worker.wait_until_done(), name="fleet-rlm-worker-completion")
        try:
            while not self.worker.done():
                if await self.context.execution.cancellation_requested():
                    self.intended_stop = RunCancelledError()
                    break
                remaining = self.context.execution.deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    self.intended_stop = TimeoutError()
                    break
                pending = asyncio.create_task(self.relay.get())
                done, _ = await asyncio.wait(
                    {completion, pending},
                    timeout=min(remaining, 0.25),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if pending in done:
                    yield pending.result()
                else:
                    pending.cancel()
                    await asyncio.gather(pending, return_exceptions=True)
                pending = None
                for detail in self.drain_capabilities():
                    yield detail
        except (GeneratorExit, asyncio.CancelledError):
            self.caller_cancelled = True
        finally:
            if pending is not None and not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
            if not completion.done():
                completion.cancel()
                await asyncio.gather(completion, return_exceptions=True)
            if self.intended_stop is None and not self.caller_cancelled:
                self.caller_cancelled |= await self.worker.settle_after_caller_cancellation()

    def raise_if_stopped(self) -> None:
        """
        Raise the exception associated with a stopped worker.

        Raises:
            asyncio.CancelledError: If the caller cancelled the observation.
            BaseException: The worker's intended stop exception, when one was recorded.
        """
        if self.caller_cancelled:
            self.worker.consume_exception()
            raise asyncio.CancelledError
        if self.intended_stop is not None:
            self.worker.consume_exception()
            intended_stop = self.intended_stop
            if isinstance(intended_stop, BaseException):
                raise intended_stop


class ObservationSession:
    """Project worker details into recorded Runtime Events and bounded outcome details."""

    def __init__(self, run_id: UUID, session_id: UUID, *, maxsize: int = MAX_DETAIL_EVENTS) -> None:
        """Initialize an observation session for a run and session.

        Parameters:
                run_id (UUID): Identifier of the run associated with the session.
                session_id (UUID): Identifier of the session associated with the run.
                maxsize (int): Maximum number of ordinary execution details retained by the session.
        """
        self._recorder = EventRecorder(run_id, session_id)
        self._relay = DetailRelay(maxsize=maxsize)
        self._details: list[ExecutionDetail] = []
        self._pending_step_details: list[RuntimeEventDetail] = []
        self._reasoning_steps: set[int] = set()

    @property
    def details(self) -> list[ExecutionDetail]:
        """
        Accesses the execution details collected during the observation session.

        Returns:
                list[ExecutionDetail]: The collected execution details.
        """
        return self._details

    @property
    def overflowed(self) -> bool:
        """Indicates whether the relay dropped ordinary details after reaching its capacity.

        Returns:
                bool: `True` if ordinary details were dropped, `False` otherwise.
        """
        return self._relay.overflowed

    def publish(self, detail: RuntimeEventDetail) -> None:
        """Publish an interpreter/tool detail from either the host or worker thread."""
        self._relay.publish(detail)

    def record(self, detail: RuntimeEventDetail) -> RuntimeEvent:
        """
        Record a runtime event and retain non-status details for the execution outcome.

        Parameters:
                detail (RuntimeEventDetail): Runtime detail to record.

        Returns:
                RuntimeEvent: The recorded runtime event.
        """
        if not isinstance(detail, Status):
            self._details.append(cast(ExecutionDetail, detail))
        return self._recorder.record(detail)

    def record_event(self, detail: RuntimeEventDetail) -> RuntimeEvent:
        """Record a stream envelope without treating it as execution detail."""
        return self._recorder.record(detail)

    def _order_live_detail(self, detail: RuntimeEventDetail) -> tuple[RuntimeEventDetail, ...]:
        """Hold step output until its parsed reasoning is ready for publication.

        Daytona execution callbacks and DSPy callbacks cross the worker/event-loop
        boundary independently.  The callbacks can therefore arrive in reverse
        scheduling order even though DSPy parsed the action before executing it.
        Buffering only the step-scoped details restores the public contract
        ``reasoning -> code -> output -> finish`` without mirroring DSPy history.
        """
        if isinstance(detail, RLMReasoning):
            step = detail.step
            if step is None:
                return (detail,)
            self._reasoning_steps.add(step)
            released = [detail]
            retained: list[RuntimeEventDetail] = []
            for pending in self._pending_step_details:
                if getattr(pending, "step", None) == step:
                    released.append(pending)
                else:
                    retained.append(pending)
            self._pending_step_details = retained
            return tuple(released)
        if isinstance(detail, (RLMCode, RLMOutput, StepFinished)):
            step = getattr(detail, "step", None)
            if step is not None and step not in self._reasoning_steps:
                self._pending_step_details.append(detail)
                return ()
        return (detail,)

    def _flush_pending_step_details(self) -> tuple[RuntimeEventDetail, ...]:
        pending = tuple(self._pending_step_details)
        self._pending_step_details.clear()
        return pending

    async def stream_worker(
        self,
        worker: RLMWorkerHandle[Any],
        context: RLMExecutionContext,
        drain_capabilities: Callable[[], tuple[ExecutionDetail, ...]],
    ) -> AsyncIterator[RuntimeEvent]:
        """Yield live worker observations, final drain details, and overflow warning."""
        monitor = WorkerMonitor(worker, self._relay, context, drain_capabilities)
        async for detail in monitor.stream():
            for ordered in self._order_live_detail(detail):
                yield self.record(ordered)
        for detail in (*drain_capabilities(), *self._relay.drain()):
            for ordered in self._order_live_detail(detail):
                yield self.record(ordered)
        for detail in self._flush_pending_step_details():
            yield self.record(detail)
        if self._relay.overflowed:
            yield self.record(WarningEvent("some detailed execution events were omitted"))
        monitor.raise_if_stopped()


__all__ = [
    "PROVIDER_ENDPOINT_NOT_FOUND_MESSAGE",
    "ArtifactCreated",
    "AsyncToolBridge",
    "AttachmentRead",
    "DetailRelay",
    "EventRecorder",
    "EventSequenceError",
    "ExecutionTraceAssembler",
    "ObservationDetail",
    "ObservationSession",
    "RLMCode",
    "RLMOutput",
    "RLMReasoning",
    "RunCancelled",
    "RunCompleted",
    "RunFailed",
    "RunStarted",
    "RunTimedOut",
    "RuntimeEvent",
    "RuntimeEventDetail",
    "SkillActivated",
    "SkillLoaded",
    "Status",
    "StepFinished",
    "StepStarted",
    "StructuredResult",
    "TextCompleted",
    "TextDelta",
    "ToolCompleted",
    "ToolEventView",
    "ToolFailed",
    "ToolInputProjection",
    "ToolObserver",
    "ToolOutputProjection",
    "ToolResultSerializationError",
    "ToolStarted",
    "Usage",
    "WarningEvent",
    "WorkerMonitor",
    "bound_event_text",
    "has_reasoning",
    "invoke_native_rlm",
    "observe_tool",
    "reconcile_trajectory",
    "record_phase_failure",
    "record_phase_success",
    "recursive_summary",
    "trajectory_details",
]
