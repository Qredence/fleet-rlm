"""Tenant admission helpers for Entra-authenticated identities."""

from __future__ import annotations

from fleet_rlm.infrastructure.database import FleetRepository, TenantStatus
from fleet_rlm.infrastructure.database.types import IdentityUpsertResult

from .base import AuthError
from .types import NormalizedIdentity


def _tenant_status_message(status: TenantStatus) -> str:
    if status == TenantStatus.SUSPENDED:
        return "Tenant access is suspended for Fleet RLM."
    if status == TenantStatus.DELETED:
        return "Tenant access has been removed for Fleet RLM."
    return "Tenant is not active for Fleet RLM."


async def resolve_admitted_identity(
    repository: FleetRepository,
    identity: NormalizedIdentity,
) -> IdentityUpsertResult:
    persisted = await repository.resolve_control_plane_identity(
        entra_tenant_id=identity.tenant_claim,
        entra_user_id=identity.user_claim,
        email=identity.email,
        full_name=identity.name,
    )
    if persisted is None:
        raise AuthError(
            "Tenant is not allowlisted for Fleet RLM.",
            status_code=403,
        )
    if persisted.tenant_status != TenantStatus.ACTIVE:
        raise AuthError(
            _tenant_status_message(persisted.tenant_status or TenantStatus.DELETED),
            status_code=403,
        )
    if persisted.user_id is None:
        raise AuthError(
            "Tenant identity could not be resolved for Fleet RLM.",
            status_code=403,
        )
    return IdentityUpsertResult(
        tenant_id=persisted.tenant_id,
        user_id=persisted.user_id,
        tenant_status=persisted.tenant_status,
        membership_role=persisted.membership_role,
    )
