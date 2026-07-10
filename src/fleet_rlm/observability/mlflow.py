"""Optional MLflow adapter for provider-neutral recorded turns."""

from __future__ import annotations

import asyncio
import contextvars
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING

from fleet_rlm.runtime.events import RuntimeEventKind

from .spans import RuntimeTraceSpan

if TYPE_CHECKING:
    from .recorder import RuntimeTraceRecord

logger = logging.getLogger(__name__)


@contextmanager
def direct_rlm_trace_context(
    *,
    session_id: str | None,
    workspace_id: str | None,
    user_id: str | None,
    app_env: str | None,
    message: str,
    turn_index: int,
    execution_mode: str | None,
    sub_lm_configured: bool,
) -> Iterator[None]:
    """Create an MLflow request context for direct RLM only when needed.

    WebSocket turns already have one. SSE enters this transport-neutral helper
    through ``stream_turn()``, so both transports use the same trace metadata
    without importing MLflow while it is disabled.
    """
    from fleet_rlm.integrations.observability.config import MlflowConfig

    if not MlflowConfig.from_env().enabled:
        yield
        return

    from fleet_rlm.integrations.observability.mlflow_context import (
        build_chat_trace_context,
        current_request_context,
        mlflow_request_context,
    )

    if current_request_context() is not None:
        yield
        return

    context = build_chat_trace_context(
        workspace_id=workspace_id,
        user_id=user_id,
        session_id=session_id,
        turn_index=turn_index,
        run_id=None,
        message=message,
        execution_mode=execution_mode,
        app_env=app_env,
        sub_lm_configured=sub_lm_configured,
        execution_backend="direct_rlm",
    )
    with mlflow_request_context(context):
        yield


def _active_trace_id() -> str | None:
    """Read an already-created trace id without triggering MLflow work."""
    from fleet_rlm.integrations.observability.mlflow_context import current_request_context

    context = current_request_context()
    return context.resolved_trace_id if context is not None else None


def _record_enabled_direct_turn(record: RuntimeTraceRecord) -> str | None:
    """Adapt existing MLflow context helpers when MLflow is explicitly enabled.

    All imports remain inside the enabled branch. Failures are intentionally
    server-log-only: the returned span is still safe and useful to transports.
    """
    from fleet_rlm.integrations.observability.config import MlflowConfig

    if not MlflowConfig.from_env().enabled:
        return None

    try:
        from fleet_rlm.integrations.observability.mlflow_context import (
            capture_last_active_trace_id,
            record_rlm_trajectory_spans,
        )

        terminal_payload = record.events[-1].payload if record.events else {}
        trajectory = terminal_payload.get("trajectory")
        if trajectory is not None:
            record_rlm_trajectory_spans(trajectory)
        # The request context owns terminal state and finalization. This worker
        # may run before the enclosing transport context unwinds, so it only
        # records trajectory spans and observes the active trace correlation.
        return capture_last_active_trace_id()
    except Exception:
        logger.warning("Direct-RLM MLflow completion recording failed.", exc_info=True)
        return None


def _schedule_direct_rlm_completion_export(record: RuntimeTraceRecord) -> None:
    """Run optional MLflow finalization outside the async event-loop path."""
    from fleet_rlm.integrations.observability.config import MlflowConfig

    if not MlflowConfig.from_env().enabled:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.debug("Direct-RLM MLflow export skipped without a running event loop.")
        return

    copied_context = contextvars.copy_context()
    future = loop.run_in_executor(None, copied_context.run, _record_enabled_direct_turn, record)

    def _log_export_result(completed: asyncio.Future[str | None]) -> None:
        try:
            completed.result()
        except Exception:
            logger.warning("Direct-RLM MLflow background export failed.", exc_info=True)

    future.add_done_callback(_log_export_result)


def emit_direct_rlm_completion_span(record: RuntimeTraceRecord) -> RuntimeTraceSpan:
    """Create a completion span and queue optional MLflow export off-loop."""
    trace_id = _active_trace_id()
    _schedule_direct_rlm_completion_export(record)
    status = "completed" if record.terminal_kind is RuntimeEventKind.DONE else "error"
    return RuntimeTraceSpan(
        span_id=f"direct-rlm-{record.trace_id}",
        name="direct_rlm.turn",
        status=status,
        trace_id=trace_id,
        duration_ms=record.duration_ms,
        metadata={
            "execution_backend": "direct_rlm",
            "session_id": record.session_id,
            "event_count": len(record.events),
            "input_tokens": record.performance.input_tokens,
            "output_tokens": record.performance.output_tokens,
            "total_tokens": record.performance.total_tokens,
        },
    )


__all__ = ["direct_rlm_trace_context", "emit_direct_rlm_completion_span"]
