"""Router for Daytona sandbox management."""

from __future__ import annotations

from typing import Annotated, Any, TypeAlias

from fastapi import APIRouter, Query

from ..dependencies import HTTPIdentityDep, ServerStateDep
from ..runtime_services.sandboxes import load_sandbox_list
from ..schemas.core import SandboxListResponse

router = APIRouter(
    prefix="/sandboxes",
    tags=["sandboxes"],
)

OpenAPIResponses: TypeAlias = dict[int | str, dict[str, Any]]

AUTH_ERROR_RESPONSES: OpenAPIResponses = {
    401: {
        "description": "Authentication is required or the provided token is invalid."
    },
    503: {
        "description": "Sandbox services are unavailable because server startup is incomplete."
    },
}


@router.get(
    "",
    response_model=SandboxListResponse,
    responses=AUTH_ERROR_RESPONSES,
    summary="List sandboxes",
    description="List active Daytona sandboxes with id, state, created_at, and volume info.",
)
async def list_sandboxes(
    state: ServerStateDep,
    identity: HTTPIdentityDep,
    page: Annotated[
        int,
        Query(ge=1, description="Page number for pagination (starting from 1)."),
    ] = 1,
    limit: Annotated[
        int,
        Query(
            ge=1,
            le=1000,
            description="Maximum number of sandboxes per page.",
        ),
    ] = 100,
) -> SandboxListResponse:
    """Return a paginated list of active Daytona sandboxes."""
    _ = state
    _ = identity
    return await load_sandbox_list(page=page, limit=limit)
