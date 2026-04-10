"""WebSocket chat stream failure handling."""

from __future__ import annotations

import logging
import time

from fastapi import WebSocket

from fleet_rlm.integrations.database import RunStatus
from fleet_rlm.worker import WorkspaceEvent

from ...events import ExecutionStepBuilder
from .completion import build_execution_completion_summary
from .failures import classify_stream_failure
from .helpers import _error_envelope, _sanitize_for_log, _try_send_json
from .lifecycle import ExecutionLifecycleManager

logger = logging.getLogger(__name__)


async def handle_stream_error(
    *,
    websocket: WebSocket,
    lifecycle: ExecutionLifecycleManager,
    step_builder: ExecutionStepBuilder,
    exc: Exception,
    request_message: str,
) -> None:
    """Log, emit, and persist a failed websocket streaming turn."""
    error_code = classify_stream_failure(exc)
    logger.error(
        "Streaming error: %s",
        _sanitize_for_log(exc),
        exc_info=True,
        extra={
            "error_type": type(exc).__name__,
            "error_code": error_code,
        },
    )
    await _try_send_json(
        websocket,
        _error_envelope(
            code=error_code,
            message=f"Streaming error: {exc}",
            details={"error_type": type(exc).__name__},
        ),
    )
    if lifecycle.run_completed:
        return

    error_text = f"Streaming error: {exc}"
    error_payload = {
        "error_type": type(exc).__name__,
        "error_code": error_code,
    }
    error_step = step_builder.from_stream_event(
        kind="error",
        text=error_text,
        payload=error_payload,
        timestamp=time.time(),
    )
    if error_step is not None:
        await lifecycle.emit_step(error_step)
    await lifecycle.complete_run(
        RunStatus.FAILED,
        step=error_step,
        error_json={
            "error": str(exc),
            "error_type": type(exc).__name__,
            "code": error_code,
        },
        summary=build_execution_completion_summary(
            event=WorkspaceEvent(
                kind="error",
                text=error_text,
                payload=error_payload,
                terminal=True,
            ),
            request_message=request_message,
            run_id=lifecycle.run_id,
        ),
    )
