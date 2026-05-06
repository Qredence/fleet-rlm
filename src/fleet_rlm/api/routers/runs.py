"""Router for execution run steps."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Query

from ..dependencies import (
    HTTPIdentityDep,
    PersistedIdentityDep,
    PersistenceDep,
)
from ..runtime_services.run_service import RunService
from ..schemas.sandbox import RunStepListResponse
from ._types import OpenAPIResponses

router = APIRouter(
    prefix="/runs",
    tags=["runs"],
)


AUTH_ERROR_RESPONSES: OpenAPIResponses = {
    401: {"description": "Authentication is required or the provided token is invalid."},
    503: {"description": "Run services are unavailable because server startup is incomplete."},
}

RUN_ERROR_RESPONSES: OpenAPIResponses = {
    **AUTH_ERROR_RESPONSES,
    404: {"description": "Run not found."},
}


@router.get(
    "/{run_id}/steps",
    response_model=RunStepListResponse,
    responses=RUN_ERROR_RESPONSES,
    summary="List run steps",
    description="Paginated execution trace steps for a run with step_type, tool_name, tokens, and latency.",
)
async def get_run_steps(
    identity: HTTPIdentityDep,
    persistence: PersistenceDep,
    persisted_identity: PersistedIdentityDep,
    run_id: Annotated[str, Path(description="Identifier of the run whose steps to list.")],
    limit: Annotated[int, Query(ge=1, le=200, description="Page size")] = 50,
    offset: Annotated[int, Query(ge=0, description="Pagination offset")] = 0,
) -> RunStepListResponse:
    """Return paginated execution steps for a run."""
    return await RunService(persistence).get_run_steps(
        persisted_identity=persisted_identity,
        run_id=run_id,
        limit=limit,
        offset=offset,
    )
