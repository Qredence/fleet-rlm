"""Compatibility session-policy shim delegating to outer orchestration."""

from __future__ import annotations

from fleet_rlm.orchestration_app import (
    SessionSwitchOutcome,
    switch_orchestration_session,
)


async def switch_execution_session(**kwargs) -> SessionSwitchOutcome:
    """Delegate legacy websocket session switching to orchestration_app."""

    return await switch_orchestration_session(**kwargs)
