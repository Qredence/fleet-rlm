"""Outer terminal ordering and completion policy around worker execution."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from fleet_rlm.integrations.database import RunStatus

from ..api.events import ExecutionStep
from ..api.routers.ws.completion import (
    build_execution_completion_summary,
    final_event_failed,
)
from ..api.routers.ws.lifecycle import ExecutionLifecycleManager
from ..api.routers.ws.types import LocalPersistFn, StreamEventLike
from .checkpoints import OrchestrationCheckpointState
from .sessions import OrchestrationSessionContext

logger = logging.getLogger(__name__)

SendTerminalEvent = Callable[[], Awaitable[bool]]


def terminal_run_status(event: StreamEventLike) -> RunStatus:
    """Return the authoritative terminal run status for one worker event."""

    if event.kind == "cancelled":
        return RunStatus.CANCELLED
    if event.kind == "final":
        payload = event.payload if isinstance(event.payload, dict) else {}
        return RunStatus.FAILED if final_event_failed(payload) else RunStatus.COMPLETED
    return RunStatus.FAILED


def finalize_terminal_session_state(
    *,
    event: StreamEventLike,
    session: OrchestrationSessionContext | None,
) -> None:
    """Apply terminal continuation/checkpoint policy without touching transport."""

    if session is None:
        return
    if event.kind not in {"final", "cancelled", "error"} and not bool(
        getattr(event, "terminal", False)
    ):
        return

    state = session.load_checkpoint_state()
    if state.pending_approval is not None:
        return
    if state.workflow_stage == "completed":
        return

    session.save_checkpoint_state(
        OrchestrationCheckpointState(
            workflow_stage="completed",
            continuation=state.continuation,
        )
    )


async def apply_terminal_event_policy(
    *,
    lifecycle: ExecutionLifecycleManager,
    event: StreamEventLike,
    step: ExecutionStep | None,
    session: OrchestrationSessionContext | None,
    persist_session_state: LocalPersistFn,
    request_message: str,
    send_terminal_event: SendTerminalEvent,
) -> bool:
    """Own terminal ordering, completion status, and continuation-state policy."""

    summary = build_execution_completion_summary(
        event=event,
        request_message=request_message,
        run_id=lifecycle.run_id,
    )
    if event.kind == "final":
        finalize_terminal_session_state(event=event, session=session)
        try:
            await persist_session_state(include_volume_save=True)
        except Exception:
            logger.exception(
                "Failed to persist session state before final event; continuing"
            )
        await lifecycle.complete_run(
            terminal_run_status(event),
            step=step,
            summary=summary,
        )
        return await send_terminal_event()

    sent = await send_terminal_event()
    if not sent:
        return False

    finalize_terminal_session_state(event=event, session=session)
    try:
        await persist_session_state(include_volume_save=True)
    except Exception:
        logger.exception(
            "Failed to persist session state after %s event; completing run anyway",
            event.kind,
        )

    error_json: dict[str, Any] | None = (
        {"error": event.text, "kind": event.kind} if event.kind == "error" else None
    )
    await lifecycle.complete_run(
        terminal_run_status(event),
        step=step,
        error_json=error_json,
        summary=summary,
    )
    return True
