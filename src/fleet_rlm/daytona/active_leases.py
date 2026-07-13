"""Serialize active interpreter leases per session (process-local)."""

from __future__ import annotations

from threading import Lock
from uuid import UUID


class ActiveLeaseConflictError(RuntimeError):
    """Another run already holds the active lease for this session."""

    def __init__(self, session_id: UUID, holder_run_id: UUID | None = None) -> None:
        self.session_id = session_id
        self.holder_run_id = holder_run_id
        super().__init__(f"active lease conflict for session {session_id}")


class ActiveLeaseRegistry:
    """At most one active lease per session_id in this process."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._holders: dict[UUID, UUID] = {}  # session_id -> run_id

    def acquire(self, session_id: UUID, run_id: UUID) -> None:
        with self._lock:
            existing = self._holders.get(session_id)
            if existing is not None and existing != run_id:
                raise ActiveLeaseConflictError(session_id, holder_run_id=existing)
            self._holders[session_id] = run_id

    def release(self, session_id: UUID, run_id: UUID) -> None:
        with self._lock:
            existing = self._holders.get(session_id)
            if existing == run_id:
                del self._holders[session_id]

    def holder(self, session_id: UUID) -> UUID | None:
        with self._lock:
            return self._holders.get(session_id)


_REGISTRY = ActiveLeaseRegistry()


def get_active_lease_registry() -> ActiveLeaseRegistry:
    return _REGISTRY


def set_active_lease_registry(registry: ActiveLeaseRegistry) -> None:
    global _REGISTRY
    _REGISTRY = registry
