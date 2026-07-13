"""Request identity: Neon JWT (prod) or synthetic headers (explicit dev mode)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Annotated, Literal, NoReturn
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from fastapi import Header, HTTPException, Request

from fleet_rlm.api.auth_errors import (
    PUBLIC_WORKSPACE_MISMATCH_DETAIL,
    AuthError,
)
from fleet_rlm.api.neon_auth import (
    NeonAuthVerifier,
    subject_to_user_id,
    tenant_to_workspace_id,
)
from fleet_rlm.config import Settings

logger = logging.getLogger(__name__)

# Stable defaults so local/dev runs are deterministic without Neon.
_DEFAULT_USER = uuid5(NAMESPACE_URL, "fleet-rlm/dev-user")
_DEFAULT_WORKSPACE = uuid5(NAMESPACE_URL, "fleet-rlm/dev-workspace")

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


def _correlation_id(request: Request) -> str:
    for header in ("x-request-id", "x-correlation-id"):
        value = request.headers.get(header)
        if value and value.strip():
            return value.strip()
    return str(uuid4())


def _raise_public_auth_error(request: Request, exc: AuthError) -> NoReturn:
    """Log internal cause; raise allowlisted public HTTPException."""
    cid = _correlation_id(request)
    logger.warning(
        "auth_failure correlation_id=%s status=%s kind=%s cause=%s",
        cid,
        exc.status_code,
        exc.kind,
        exc.message,
    )
    raise HTTPException(status_code=exc.status_code, detail=exc.public_detail) from exc


def _get_verifier(request: Request, settings: Settings) -> NeonAuthVerifier:
    verifier = getattr(request.app.state, "auth_verifier", None)
    if verifier is not None:
        return verifier
    from fleet_rlm.composition import is_live_mode

    if is_live_mode(request.app):
        raise HTTPException(status_code=503, detail="live composition is not ready")
    url = (settings.neon_auth_url or "").strip()
    if not url:
        raise AuthError(
            "FLEET_NEON_AUTH_URL is required when auth_mode=neon",
            status_code=503,
            kind="unavailable",
        )
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
        _raise_public_auth_error(request, exc)

    user_id = subject_to_user_id(claims.subject)
    # Workspace is server-derived from JWT/tenant config only — client header cannot escalate.
    # Optional header may be supplied for clients that mirror server state; it must match.
    tenant = (
        str(claims.raw.get("tenant") or claims.raw.get("workspace_id") or "").strip()
        or settings.neon_tenant_claim
        or "default"
    )
    workspace_id = tenant_to_workspace_id(tenant)
    if x_fleet_workspace_id is not None and x_fleet_workspace_id != workspace_id:
        raise HTTPException(
            status_code=403,
            detail=PUBLIC_WORKSPACE_MISMATCH_DETAIL,
        )
    # Ignore X-Fleet-User-Id in neon mode (identity comes only from JWT sub)

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
        from fleet_rlm.sessions.errors import SessionAccessDenied

        raise SessionAccessDenied()
