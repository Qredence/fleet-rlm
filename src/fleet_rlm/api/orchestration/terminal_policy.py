"""Compatibility terminal-policy shim delegating to outer orchestration."""

from __future__ import annotations

from fleet_rlm.orchestration_app.terminal_flow import (
    SendTerminalEvent,
    apply_terminal_event_policy,
)

__all__ = ["SendTerminalEvent", "apply_terminal_event_policy"]
