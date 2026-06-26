"""Auth provider factory."""

from __future__ import annotations

from .base import AuthProvider
from .neon import NeonAuthProvider


def build_auth_provider(
    *,
    neon_auth_url: str | None = None,
    neon_tenant_claim: str | None = None,
) -> AuthProvider:
    """Build the Neon Auth provider (the only authentication method)."""
    return NeonAuthProvider(
        neon_auth_url=neon_auth_url,
        tenant_claim=neon_tenant_claim,
    )
