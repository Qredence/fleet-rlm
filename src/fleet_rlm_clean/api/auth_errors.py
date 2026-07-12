"""Auth domain errors for the clean package."""

from __future__ import annotations


class AuthError(Exception):
    """Authentication or authorization failure."""

    def __init__(self, message: str, *, status_code: int = 401) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class SessionAccessDenied(AuthError):
    """Caller cannot access the requested session (map to 404 publicly)."""

    def __init__(self, message: str = "session not found") -> None:
        super().__init__(message, status_code=404)
