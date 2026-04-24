"""Router for execution run steps."""

from __future__ import annotations

from typing import Annotated, Any, TypeAlias
import uuid

from fastapi import APIRouter, HTTPException, Path, Query

from fleet_rlm.integrations.database.types import IdentityUpsertResult

from ..auth import AuthError, resolve_admitted_identity
from ..dependencies import HTTPIdentityDep, RepositoryDep, ServerStateDep
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
        raise HTTPException(status_code=404, detail="Run not found") from exc


async def _resolve_persisted_identity(
    *,
    state: ServerStateDep,
    repository: RepositoryDep,
    identity: HTTPIdentityDep,
) -> IdentityUpsertResult | None:
    if repository is None:
        return None
    if state.config.auth_mode == "entra":
        try:
            return await resolve_admitted_identity(repository, identity)
        except AuthError as exc:
            raise HTTPException(
                status_code=exc.status_code,
                detail=exc.message,
            ) from exc
    return await repository.upsert_identity(
        entra_tenant_id=identity.tenant_claim,
        entra_user_id=identity.user_claim,
        email=identity.email,
        full_name=identity.name,
    )


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
    run_id: Annotated[
        str, Path(description="Identifier of the run whose steps to list.")
    ],
    limit: Annotated[int, Query(ge=1, le=200, description="Page size")] = 50,
    offset: Annotated[int, Query(ge=0, description="Pagination offset")] = 0,
) -> RunStepListResponse:
    """Return paginated execution steps for a run."""
    run_uuid = _parse_run_uuid(run_id)

    persisted_identity = await _resolve_persisted_identity(
        state=state,
        repository=repository,
        identity=identity,
    )
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
        raise HTTPException(status_code=404, detail="Run not found")

    steps = await repository.get_run_steps(
        tenant_id=persisted_identity.tenant_id,
        run_id=run_uuid,
        workspace_id=persisted_identity.workspace_id,
        created_by_user_id=persisted_identity.user_id,
    )

    total = len(steps)
    paginated = steps[offset : offset + limit]

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
            for s in paginated
        ],
        total=total,
        offset=offset,
        limit=limit,
        has_more=(offset + limit) < total,
    )
