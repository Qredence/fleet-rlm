"""Microsoft Entra auth stub provider.

TODO: Implement real JWKS-based token verification for Entra OIDC.
"""

from __future__ import annotations

from fastapi import Request, WebSocket

from .base import AuthError
from .types import NormalizedIdentity


class EntraAuthProvider:
    """Placeholder for future Entra OIDC/JWKS verification."""

    def __init__(
        self,
        *,
        jwks_url: str | None = None,
        issuer: str | None = None,
        audience: str | None = None,
    ) -> None:
        self.jwks_url = jwks_url
        self.issuer = issuer
        self.audience = audience

    async def authenticate_http(self, request: Request) -> NormalizedIdentity:
        raise AuthError(
            "AUTH_MODE=entra is configured, but Entra token verification is not implemented yet.",
            status_code=503,
        )

    async def authenticate_websocket(self, websocket: WebSocket) -> NormalizedIdentity:
        raise AuthError(
            "AUTH_MODE=entra is configured, but Entra token verification is not implemented yet.",
            status_code=503,
        )
