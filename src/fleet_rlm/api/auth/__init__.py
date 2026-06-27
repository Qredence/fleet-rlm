"""Server auth abstraction using Neon Auth."""

from __future__ import annotations

from .admission import resolve_admitted_identity
from .base import AuthError, AuthProvider
from .neon import NeonAuthProvider
from .types import NormalizedIdentity
from .ws_ticket import WebSocketTicketStore


def build_auth_provider(
    *,
    neon_tenant_claim: str | None = None,
) -> AuthProvider:
    """Build the Neon Auth provider (the only authentication method).

    The Neon Auth URL is hardcoded as a class constant on
    :class:`NeonAuthProvider`; it is not read from environment or config.
    """
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
