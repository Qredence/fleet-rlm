"""Terminal event ordering and cleanup policy for websocket execution."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from fleet_rlm.integrations.database import RunStatus

from ..events import ExecutionStep
from ..routers.ws.completion import (
    build_execution_completion_summary,
    final_event_failed,
)
from ..routers.ws.lifecycle import ExecutionLifecycleManager
from ..routers.ws.types import LocalPersistFn, StreamEventLike

logger = logging.getLogger(__name__)

SendTerminalEvent = Callable[[], Awaitable[bool]]


def _final_run_status(event: StreamEventLike) -> RunStatus:
    payload = event.payload if isinstance(event.payload, dict) else {}
    return RunStatus.FAILED if final_event_failed(payload) else RunStatus.COMPLETED


async def apply_terminal_event_policy(
    *,
    lifecycle: ExecutionLifecycleManager,
    event: StreamEventLike,
    step: ExecutionStep | None,
    persist_session_state: LocalPersistFn,
    request_message: str,
    send_terminal_event: SendTerminalEvent,
) -> bool:
    """Apply terminal ordering/cleanup policy while transport owns the socket."""

    if event.kind == "final":
        try:
            await persist_session_state(include_volume_save=True)
        except Exception:
            logger.exception(
                "Failed to persist session state before final event; continuing"
            )
        await lifecycle.complete_run(
            _final_run_status(event),
            step=step,
            summary=build_execution_completion_summary(
                event=event,
                request_message=request_message,
                run_id=lifecycle.run_id,
            ),
        )
        return await send_terminal_event()

    sent = await send_terminal_event()
    if not sent:
        return False

    try:
        await persist_session_state(include_volume_save=True)
    except Exception:
        logger.exception(
            "Failed to persist session state after %s event; completing run anyway",
            event.kind,
        )

    status = RunStatus.CANCELLED if event.kind == "cancelled" else RunStatus.FAILED
    error_json: dict[str, Any] | None = (
        {"error": event.text, "kind": event.kind} if event.kind == "error" else None
    )
    await lifecycle.complete_run(
        status,
        step=step,
        error_json=error_json,
        summary=build_execution_completion_summary(
            event=event,
            request_message=request_message,
            run_id=lifecycle.run_id,
        ),
    )
    return True

