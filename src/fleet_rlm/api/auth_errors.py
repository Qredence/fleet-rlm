"""Auth domain errors and public detail allowlist for the clean package."""

from __future__ import annotations

from typing import Literal

AuthFailureKind = Literal["required", "invalid", "unavailable"]

PUBLIC_AUTH_DETAIL: dict[AuthFailureKind, str] = {
    "required": "authentication required",
    "invalid": "invalid token",
    "unavailable": "authentication unavailable",
}

PUBLIC_WORKSPACE_MISMATCH_DETAIL = "workspace header does not match authenticated tenant"


class AuthError(Exception):
    """Authentication or authorization failure (server-side message + public kind)."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 401,
        kind: AuthFailureKind = "invalid",
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.kind = kind

    @property
    def public_detail(self) -> str:
        return PUBLIC_AUTH_DETAIL[self.kind]
