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
from uuid import UUID

logger = logging.getLogger(__name__)

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

        span.set_inputs({"request": request})

        response: dict[str, object] = {}
        if response_text is not None:
            response["answer"] = response_text
        if response_outputs is not None:
            for key in ("answer", "final_reasoning"):
                if key in response_outputs:
                    response[key] = response_outputs[key]

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
class PhaseSpanHandle:
    """Fail-soft output accumulator yielded by ``turn_phase_span``.

    Callers attach bounded operational metadata via ``set_outputs``; the values
    are merged with the terminal ``phase_status`` when the span closes. When
    tracing is inactive the handle is a no-op. Neither path ever raises.
    """

    outputs: dict[str, object] = field(default_factory=dict)

    def set_outputs(self, outputs: Mapping[str, object]) -> None:
        try:
            self.outputs.update(dict(outputs))
        except Exception:
            logger.debug("phase span output accumulation failed; continuing", exc_info=True)


@contextmanager
def turn_phase_span(name: str, *, inputs: Mapping[str, object]) -> Iterator[PhaseSpanHandle]:
    """Record one bounded, nested Turn phase without affecting its outcome.

    The caller supplies operational metadata only. In particular, callers must
    not attach model prompts, REPL code, or interpreter output here. Yields a
    ``PhaseSpanHandle`` so callers can attach bounded outputs at exit time.
    Outside an active ``fleet_turn`` trace this is a no-op that never imports
    MLflow, keeping tracing-disabled turns free of any MLflow footprint.
    """
    if not _fleet_trace_active.get():
        yield PhaseSpanHandle()
        return

    try:
        import mlflow
        from mlflow.entities import SpanType

        active_span = mlflow.get_current_active_span()
    except Exception:
        logger.debug("MLflow phase span setup failed; continuing without phase tracing", exc_info=True)
        yield PhaseSpanHandle()
        return

    if active_span is None:
        yield PhaseSpanHandle()
        return

    try:
        span_context = mlflow.start_span(name=name, span_type=SpanType.CHAIN)
        span = span_context.__enter__()
    except Exception:
        logger.debug("MLflow phase span setup failed; continuing without phase tracing", exc_info=True)
        yield PhaseSpanHandle()
        return

    handle = PhaseSpanHandle()
    try:
        try:
            span.set_inputs(dict(inputs))
        except Exception:
            logger.debug("MLflow phase span input annotation failed; continuing", exc_info=True)
        yield handle
    except BaseException as exc:
        try:
            span.set_outputs({**handle.outputs, "phase_status": "failed"})
        except Exception:
            logger.debug("MLflow phase span failure annotation failed; continuing", exc_info=True)
        try:
            span_context.__exit__(type(exc), exc, exc.__traceback__)
        except BaseException:
            logger.debug("MLflow phase span close failed; continuing", exc_info=True)
        raise
    else:
        try:
            span.set_outputs({**handle.outputs, "phase_status": "completed"})
        except Exception:
            logger.debug("MLflow phase span completion annotation failed; continuing", exc_info=True)
        try:
            span_context.__exit__(None, None, None)
        except BaseException:
            logger.debug("MLflow phase span close failed; continuing", exc_info=True)


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
