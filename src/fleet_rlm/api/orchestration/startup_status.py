"""Compatibility shim delegating startup-status policy to the outer host."""

from fleet_rlm.agent_host.startup_status import (
    build_startup_status_event,
    cancel_startup_status_task,
    emit_delayed_startup_status,
)

__all__ = [
    "build_startup_status_event",
    "cancel_startup_status_task",
    "emit_delayed_startup_status",
]
