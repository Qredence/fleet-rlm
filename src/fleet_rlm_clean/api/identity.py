"""Request identity: Neon JWT (prod) or synthetic headers (explicit dev mode)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Literal
from uuid import UUID, uuid5, NAMESPACE_URL

from fastapi import Depends, Header, HTTPException, Request

from fleet_rlm_clean.api.auth_errors import AuthError
from fleet_rlm_clean.api.neon_auth import (
    DEFAULT_NEON_AUTH_URL,
    NeonAuthVerifier,
    subject_to_user_id,
    tenant_to_workspace_id,
)
from fleet_rlm_clean.config import Settings

# Stable defaults so local/dev runs are deterministic without Neon.
_DEFAULT_USER = uuid5(NAMESPACE_URL, "fleet-rlm-clean/dev-user")
_DEFAULT_WORKSPACE = uuid5(NAMESPACE_URL, "fleet-rlm-clean/dev-workspace")

AuthMode = Literal["dev", "neon"]


@dataclass(frozen=True, slots=True)
class RequestIdentity:
    """Stable identity fields for chat/files/skills (do not rename for callers)."""

    user_id: UUID
    workspace_id: UUID
    auth_mode: AuthMode = "dev"
    email: str | None = None
    name: str | None = None


def _settings(request: Request) -> Settings:
    return getattr(request.app.state, "settings", None) or Settings()


def _get_verifier(request: Request, settings: Settings) -> NeonAuthVerifier:
    verifier = getattr(request.app.state, "auth_verifier", None)
    if verifier is not None:
        return verifier
    url = settings.neon_auth_url or DEFAULT_NEON_AUTH_URL
    verifier = NeonAuthVerifier(neon_auth_url=url)
    request.app.state.auth_verifier = verifier
    return verifier


async def get_request_identity(
    request: Request,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    x_fleet_user_id: Annotated[UUID | None, Header(alias="X-Fleet-User-Id")] = None,
    x_fleet_workspace_id: Annotated[UUID | None, Header(alias="X-Fleet-Workspace-Id")] = None,
) -> RequestIdentity:
    """Resolve identity from Neon JWT (auth_mode=neon) or synthetic headers (dev)."""
    settings = _settings(request)
    mode: AuthMode = "neon" if settings.auth_mode == "neon" else "dev"

    if mode == "dev":
        return RequestIdentity(
            user_id=x_fleet_user_id or _DEFAULT_USER,
            workspace_id=x_fleet_workspace_id or _DEFAULT_WORKSPACE,
            auth_mode="dev",
        )

    # neon mode: bearer required; synthetic-only path disabled
    try:
        verifier = _get_verifier(request, settings)
        claims = await verifier.authenticate_bearer(authorization)
    except AuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    user_id = subject_to_user_id(claims.subject)
    # Workspace: explicit header wins if present; else tenant claim / default
    if x_fleet_workspace_id is not None:
        workspace_id = x_fleet_workspace_id
    else:
        tenant = (
            str(claims.raw.get("tenant") or claims.raw.get("workspace_id") or "").strip()
            or settings.neon_tenant_claim
            or "default"
        )
        workspace_id = tenant_to_workspace_id(tenant)

    return RequestIdentity(
        user_id=user_id,
        workspace_id=workspace_id,
        auth_mode="neon",
        email=claims.email,
        name=claims.name,
    )


def require_session_access(
    *,
    session_user_id: UUID,
    session_workspace_id: UUID,
    identity: RequestIdentity,
) -> None:
    """Reject cross-workspace/user session access (public shape: not found)."""
    if session_user_id != identity.user_id or session_workspace_id != identity.workspace_id:
        from fleet_rlm_clean.api.auth_errors import SessionAccessDenied

        raise SessionAccessDenied()


# FastAPI dependency alias
RequestIdentityDep = Annotated[RequestIdentity, Depends(get_request_identity)]
