"""Router for memory browsing."""

from __future__ import annotations

from typing import Annotated, Any, TypeAlias

from fastapi import APIRouter, HTTPException, Query

from fleet_rlm.integrations.database import MemoryScope

from ..dependencies import PersistedIdentityDep, RepositoryDep
from ..schemas.core import MemoryItemResponse, MemoryListResponse

router = APIRouter(
    prefix="/memory",
    tags=["memory"],
)

OpenAPIResponses: TypeAlias = dict[int | str, dict[str, Any]]

AUTH_ERROR_RESPONSES: OpenAPIResponses = {
    401: {
        "description": "Authentication is required or the provided token is invalid."
    },
    503: {
        "description": "Memory services are unavailable because server startup is incomplete."
    },
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
    repository: RepositoryDep,
    persisted_identity: PersistedIdentityDep,
    scope: Annotated[
        str | None,
        Query(
            description="Filter by memory scope (user, tenant, workspace, run, session)."
        ),
    ] = None,
    scope_id: Annotated[
        str | None,
        Query(description="Filter by scope identifier."),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=200, description="Page size")] = 100,
    offset: Annotated[int, Query(ge=0, description="Pagination offset")] = 0,
) -> MemoryListResponse:
    """Return memory items for the authenticated user, optionally filtered."""
    scope_filter = None
    if scope is not None:
        try:
            scope_filter = MemoryScope(scope)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid scope: {scope}",
            ) from exc

    if repository is None or persisted_identity is None:
        raise HTTPException(
            status_code=503,
            detail="Database persistence is unavailable.",
        )

    items, total = await repository.list_memory_items_paginated(
        tenant_id=persisted_identity.tenant_id,
        workspace_id=persisted_identity.workspace_id,
        user_id=persisted_identity.user_id,
        scope=scope_filter,
        scope_id=scope_id,
        limit=limit,
        offset=offset,
    )

    return MemoryListResponse(
        items=[
            MemoryItemResponse(
                id=str(item.id),
                scope=item.scope.value
                if hasattr(item.scope, "value")
                else str(item.scope),
                scope_id=item.scope_id,
                kind=item.kind.value if hasattr(item.kind, "value") else str(item.kind),
                source=item.source.value
                if hasattr(item.source, "value")
                else str(item.source),
                status=item.status.value
                if hasattr(item.status, "value")
                else str(item.status),
                content_text=item.content_text,
                importance=item.importance,
                tags=list(item.tags) if item.tags is not None else [],
                created_at=item.created_at.isoformat(),
            )
            for item in items
        ],
        total=total,
        offset=offset,
        limit=limit,
        has_more=offset + len(items) < total,
    )
