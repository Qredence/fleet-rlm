"""Authentication routes."""

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException

from fleet_rlm.db import FleetRepository

from ..auth import AuthError, resolve_admitted_identity
from ..dependencies import ConfigDepsDep, HTTPIdentityDep, PersistenceDep, WebSocketTicketDepsDep
from ..schemas.base import AuthMeResponse, WebSocketTicketResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get(
    "/me",
    response_model=AuthMeResponse,
    responses={
        401: {"description": "Authentication is required or the provided token is invalid."},
        403: {"description": "The authenticated tenant or user is not admitted to Fleet RLM."},
        503: {"description": "Authentication or repository services are not configured yet."},
    },
)
async def get_me(
    identity: HTTPIdentityDep,
    config_deps: ConfigDepsDep,
    persistence: PersistenceDep,
) -> AuthMeResponse:
    """Return the authenticated identity and any admitted control-plane IDs."""
    persisted_identity = None
    if config_deps.config.auth_required:
        if not isinstance(persistence, FleetRepository):
            raise HTTPException(
                status_code=503,
                detail="Database repository unavailable for tenant admission.",
            )
        try:
            persisted_identity = await resolve_admitted_identity(persistence, identity)
        except AuthError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    return AuthMeResponse(
        tenant_claim=identity.tenant_claim,
        user_claim=identity.user_claim,
        email=identity.email,
        name=identity.name,
        tenant_id=(str(persisted_identity.tenant_id) if persisted_identity is not None else None),
        user_id=(str(persisted_identity.user_id) if persisted_identity is not None else None),
    )


@router.post(
    "/ws-ticket",
    response_model=WebSocketTicketResponse,
    responses={
        401: {"description": "Authentication is required or the provided token is invalid."},
        403: {"description": "The authenticated tenant or user is not admitted to Fleet RLM."},
        503: {"description": "Authentication or repository services are not configured yet."},
    },
)
async def create_ws_ticket(
    identity: HTTPIdentityDep,
    config_deps: ConfigDepsDep,
    persistence: PersistenceDep,
    ws_ticket_deps: WebSocketTicketDepsDep,
) -> WebSocketTicketResponse:
    """Exchange an authenticated HTTP identity for a one-time WebSocket ticket."""
    if config_deps.config.auth_required:
        if not isinstance(persistence, FleetRepository):
            raise HTTPException(
                status_code=503,
                detail="Database repository unavailable for tenant admission.",
            )
        try:
            await resolve_admitted_identity(persistence, identity)
        except AuthError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    ticket, expires_at = ws_ticket_deps.tickets.issue(identity)
    return WebSocketTicketResponse(
        ticket=ticket,
        expires_at=datetime.fromtimestamp(expires_at, tz=UTC),
    )
