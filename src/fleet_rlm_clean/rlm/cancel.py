"""In-process run cancel flags (idempotent cancel requests)."""

from __future__ import annotations

from threading import Lock
from uuid import UUID


class RunCancelRegistry:
    """Track cancel requests by run_id. Process-local foundation implementation."""

    def __init__(self) -> None:
        self._cancelled: set[UUID] = set()
        self._lock = Lock()

    def request_cancel(self, run_id: UUID) -> bool:
        """Mark run cancelled. Returns True if newly cancelled, False if already set."""
        with self._lock:
            if run_id in self._cancelled:
                return False
            self._cancelled.add(run_id)
            return True

    def is_cancelled(self, run_id: UUID) -> bool:
        with self._lock:
            return run_id in self._cancelled

    def clear(self, run_id: UUID) -> None:
        with self._lock:
            self._cancelled.discard(run_id)


_REGISTRY = RunCancelRegistry()


def get_run_cancel_registry() -> RunCancelRegistry:
    return _REGISTRY


def set_run_cancel_registry(registry: RunCancelRegistry) -> None:
    """Test hook to inject a fresh registry."""
    global _REGISTRY
    _REGISTRY = registry
