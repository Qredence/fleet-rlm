"""Router for execution run steps."""

from __future__ import annotations

from typing import Annotated, Any, TypeAlias
import uuid

from fastapi import APIRouter, HTTPException, Path, Query

from ..dependencies import (
    HTTPIdentityDep,
    PersistedIdentityDep,
    RepositoryDep,
    ServerStateDep,
)
from ..schemas.core import RunStepItem, RunStepListResponse

router = APIRouter(
    prefix="/runs",
    tags=["runs"],
)

OpenAPIResponses: TypeAlias = dict[int | str, dict[str, Any]]

AUTH_ERROR_RESPONSES: OpenAPIResponses = {
    401: {
        "description": "Authentication is required or the provided token is invalid."
    },
    503: {
        "description": "Run services are unavailable because server startup is incomplete."
    },
}

RUN_ERROR_RESPONSES: OpenAPIResponses = {
    **AUTH_ERROR_RESPONSES,
    404: {"description": "Run not found."},
}


def _parse_run_uuid(run_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Run not found.") from exc


@router.get(
    "/{run_id}/steps",
    response_model=RunStepListResponse,
    responses=RUN_ERROR_RESPONSES,
    summary="List run steps",
    description="Paginated execution trace steps for a run with step_type, tool_name, tokens, and latency.",
)
async def get_run_steps(
    state: ServerStateDep,
    identity: HTTPIdentityDep,
    repository: RepositoryDep,
    persisted_identity: PersistedIdentityDep,
    run_id: Annotated[
        str, Path(description="Identifier of the run whose steps to list.")
    ],
    limit: Annotated[int, Query(ge=1, le=200, description="Page size")] = 50,
    offset: Annotated[int, Query(ge=0, description="Pagination offset")] = 0,
) -> RunStepListResponse:
    """Return paginated execution steps for a run."""
    run_uuid = _parse_run_uuid(run_id)

    if repository is None or persisted_identity is None:
        raise HTTPException(
            status_code=503,
            detail="Database persistence is unavailable.",
        )

    run = await repository.get_run(
        tenant_id=persisted_identity.tenant_id,
        run_id=run_uuid,
        workspace_id=persisted_identity.workspace_id,
        created_by_user_id=persisted_identity.user_id,
    )
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found.")

    steps, total = await repository.get_run_steps_paginated(
        tenant_id=persisted_identity.tenant_id,
        run_id=run_uuid,
        workspace_id=persisted_identity.workspace_id,
        created_by_user_id=persisted_identity.user_id,
        limit=limit,
        offset=offset,
    )

    return RunStepListResponse(
        items=[
            RunStepItem(
                id=str(s.id),
                step_index=s.step_index,
                step_type=s.step_type.value
                if hasattr(s.step_type, "value")
                else str(s.step_type),
                tool_name=s.tool_name,
                tokens_in=s.tokens_in,
                tokens_out=s.tokens_out,
                latency_ms=s.latency_ms,
                created_at=s.created_at.isoformat(),
            )
            for s in steps
        ],
        total=total,
        offset=offset,
        limit=limit,
        has_more=(offset + limit) < total,
    )
