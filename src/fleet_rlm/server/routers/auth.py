"""Identity introspection endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Request

from ..deps import get_repository
from ..schemas import AuthMeResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/me", response_model=AuthMeResponse)
async def auth_me(request: Request) -> AuthMeResponse:
    identity = getattr(request.state, "identity", None)
    if identity is None:
        # Dependency should already enforce auth, but keep defensive fallback.
        return AuthMeResponse(tenant_claim="", user_claim="")

    tenant_id: str | None = None
    user_id: str | None = None

    repository = get_repository()
    if repository is not None:
        upserted = await repository.upsert_identity(
            entra_tenant_id=identity.tenant_claim,
            entra_user_id=identity.user_claim,
            email=identity.email,
            full_name=identity.name,
        )
        tenant_id = str(upserted.tenant_id)
        user_id = str(upserted.user_id)

    return AuthMeResponse(
        tenant_claim=identity.tenant_claim,
        user_claim=identity.user_claim,
        email=identity.email,
        name=identity.name,
        tenant_id=tenant_id,
        user_id=user_id,
    )
