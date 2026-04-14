"""Compatibility re-export for legacy terminal flow imports."""

from __future__ import annotations

from fleet_rlm.agent_host.terminal_flow import (
    apply_terminal_event_policy,
    finalize_and_persist_terminal_session_state,
    finalize_terminal_session_state,
    is_terminal_event,
    terminal_run_status,
)

__all__ = [
    "apply_terminal_event_policy",
    "finalize_and_persist_terminal_session_state",
    "finalize_terminal_session_state",
    "is_terminal_event",
    "terminal_run_status",
]
