"""Session repository errors."""

from __future__ import annotations


class SessionRepositoryError(RuntimeError):
    """Base error for session persistence failures."""


class SessionNotFoundError(SessionRepositoryError):
    """Raised when a session id cannot be loaded."""


class SessionAccessDenied(SessionRepositoryError):
    """Caller is not allowed to access the session (map publicly to not-found)."""


class IdempotencyConflictError(SessionRepositoryError):
    """Raised when an idempotency key is already bound to an in-flight run."""
