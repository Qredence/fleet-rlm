"""Shutdown and outer-exception handling for websocket chat loops."""

from __future__ import annotations

import asyncio
import logging

from fastapi import WebSocket

from fleet_rlm.integrations.database import RunStatus

from .failures import PersistenceRequiredError, classify_stream_failure
from .helpers import _error_envelope, _sanitize_for_log, _try_send_json
from ...runtime_services.chat_persistence import ExecutionLifecycleManager
from .task_control import cancel_task
from .types import LocalPersistFn

logger = logging.getLogger(__name__)


async def handle_chat_disconnect(
    *,
    pending_receive_task: asyncio.Task[object] | None,
    stream_task: asyncio.Task[str | None] | None,
    cancel_flag: dict[str, bool],
    local_persist: LocalPersistFn,
    lifecycle: ExecutionLifecycleManager | None,
) -> None:
    """Cleanly stop the active websocket loop after a client disconnect."""
    cancel_flag["cancelled"] = True
    await cancel_task(pending_receive_task)
    await cancel_task(stream_task)
    try:
        await local_persist(include_volume_save=True)
    except PersistenceRequiredError as exc:
        logger.warning(
            "Session persistence failed during disconnect: %s",
            _sanitize_for_log(exc),
        )
        if lifecycle is not None and not lifecycle.run_completed:
            await lifecycle.complete_run(
                RunStatus.FAILED,
                error_json={
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                    "code": exc.code,
                },
            )
        return

    if lifecycle is not None:
        await lifecycle.complete_run(RunStatus.CANCELLED)


async def handle_chat_loop_exception(
    *,
    websocket: WebSocket,
    exc: Exception,
    pending_receive_task: asyncio.Task[object] | None,
    stream_task: asyncio.Task[str | None] | None,
    local_persist: LocalPersistFn,
    lifecycle: ExecutionLifecycleManager | None,
) -> None:
    """Handle an unexpected outer-loop failure without losing client notification."""
    await cancel_task(pending_receive_task)
    await cancel_task(stream_task)
    error_code = classify_stream_failure(exc)
    await _try_send_json(
        websocket,
        _error_envelope(
            code=error_code,
            message=f"Server error: {str(exc)}",
            details={"error_type": type(exc).__name__},
        ),
    )
    try:
        await local_persist(include_volume_save=True)
    except PersistenceRequiredError as persist_exc:
        logger.warning(
            "Session persistence failed after stream error: %s",
            _sanitize_for_log(persist_exc),
        )

    if lifecycle is not None:
        await lifecycle.complete_run(
            RunStatus.FAILED,
            error_json={
                "error": str(exc),
                "error_type": type(exc).__name__,
                "code": error_code,
            },
        )
