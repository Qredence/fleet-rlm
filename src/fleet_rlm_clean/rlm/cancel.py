"""In-process run cancel flags with ownership binding (idempotent cancel)."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from uuid import UUID


@dataclass(frozen=True, slots=True)
class RunOwnership:
    user_id: UUID
    workspace_id: UUID
    session_id: UUID
    terminal: bool = False


class RunCancelRegistry:
    """Track cancel requests and Run ownership. Process-local foundation implementation."""

    def __init__(self) -> None:
        self._cancelled: set[UUID] = set()
        self._ownership: dict[UUID, RunOwnership] = {}
        self._lock = Lock()

    def bind(
        self,
        run_id: UUID,
        *,
        user_id: UUID,
        workspace_id: UUID,
        session_id: UUID,
    ) -> None:
        """Record ownership for a Run so cancel can authorize without guessing."""
        with self._lock:
            existing = self._ownership.get(run_id)
            if existing is not None and existing.terminal:
                return
            self._ownership[run_id] = RunOwnership(
                user_id=user_id,
                workspace_id=workspace_id,
                session_id=session_id,
                terminal=False,
            )

    def mark_terminal(self, run_id: UUID) -> None:
        with self._lock:
            existing = self._ownership.get(run_id)
            if existing is None:
                return
            self._ownership[run_id] = RunOwnership(
                user_id=existing.user_id,
                workspace_id=existing.workspace_id,
                session_id=existing.session_id,
                terminal=True,
            )

    def ownership_of(self, run_id: UUID) -> RunOwnership | None:
        with self._lock:
            return self._ownership.get(run_id)

    def authorize(
        self,
        run_id: UUID,
        *,
        user_id: UUID,
        workspace_id: UUID,
    ) -> RunOwnership | None:
        """Return ownership when principal matches; None means not found (404)."""
        with self._lock:
            owned = self._ownership.get(run_id)
            if owned is None:
                return None
            if owned.user_id != user_id or owned.workspace_id != workspace_id:
                return None
            return owned

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
            self._ownership.pop(run_id, None)


_REGISTRY = RunCancelRegistry()


def get_run_cancel_registry() -> RunCancelRegistry:
    return _REGISTRY


def set_run_cancel_registry(registry: RunCancelRegistry) -> None:
    """Test hook to inject a fresh registry."""
    global _REGISTRY
    _REGISTRY = registry
