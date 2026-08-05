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
        call_id = str(uuid4())
        tool_span: Any | None = None

        def finish_trace(*, status: str, output: Mapping[str, Any] | None = None) -> None:
            if tool_span is None:
                return
            try:
                tool_span.finish(phase_status=status, outputs=output)
            except Exception:
                return

        def start_trace(input_value: JsonValue) -> None:
            nonlocal tool_span
            try:
                from fleet_rlm.observability.turn_tracing import start_turn_span

                tool_span = start_turn_span(
                    f"tool.{source.name}",
                    span_type="TOOL",
                    inputs={
                        "tool_name": str(source.name),
                        "tool_call_id": call_id,
                        "input": input_value,
                    },
                )
            except Exception:
                tool_span = None

        if is_authorized is not None and not is_authorized():
            start_trace({})
            observer(ToolFailed(call_id, str(source.name), event_view.error(validation=False)))
            finish_trace(status="failed", output={"tool_status": "failed", "failure_category": "unauthorized"})
            raise RuntimeError("Turn is no longer authorized")
        try:
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
        except TypeError:
            start_trace({})
            observer(ToolStarted(call_id, str(source.name), {}))
            observer(ToolFailed(call_id, str(source.name), event_view.error(validation=True)))
            finish_trace(status="failed", output={"tool_status": "failed", "failure_category": "invalid_arguments"})
            raise

        arguments = dict(bound.arguments)
        projected_input = event_view.input(arguments)
        start_trace(projected_input)
        observer(ToolStarted(call_id, str(source.name), projected_input))
        try:
            validated = validator(**arguments)
        except Exception:
            observer(ToolFailed(call_id, str(source.name), event_view.error(validation=True)))
            finish_trace(status="failed", output={"tool_status": "failed", "failure_category": "invalid_arguments"})
            raise

        try:
            result = source.func(**validated)
            if inspect.isawaitable(result):
                if inspect.iscoroutine(result):
                    result.close()
                raise TypeError("async host tools are not supported inside the synchronous interpreter bridge")
            if after_result is not None:
                after_result(result)
        except Exception as exc:
            if guards is not None:
                guards.failed(str(source.name), arguments)
            observer(ToolFailed(call_id, str(source.name), event_view.error(validation=False, exception=exc)))
            finish_trace(status="failed", output={"tool_status": "failed", "failure_category": "tool_error"})
            raise
        if guards is not None:
            warning = guards.completed(str(source.name), arguments, result)
            if warning is not None:
                observer(WarningEvent(warning, "tool_no_progress"))
                if not event_view.allow_repeated_identical:
                    finish_trace(status="failed", output={"tool_status": "failed", "failure_category": "no_progress"})
                    raise TurnNoProgressError
        projected_output = event_view.output(result)
        observer(ToolCompleted(call_id, str(source.name), projected_output))
        finish_trace(status="completed", output={"tool_status": "completed", "output": projected_output})
        return result

    source_args = source.args or {}
    source_arg_types = source.arg_types or {}

    def permissive_schema(schema: object) -> dict[str, Any]:
        value = dict(schema) if isinstance(schema, Mapping) else {}
        # DSPy skips JSON-schema validation for ``Any`` while retaining all
        # source metadata (defaults, descriptions, and provider-facing fields).
        value["type"] = "Any"
        return value

    permissive_args = {name: permissive_schema(schema) for name, schema in source_args.items()}
    for name in source_arg_types:
        permissive_args.setdefault(name, {"type": "Any"})
    return dspy.Tool(
        wrapped,
        name=source.name,
        desc=source.desc,
        # The final DSPy RLM validates the normalized Tool against ``args``
        # before invoking it. Use unconstrained schemas here so Fleet's
        # source validator below remains the single validation/observation
        # point and can report invalid values publicly.
        args=permissive_args,
        # DSPy 3.3.0 validates the normalized Tool before invoking its
        # function. Keep that outer adapter permissive so Fleet's wrapped
        # validator can emit the public ToolStarted/ToolFailed events and
        # preserve the original source validation contract.
        arg_types={name: Any for name in permissive_args},
        arg_desc=source.arg_desc,
    )
