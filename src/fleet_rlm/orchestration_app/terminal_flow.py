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


def is_terminal_event(event: StreamEventLike) -> bool:
    """Return orchestration terminal semantics for worker-compatible events.

    The worker boundary sets ``terminal`` on normalized worker events, while
    websocket transport and compatibility paths may still surface legacy events
    that only expose the canonical terminal kinds. Checking both preserves the
    existing websocket/frontend contract across both shapes.
    """

    return event.kind in {"final", "cancelled", "error"} or bool(
        getattr(event, "terminal", False)
    )


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
    if not is_terminal_event(event):
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


async def finalize_and_persist_terminal_session_state(
    *,
    event: StreamEventLike,
    session: OrchestrationSessionContext | None,
    persist_session_state: LocalPersistFn,
    failure_log_message: str,
) -> None:
    """Apply outer terminal session-state policy before best-effort persistence."""

    finalize_terminal_session_state(event=event, session=session)
    try:
        await persist_session_state(include_volume_save=True)
    except Exception:
        if "%s" in failure_log_message:
            logger.exception(failure_log_message, event.kind)
        else:
            logger.exception(failure_log_message)


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
        await finalize_and_persist_terminal_session_state(
            event=event,
            session=session,
            persist_session_state=persist_session_state,
            failure_log_message=(
                "Failed to persist session state before final event; continuing"
            ),
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

    await finalize_and_persist_terminal_session_state(
        event=event,
        session=session,
        persist_session_state=persist_session_state,
        failure_log_message=(
            "Failed to persist session state after %s event; completing run anyway"
        ),
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
