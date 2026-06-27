"""Server auth abstraction using Neon Auth."""

from __future__ import annotations

from .admission import resolve_admitted_identity
from .base import AuthError, AuthProvider
from .dev import DevAuthProvider
from .neon import NeonAuthProvider
from .types import NormalizedIdentity
from .ws_ticket import WebSocketTicketStore


def build_auth_provider(
    *,
    neon_tenant_claim: str | None = None,
    dev_mode: bool = False,
    dev_jwt_secret: str = "change-me",
) -> AuthProvider:
    """Build the appropriate auth provider.

    In production mode (dev_mode=False), returns a :class:`NeonAuthProvider`
    wired to the Neon Auth URL.

    In dev mode, returns a :class:`DevAuthProvider` that accepts debug headers
    and local bearer tokens for testing.
    """
    if dev_mode:
        return DevAuthProvider(
            jwt_secret=dev_jwt_secret,
        )
    return NeonAuthProvider(
        tenant_claim=neon_tenant_claim,
    )


__all__ = [
    "AuthError",
    "AuthProvider",
    "NeonAuthProvider",
    "NormalizedIdentity",
    "WebSocketTicketStore",
    "build_auth_provider",
    "resolve_admitted_identity",
]
