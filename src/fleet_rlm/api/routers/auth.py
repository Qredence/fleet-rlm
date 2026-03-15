"""Authentication routes."""

from fastapi import APIRouter, HTTPException

from ..auth import AuthError, resolve_admitted_identity
from ..dependencies import HTTPIdentityDep, RepositoryDep, ServerStateDep
from ..schemas.core import AuthMeResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/me", response_model=AuthMeResponse)
async def get_me(
    identity: HTTPIdentityDep,
    state: ServerStateDep,
    repository: RepositoryDep,
) -> AuthMeResponse:
    persisted_identity = None
    if state.config.auth_mode == "entra":
        if repository is None:
            raise HTTPException(
                status_code=503,
                detail="Database repository unavailable for Entra tenant admission.",
            )
        try:
            persisted_identity = await resolve_admitted_identity(repository, identity)
        except AuthError as exc:
            raise HTTPException(
                status_code=exc.status_code, detail=exc.message
            ) from exc

    return AuthMeResponse(
        tenant_claim=identity.tenant_claim,
        user_claim=identity.user_claim,
        email=identity.email,
        name=identity.name,
        tenant_id=(
            str(persisted_identity.tenant_id)
            if persisted_identity is not None
            else None
        ),
        user_id=(
            str(persisted_identity.user_id) if persisted_identity is not None else None
        ),
    )
