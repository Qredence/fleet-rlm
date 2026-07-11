"""Capability-aware Sandbox lifecycle helpers (no SDK imports)."""

from __future__ import annotations

from typing import Any, Literal

from fleet_rlm_clean.daytona.errors import DaytonaAdapterError

ProviderState = Literal[
    "missing",
    "running",
    "stopped",
    "paused",
    "archived",
    "unrecoverable",
]

# Normalized states SessionManager understands.
RUNNING_STATES = frozenset({"running", "started", "active"})
STOPPED_STATES = frozenset({"stopped", "stop"})
PAUSED_STATES = frozenset({"paused", "pause"})
ARCHIVED_STATES = frozenset({"archived", "archive"})


class LifecycleCapabilityError(DaytonaAdapterError):
    """Raised when a lifecycle operation is not supported by the provider object."""

    def __init__(self, operation: str, *, cause_type: str = "LifecycleCapabilityError") -> None:
        super().__init__(
            message=f"Sandbox lifecycle operation not supported: {operation}",
            cause_type=cause_type,
        )
        self.operation = operation


def normalize_state(raw: Any) -> ProviderState:
    """Map provider state strings to Fleet provider_state values."""
    if raw is None:
        return "missing"
    text = str(getattr(raw, "value", raw)).strip().lower()
    if text in RUNNING_STATES:
        return "running"
    if text in STOPPED_STATES:
        return "stopped"
    if text in PAUSED_STATES:
        return "paused"
    if text in ARCHIVED_STATES:
        return "archived"
    if text in {"missing", "deleted", "error", "unhealthy", "unknown", ""}:
        return "unrecoverable" if text in {"error", "unhealthy"} else "missing"
    # Unknown positive-looking states treated as running when present.
    if text:
        return "running"
    return "missing"


def sandbox_state(sandbox: Any) -> ProviderState:
    raw = getattr(sandbox, "state", None)
    if raw is None:
        raw = getattr(sandbox, "status", None)
    return normalize_state(raw)


def call_if_supported(sandbox: Any, operation: str) -> None:
    """Invoke sandbox.<operation>() or raise LifecycleCapabilityError."""
    method = getattr(sandbox, operation, None)
    if not callable(method):
        raise LifecycleCapabilityError(operation)
    method()
