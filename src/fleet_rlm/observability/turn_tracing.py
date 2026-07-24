"""Fail-soft per-Turn MLflow root spans for engineering observability.

Must never affect Turn outcomes. When disabled or when MLflow is unavailable,
``turn_trace`` yields a no-op handle with ``trace_id=None``.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator
from uuid import UUID

logger = logging.getLogger(__name__)

_LOCAL_BYOK_USER = "fleet-local"
_SPAN_NAME = "fleet_turn"
_current_trace_id: ContextVar[str | None] = ContextVar("fleet_mlflow_trace_id", default=None)


@dataclass(frozen=True, slots=True)
class TraceHandle:
    """Public-safe handle for an optional active Turn trace."""

    trace_id: str | None


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
        except Exception:  # noqa: BLE001
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
                except Exception:  # noqa: BLE001
                    logger.warning("MLflow update_current_trace failed; continuing", exc_info=True)
                trace_id: str | None = None
                try:
                    raw = mlflow.get_last_active_trace_id() or getattr(span, "request_id", None)
                    if raw is not None:
                        trace_id = str(raw)
                        if expose_trace_id:
                            _current_trace_id.set(trace_id)
                except Exception:  # noqa: BLE001
                    logger.warning("MLflow get_last_active_trace_id failed; continuing", exc_info=True)
                yield TraceHandle(trace_id=trace_id if expose_trace_id else None)
        except Exception:  # noqa: BLE001
            logger.warning("MLflow turn span failed; continuing without traces", exc_info=True)
            yield TraceHandle(trace_id=None)
    finally:
        _current_trace_id.reset(token)
