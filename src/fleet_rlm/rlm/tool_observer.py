"""Observe validated synchronous host Tools without changing DSPy internals."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import wraps
from typing import Any, TypeAlias
from uuid import uuid4

import dspy

from fleet_rlm.rlm.errors import TurnNoProgressError
from fleet_rlm.rlm.events import (
    JsonValue,
    ObservationDetail,
    Status,
    ToolCompleted,
    ToolFailed,
    ToolStarted,
    WarningEvent,
)
from fleet_rlm.rlm.tool_guards import TurnToolGuards

ToolInputProjection = Callable[[Mapping[str, Any]], JsonValue]
ToolOutputProjection = Callable[[Any], JsonValue]
ToolAfterResult = Callable[[Any], None]
ToolObserver: TypeAlias = Callable[[ObservationDetail | Status | WarningEvent], None]


def _empty_input(_arguments: Mapping[str, Any]) -> JsonValue:
    """Return an empty JSON object for any argument mapping."""
    return {}


def _empty_output(_result: Any) -> JsonValue:
    return {}


def bound_event_text(value: object, *, max_chars: int = 256) -> str:
    """Bound one allowlisted structural text value without rewriting its content."""
    limit = max(4, int(max_chars))
    text = str(value or "")
    return text if len(text) <= limit else text[: limit - 3] + "..."


@dataclass(frozen=True, slots=True)
class ToolEventView:
    """Host-owned, fail-closed public projection for one Tool."""

    input_projection: ToolInputProjection = _empty_input
    output_projection: ToolOutputProjection = _empty_output
    allow_repeated_identical: bool = False

    @classmethod
    def metadata_only(cls) -> ToolEventView:
        return cls()

    def input(self, arguments: Mapping[str, Any]) -> JsonValue:
        try:
            return self.input_projection(arguments)
        except Exception:
            return {}

    def output(self, result: Any) -> JsonValue:
        try:
            return self.output_projection(result)
        except Exception:
            return {}

    def error(self, *, validation: bool, exception: BaseException | None = None) -> str:
        public_message = getattr(exception, "public_message", None)
        if isinstance(public_message, str) and public_message:
            return public_message
        return "Tool arguments are invalid" if validation else "Tool failed"


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
            from fleet_rlm.observability.turn_tracing import start_turn_span

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


def _reject_awaitable_result(result: Any) -> None:
    if not inspect.isawaitable(result):
        return
    if inspect.iscoroutine(result):
        result.close()
    raise TypeError("async host tools are not supported inside the synchronous interpreter bridge")


def _execute_observed_tool(
    source: dspy.Tool,
    validated: Mapping[str, Any],
    arguments: Mapping[str, Any],
    observer: ToolObserver,
    event_view: ToolEventView,
    trace: _ToolTrace,
    after_result: ToolAfterResult | None,
    guards: TurnToolGuards | None,
) -> Any:
    try:
        result = source.func(**validated)
        _reject_awaitable_result(result)
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
    guards: TurnToolGuards | None,
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
    raise TurnNoProgressError


def _run_observed_tool(
    source: dspy.Tool,
    signature: Any,
    validator: dspy.Tool,
    observer: ToolObserver,
    event_view: ToolEventView,
    after_result: ToolAfterResult | None,
    is_authorized: Callable[[], bool] | None,
    guards: TurnToolGuards | None,
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
    guards: TurnToolGuards | None = None,
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
