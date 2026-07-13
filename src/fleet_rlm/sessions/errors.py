"""Public-safe Session domain errors."""

from __future__ import annotations


class SessionError(RuntimeError):
    """Base error for Session domain failures."""


class SessionNotFoundError(SessionError):
    """Raised when a session id cannot be loaded."""


class SessionAccessDenied(SessionError):
    """Caller is not allowed to access the session (map publicly to not-found)."""
