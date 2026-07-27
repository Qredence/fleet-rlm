"""Fail-soft per-Turn MLflow root spans for engineering observability.

Must never affect Turn outcomes. When disabled or when MLflow is unavailable,
``turn_trace`` yields a no-op handle with ``trace_id=None``.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from uuid import UUID

logger = logging.getLogger(__name__)

_LOCAL_BYOK_USER = "fleet-local"
_SPAN_NAME = "fleet_turn"
_current_trace_id: ContextVar[str | None] = ContextVar("fleet_mlflow_trace_id", default=None)


@dataclass(frozen=True, slots=True)
class TraceHandle:
    """Public-safe handle for an optional active Turn trace."""

    trace_id: str | None


def annotate_trace_io(
    *,
    request: str,
    response_text: str | None = None,
    response_outputs: dict[str, object] | None = None,
) -> None:
    """Fail-soft: propagate request/response to the active root trace for MLflow judges.

    MLflow LLM judges (Safety, Completeness, RelevanceToQuery) read from the
    root span's inputs/outputs. Without this, judges either fail or fall back
    to expensive trace-based parsing of all spans.

    Uses span.set_inputs()/set_outputs() on the current active span (which is
    the fleet_turn root span when called from TurnCoordinator._execute_traced).

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
    except Exception:
        logger.debug("annotate_trace_io failed; continuing without root span I/O", exc_info=True)


def current_turn_trace_id() -> str | None:
    """Return the active Turn trace id for this context, if any."""
    return _current_trace_id.get()


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
    try:
        try:
            import mlflow
            from mlflow.entities import SpanType
        except Exception:
            logger.warning("MLflow import failed for turn span; continuing without traces", exc_info=True)
            yield TraceHandle(trace_id=None)
            return

        try:
            with mlflow.start_span(name=_SPAN_NAME, span_type=SpanType.CHAIN) as span:
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
                yield TraceHandle(trace_id=trace_id if expose_trace_id else None)
        except Exception:
            logger.warning("MLflow turn span failed; continuing without traces", exc_info=True)
            yield TraceHandle(trace_id=None)
    finally:
        _current_trace_id.reset(token)
