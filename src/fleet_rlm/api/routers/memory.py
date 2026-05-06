"""Router for memory browsing."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from ..dependencies import PersistedIdentityDep, PersistenceDep
from ..runtime_services.memory_service import MemoryService
from ..schemas.memory import MemoryListResponse
from ._types import OpenAPIResponses

router = APIRouter(
    prefix="/memory",
    tags=["memory"],
)


AUTH_ERROR_RESPONSES: OpenAPIResponses = {
    401: {"description": "Authentication is required or the provided token is invalid."},
    503: {"description": "Memory services are unavailable because server startup is incomplete."},
}

MEMORY_ERROR_RESPONSES: OpenAPIResponses = {
    **AUTH_ERROR_RESPONSES,
    400: {"description": "Invalid scope filter value."},
}


@router.get(
    "",
    response_model=MemoryListResponse,
    responses=MEMORY_ERROR_RESPONSES,
    summary="List memory items",
    description="Return memory items filtered by scope and scope_id. Without filters, returns all memory for the authenticated user.",
)
async def list_memory_items(
    persistence: PersistenceDep,
    persisted_identity: PersistedIdentityDep,
    scope: Annotated[
        str | None,
        Query(description="Filter by memory scope (user, tenant, workspace, run, session)."),
    ] = None,
    scope_id: Annotated[
        str | None,
        Query(description="Filter by scope identifier."),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=200, description="Page size")] = 100,
    offset: Annotated[int, Query(ge=0, description="Pagination offset")] = 0,
) -> MemoryListResponse:
    """Return memory items for the authenticated user, optionally filtered."""
    return await MemoryService(persistence).list_memory_items(
        persisted_identity=persisted_identity,
        scope=scope,
        scope_id=scope_id,
        limit=limit,
        offset=offset,
    )
