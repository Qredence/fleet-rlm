"""Public-safe Session domain errors."""

from __future__ import annotations

from uuid import UUID


class SessionError(RuntimeError):
    """Base error for Session domain failures."""


class SessionNotFoundError(SessionError):
    """Raised when a session id cannot be loaded."""


class SessionAccessDeniedError(SessionError):
    """Caller is not allowed to access the session (map publicly to not-found)."""


class SessionRetirementPendingError(SessionError):
    """Raised when a Session is archived durably but provider retirement failed."""

    def __init__(self, session_id: UUID) -> None:
        self.session_id = session_id
        super().__init__(f"session retirement is pending for {session_id}")
