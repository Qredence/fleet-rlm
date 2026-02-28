"""Stub router for Authentication."""

from fastapi import APIRouter

from ..deps import HTTPIdentityDep
from ..schemas.core import AuthLoginResponse, AuthLogoutResponse, AuthMeResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=AuthLoginResponse)
async def login() -> AuthLoginResponse:
    return AuthLoginResponse(token="dummy_token")


@router.post("/logout", response_model=AuthLogoutResponse)
async def logout() -> AuthLogoutResponse:
    return AuthLogoutResponse(status="ok")


@router.get("/me", response_model=AuthMeResponse)
async def get_me(identity: HTTPIdentityDep) -> AuthMeResponse:
    return AuthMeResponse(
        tenant_claim=identity.tenant_claim,
        user_claim=identity.user_claim,
        email=identity.email,
        name=identity.name,
    )
