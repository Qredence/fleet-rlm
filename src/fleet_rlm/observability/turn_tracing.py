"""Fail-soft per-Turn MLflow root spans for engineering observability.

Must never affect Turn outcomes. When disabled or when MLflow is unavailable,
``turn_trace`` yields a no-op handle with ``trace_id=None``.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

logger = logging.getLogger(__name__)

_MAX_TRACE_TEXT_CHARS = 1_000


def _trace_value(value: object) -> object:
    """Return bounded, redacted values safe for engineering traces."""
    if isinstance(value, str):
        from fleet_rlm.rlm.sanitize import sanitize_public_text

        return sanitize_public_text(value, max_len=_MAX_TRACE_TEXT_CHARS)
    if isinstance(value, Mapping):
        return {str(key): _trace_value(item) for key, item in list(value.items())[:32]}
    if isinstance(value, (list, tuple)):
        return [_trace_value(item) for item in value[:32]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return type(value).__name__


def _trace_mapping(values: Mapping[str, object]) -> dict[str, object]:
    return {str(key): _trace_value(item) for key, item in list(values.items())[:32]}


_LOCAL_BYOK_USER = "fleet-local"
_SPAN_NAME = "fleet_turn"
_current_trace_id: ContextVar[str | None] = ContextVar("fleet_mlflow_trace_id", default=None)
# True only while a fleet_turn root span is open. Phase spans gate on this so
# tracing-disabled turns never import or touch MLflow at all.
_fleet_trace_active: ContextVar[bool] = ContextVar("fleet_turn_trace_active", default=False)


@dataclass(frozen=True, slots=True)
class TraceHandle:
    """Public-safe handle for an optional active Turn trace."""

    trace_id: str | None


def annotate_trace_io(
    *,
    request: str,
    response_text: str | None = None,
    response_outputs: dict[str, object] | None = None,
    failed: bool = False,
) -> None:
    """Fail-soft: propagate request/response to the active root trace for MLflow judges.

    MLflow LLM judges (Safety, Completeness, RelevanceToQuery) read from the
    root span's inputs/outputs. Without this, judges either fail or fall back
    to expensive trace-based parsing of all spans.

    Uses span.set_inputs()/set_outputs() on the current active span (which is
    the fleet_turn root span when called from TurnCoordinator._execute_traced).
    When ``failed`` is true, also mark the root span ``ERROR`` so swallowed Turn
    failures (outcome-based, not raised) are not reported as ``OK``.

    Must never raise — trace annotation failures are not Turn failures.
    """
    try:
        import mlflow

        span = mlflow.get_current_active_span()
        if span is None:
            return

        span.set_inputs({"request": _trace_value(request)})

        response: dict[str, object] = {}
        if response_text is not None:
            response["answer"] = _trace_value(response_text)
        if response_outputs is not None:
            for key in ("answer", "final_reasoning"):
                if key in response_outputs:
                    response[key] = _trace_value(response_outputs[key])

        span.set_outputs(response or {"answer": response_text or ""})
        if failed:
            try:
                span.set_status("ERROR")
            except Exception:
                logger.debug("annotate_trace_io status update failed; continuing", exc_info=True)
    except Exception:
        logger.debug("annotate_trace_io failed; continuing without root span I/O", exc_info=True)


def current_turn_trace_id() -> str | None:
    """Return the active Turn trace id for this context, if any."""
    return _current_trace_id.get()


@dataclass(slots=True)
class TraceSpanHandle:
    """Fail-soft lifecycle handle for a bounded nested MLflow span.

    The handle supports callbacks whose start and end hooks are separate
    invocations. It never exposes raw exception details to MLflow and never
    lets tracing failures affect the owning Turn.
    """

    _span_context: Any | None = None
    _span: Any | None = None
    outputs: dict[str, object] = field(default_factory=dict)
    _closed: bool = False

    def set_outputs(self, outputs: Mapping[str, object]) -> None:
        try:
            self.outputs.update(dict(outputs))
        except Exception:
            logger.debug("trace span output accumulation failed; continuing", exc_info=True)

    def finish(
        self,
        *,
        phase_status: str,
        outputs: Mapping[str, object] | None = None,
        attributes: Mapping[str, object] | None = None,
    ) -> None:
        """Close the span with bounded outputs and a sanitized status."""
        if self._closed:
            return
        self._closed = True
        if outputs is not None:
            self.set_outputs(outputs)
        if self._span is None or self._span_context is None:
            return
        try:
            self._span.set_outputs({**_trace_mapping(self.outputs), "phase_status": phase_status})
        except Exception:
            logger.debug("trace span output annotation failed; continuing", exc_info=True)
        if attributes:
            try:
                setter = getattr(self._span, "set_attributes", None)
                if callable(setter):
                    setter(_trace_mapping(attributes))
            except Exception:
                logger.debug("trace span attribute annotation failed; continuing", exc_info=True)
        if phase_status != "completed":
            try:
                self._span.set_status("ERROR")
            except Exception:
                logger.debug("trace span status annotation failed; continuing", exc_info=True)
        try:
            # Do not pass provider exceptions to MLflow: their messages can
            # contain prompts, generated code, or gateway response bodies.
            self._span_context.__exit__(None, None, None)
        except BaseException:
            logger.debug("trace span close failed; continuing", exc_info=True)


# Kept as an alias for the existing phase-span test and call-site vocabulary.
PhaseSpanHandle = TraceSpanHandle


def start_turn_span(
    name: str,
    *,
    inputs: Mapping[str, object],
    span_type: str = "CHAIN",
) -> TraceSpanHandle:
    """Start a bounded nested span when a ``fleet_turn`` trace is active.

    MLflow's manual span API is used because DSPy callback start/end hooks are
    separate events and cannot be represented by a single ``with`` body.
    """
    handle = TraceSpanHandle()
    if not _fleet_trace_active.get():
        return handle

    try:
        import mlflow
        from mlflow.entities import SpanType

        active_span = mlflow.get_current_active_span()
        if active_span is None:
            return handle
        span_context = mlflow.start_span(
            name=name,
            span_type=getattr(SpanType, span_type, SpanType.CHAIN),
        )
        span = span_context.__enter__()
    except Exception:
        logger.debug("MLflow lifecycle span setup failed; continuing", exc_info=True)
        return handle

    handle._span_context = span_context
    handle._span = span
    try:
        span.set_inputs(_trace_mapping(inputs))
    except Exception:
        logger.debug("trace span input annotation failed; continuing", exc_info=True)
    return handle


@contextmanager
def turn_phase_span(name: str, *, inputs: Mapping[str, object]) -> Iterator[PhaseSpanHandle]:
    """Record one bounded, nested Turn phase without affecting its outcome.

    The caller supplies bounded operational metadata and already-redacted
    previews when step-level debugging needs them. Full prompts, generated
    programs, and interpreter output must never be attached. Yields a
    ``PhaseSpanHandle`` so callers can attach bounded outputs at exit time.
    Outside an active ``fleet_turn`` trace this is a no-op that never imports
    MLflow, keeping tracing-disabled turns free of any MLflow footprint.
    """
    handle = start_turn_span(name, inputs=inputs)
    try:
        yield handle
    except BaseException:
        handle.finish(phase_status="failed")
        raise
    else:
        handle.finish(phase_status="completed")


@contextmanager
def turn_trace(
    session_id: UUID,
    run_id: UUID,
    *,
    enabled: bool,
    expose_trace_id: bool = True,
) -> Iterator[TraceHandle]:
    """Open a root ``fleet_turn`` span for one live Turn, or no-op when disabled."""
    if not enabled:
        yield TraceHandle(trace_id=None)
        return

    token = _current_trace_id.set(None)
    active_token: Token[bool] | None = None
    try:
        try:
            import mlflow
            from mlflow.entities import SpanType
        except Exception:
            logger.warning("MLflow import failed for turn span; continuing without traces", exc_info=True)
            yield TraceHandle(trace_id=None)
            return

        try:
            span_context = mlflow.start_span(name=_SPAN_NAME, span_type=SpanType.CHAIN)
            span = span_context.__enter__()
        except Exception:
            logger.warning("MLflow turn span setup failed; continuing without traces", exc_info=True)
            yield TraceHandle(trace_id=None)
            return

        active_token = _fleet_trace_active.set(True)
        try:
            mlflow.update_current_trace(
                session_id=str(session_id),
                user=_LOCAL_BYOK_USER,
                tags={
                    "fleet.run_id": str(run_id),
                    "fleet.session_id": str(session_id),
                },
            )
        except Exception:
            logger.warning("MLflow update_current_trace failed; continuing", exc_info=True)
        trace_id: str | None = None
        try:
            raw = mlflow.get_last_active_trace_id() or getattr(span, "request_id", None)
            if raw is not None:
                trace_id = str(raw)
                if expose_trace_id:
                    _current_trace_id.set(trace_id)
        except Exception:
            logger.warning("MLflow get_last_active_trace_id failed; continuing", exc_info=True)

        try:
            yield TraceHandle(trace_id=trace_id if expose_trace_id else None)
        except BaseException as exc:
            try:
                span_context.__exit__(type(exc), exc, exc.__traceback__)
            except BaseException:
                logger.warning("MLflow turn span teardown failed; continuing", exc_info=True)
            raise
        else:
            try:
                span_context.__exit__(None, None, None)
            except BaseException:
                logger.warning("MLflow turn span teardown failed; continuing", exc_info=True)
    finally:
        if active_token is not None:
            _fleet_trace_active.reset(active_token)
        _current_trace_id.reset(token)
