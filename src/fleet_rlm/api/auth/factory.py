"""Auth provider factory."""

from __future__ import annotations

from .base import AuthProvider
from .dev import DevAuthProvider
from .entra import EntraAuthProvider
from .neon import NeonAuthProvider


def build_auth_provider(
    *,
    auth_mode: str,
    dev_jwt_secret: str,
    allow_debug_auth: bool = True,
    allow_query_auth_tokens: bool = True,
    entra_jwks_url: str | None = None,
    entra_issuer_url: str | None = None,
    entra_issuer_template: str | None = None,
    entra_audience: str | None = None,
    entra_allowed_user_ids: set[str] | None = None,
    entra_allowed_group_ids: set[str] | None = None,
    neon_auth_url: str | None = None,
    neon_tenant_claim: str | None = None,
) -> AuthProvider:
    mode = auth_mode.strip().lower()
    if mode == "dev":
        return DevAuthProvider(
            jwt_secret=dev_jwt_secret,
            allow_debug_auth=allow_debug_auth,
            allow_query_auth_tokens=allow_query_auth_tokens,
        )
    if mode == "entra":
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
            neon_auth_url=neon_auth_url,
            tenant_claim=neon_tenant_claim,
            allow_query_auth_tokens=allow_query_auth_tokens,
        )
    raise ValueError(f"Unsupported auth mode: {auth_mode}")
