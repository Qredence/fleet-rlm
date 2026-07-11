"""Session repository errors."""

from __future__ import annotations


class SessionRepositoryError(RuntimeError):
    """Base error for session persistence failures."""


class SessionNotFoundError(SessionRepositoryError):
    """Raised when a session id cannot be loaded."""


class IdempotencyConflictError(SessionRepositoryError):
    """Raised when an idempotency key is already bound to an in-flight run."""
