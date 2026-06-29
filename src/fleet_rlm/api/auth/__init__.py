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
    auth_mode: str,
    dev_jwt_secret: str = "change-me",
    allow_query_auth_tokens: bool = True,
    entra_jwks_url: str | None = None,
    entra_issuer_url: str | None = None,
    entra_issuer_template: str | None = None,
    entra_audience: str | None = None,
    entra_allowed_user_ids: set[str] | None = None,
    entra_allowed_group_ids: set[str] | None = None,
    neon_tenant_claim: str | None = None,
) -> AuthProvider:
    """Build the appropriate auth provider."""
    mode = auth_mode.strip().lower()
    if mode == "dev":
        return DevAuthProvider(
            jwt_secret=dev_jwt_secret,
            allow_query_auth_tokens=allow_query_auth_tokens,
        )
    if mode == "entra":
        from .entra import EntraAuthProvider

        return EntraAuthProvider(
            jwks_url=entra_jwks_url,
            issuer_url=entra_issuer_url,
            issuer_template=entra_issuer_template,
            audience=entra_audience,
            allowed_user_ids=entra_allowed_user_ids,
            allowed_group_ids=entra_allowed_group_ids,
            allow_query_auth_tokens=allow_query_auth_tokens,
        )
    if mode == "neon":
        return NeonAuthProvider(
            tenant_claim=neon_tenant_claim,
            allow_query_auth_tokens=allow_query_auth_tokens,
        )
    raise ValueError(f"Unsupported auth mode: {auth_mode}")


__all__ = [
    "AuthError",
    "AuthProvider",
    "NeonAuthProvider",
    "NormalizedIdentity",
    "WebSocketTicketStore",
    "build_auth_provider",
    "resolve_admitted_identity",
]
