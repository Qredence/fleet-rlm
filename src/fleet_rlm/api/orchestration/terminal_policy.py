"""Compatibility terminal-policy shim delegating to outer orchestration."""

from __future__ import annotations

from fleet_rlm.api.events import ExecutionStep
from fleet_rlm.api.routers.ws.lifecycle import ExecutionLifecycleManager
from fleet_rlm.api.routers.ws.types import LocalPersistFn, StreamEventLike
from fleet_rlm.orchestration_app.terminal_flow import (
    SendTerminalEvent,
    apply_terminal_event_policy as _apply_terminal_event_policy,
)

__all__ = ["SendTerminalEvent", "apply_terminal_event_policy"]


async def apply_terminal_event_policy(
    *,
    lifecycle: ExecutionLifecycleManager,
    event: StreamEventLike,
    step: ExecutionStep | None,
    persist_session_state: LocalPersistFn,
    request_message: str,
    send_terminal_event: SendTerminalEvent,
) -> bool:
    """Preserve the legacy terminal-policy call shape while delegating outward."""

    return await _apply_terminal_event_policy(
        lifecycle=lifecycle,
        event=event,
        step=step,
        session=None,
        persist_session_state=persist_session_state,
        request_message=request_message,
        send_terminal_event=send_terminal_event,
    )
