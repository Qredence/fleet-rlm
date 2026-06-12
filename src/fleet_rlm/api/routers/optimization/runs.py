"""Run endpoints for GEPA prompt optimization."""

from __future__ import annotations

import json
import logging
from typing import Annotated, cast

from fastapi import (
    APIRouter,
    BackgroundTasks,
    HTTPException,
    Query,
)
from fastapi import (
    Path as ApiPath,
)

from fleet_rlm.integrations.database import OptimizationRunStatus

from ...dependencies import ConfigDepsDep, HTTPIdentityDep, PersistenceDep, PersistenceDepsDep
from ...schemas.optimization import (
    EvaluationResultItem,
    EvaluationResultsResponse,
    GEPAOptimizationRequest,
    GEPAOptimizationResponse,
    OptimizationPromotionDraftResponse,
    OptimizationRunCreatedResponse,
    OptimizationRunDetailResponse,
    OptimizationRunResponse,
    PromptSnapshotItem,
    RunComparisonItem,
    RunComparisonResponse,
)
from ._deps import (
    AUTH_ERROR_RESPONSES,
    OpenAPIResponses,
    _db_run_to_response,
    _resolve_persisted_identity,
    parse_run_uuid,
)
from .orchestration import (
    blocking_optimization_response,
    create_async_run_and_enqueue,
    create_blocking_run_record,
    ensure_optimizer_runtime_available,
    execute_blocking_optimization,
    failed_blocking_optimization_response,
    mark_blocking_run_complete,
    mark_blocking_run_failed,
    prepare_optimization_request,
)
from .run_details import (
    build_optimization_run_detail,
    create_or_load_promotion_draft,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/run",
    response_model=GEPAOptimizationResponse,
    responses=cast(
        OpenAPIResponses,
        {
            **AUTH_ERROR_RESPONSES,
            400: {"description": "Invalid optimization parameters."},
            503: {"description": "GEPA optimization is unavailable in this environment."},
        },
    ),
)
async def run_optimization(
    request: GEPAOptimizationRequest,
    config_deps: ConfigDepsDep,
    identity: HTTPIdentityDep,
    persistence: PersistenceDep,
    persistence_deps: PersistenceDepsDep,
) -> GEPAOptimizationResponse:
    """Trigger a blocking GEPA prompt optimization run."""
    persisted_identity = await _resolve_persisted_identity(
        config_deps=config_deps,
        persistence=persistence,
        identity=identity,
    )
    ensure_optimizer_runtime_available()
    prepared = await prepare_optimization_request(
        request=request,
        persistence=persistence,
        persistence_deps=persistence_deps,
        persisted_identity=persisted_identity,
    )
    db_run_id = await create_blocking_run_record(
        request=request,
        persistence=persistence,
        persisted_identity=persisted_identity,
        program_spec=prepared.program_spec,
        dataset_ref=prepared.dataset_ref,
        reflection_lm_config=prepared.reflection_lm_config,
    )

    try:
        result = await execute_blocking_optimization(
            request=request,
            dataset=prepared.dataset_path,
            output_path=prepared.output_path,
            resolved_skill_path=prepared.skill_path,
            program_spec=prepared.program_spec,
            reflection_lm_config=prepared.reflection_lm_config,
        )
    except Exception as exc:
        logger.exception("GEPA optimization failed")
        await mark_blocking_run_failed(
            db_run_id=db_run_id,
            persistence=persistence,
            persisted_identity=persisted_identity,
            error=str(exc),
        )
        return failed_blocking_optimization_response(
            exc=exc,
            request=request,
            program_spec=prepared.program_spec,
        )

    await mark_blocking_run_complete(
        db_run_id=db_run_id,
        persistence=persistence,
        persisted_identity=persisted_identity,
        result=result,
    )
    return blocking_optimization_response(
        result=result,
        request=request,
        program_spec=prepared.program_spec,
    )


# ── Async run endpoints ──────────────────────────────────────────────


@router.post(
    "/runs",
    response_model=OptimizationRunCreatedResponse,
    responses=cast(
        OpenAPIResponses,
        {
            **AUTH_ERROR_RESPONSES,
            400: {"description": "Invalid optimization parameters."},
            503: {"description": "GEPA optimization is unavailable in this environment."},
        },
    ),
)
async def create_optimization_run(
    request: GEPAOptimizationRequest,
    background_tasks: BackgroundTasks,
    config_deps: ConfigDepsDep,
    identity: HTTPIdentityDep,
    persistence: PersistenceDep,
    persistence_deps: PersistenceDepsDep,
) -> OptimizationRunCreatedResponse:
    """Create a non-blocking prompt optimization run.

    Returns immediately with the run_id.  The optimization executes as a
    background task.  Poll ``GET /runs/{run_id}`` for progress and results.
    """
    persisted_identity = await _resolve_persisted_identity(
        config_deps=config_deps,
        persistence=persistence,
        identity=identity,
    )
    ensure_optimizer_runtime_available()
    return await create_async_run_and_enqueue(
        request=request,
        background_tasks=background_tasks,
        persistence=persistence,
        persistence_deps=persistence_deps,
        persisted_identity=persisted_identity,
    )


@router.get(
    "/runs",
    response_model=list[OptimizationRunResponse],
    responses=cast(
        OpenAPIResponses,
        {
            **AUTH_ERROR_RESPONSES,
            400: {"description": "Invalid status filter."},
        },
    ),
)
async def list_runs(
    config_deps: ConfigDepsDep,
    identity: HTTPIdentityDep,
    persistence: PersistenceDep,
    status: Annotated[str | None, Query(description="Filter by status: running, completed, failed")] = None,
    limit: Annotated[int, Query(ge=1, le=200, description="Maximum number of runs to return.")] = 50,
    offset: Annotated[int, Query(ge=0, description="Pagination offset into the run list.")] = 0,
) -> list[OptimizationRunResponse]:
    """List optimization runs, most recent first."""
    persisted_identity = await _resolve_persisted_identity(
        config_deps=config_deps,
        persistence=persistence,
        identity=identity,
    )
    status_filter = None
    if status:
        try:
            status_filter = OptimizationRunStatus(status)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid status filter: {status!r}") from exc
    runs = await persistence.list_optimization_runs(
        tenant_id=persisted_identity.tenant_id,
        workspace_id=persisted_identity.workspace_id,
        created_by_user_id=persisted_identity.user_id,
        status=status_filter,
        limit=limit,
        offset=offset,
    )
    return [_db_run_to_response(r) for r in runs]


@router.get(
    "/runs/compare",
    response_model=RunComparisonResponse,
    responses=cast(
        OpenAPIResponses,
        {
            **AUTH_ERROR_RESPONSES,
            400: {"description": "Invalid run_ids parameter."},
        },
    ),
)
async def compare_runs(
    config_deps: ConfigDepsDep,
    identity: HTTPIdentityDep,
    persistence: PersistenceDep,
    run_ids: Annotated[str, Query(description="Comma-separated run IDs to compare (max 5).")],
) -> RunComparisonResponse:
    """Compare prompt diffs and scores across optimization runs."""
    persisted_identity = await _resolve_persisted_identity(
        config_deps=config_deps,
        persistence=persistence,
        identity=identity,
    )

    raw_ids = [s.strip() for s in run_ids.split(",") if s.strip()]
    if not raw_ids:
        raise HTTPException(status_code=400, detail="run_ids is required.")
    if len(raw_ids) > 5:
        raise HTTPException(status_code=400, detail="Maximum 5 runs can be compared at once.")

    comparison_items: list[RunComparisonItem] = []
    for raw_id in raw_ids:
        run_uuid = parse_run_uuid(raw_id)
        run_row = await persistence.get_optimization_run(
            tenant_id=persisted_identity.tenant_id,
            run_id=run_uuid,
            workspace_id=persisted_identity.workspace_id,
            created_by_user_id=persisted_identity.user_id,
        )
        if run_row is None:
            raise HTTPException(status_code=400, detail=f"Run {raw_id} not found.")
        snapshots = await persistence.get_prompt_snapshots(
            tenant_id=persisted_identity.tenant_id,
            run_id=run_uuid,
            workspace_id=persisted_identity.workspace_id,
            created_by_user_id=persisted_identity.user_id,
        )
        comparison_items.append(
            RunComparisonItem(
                run_id=str(run_row.id),
                program_spec=run_row.program_spec,
                validation_score=run_row.validation_score,
                prompt_snapshots=[
                    PromptSnapshotItem(
                        predictor_name=s.predictor_name,
                        prompt_type=s.prompt_type.value if hasattr(s.prompt_type, "value") else str(s.prompt_type),
                        prompt_text=s.prompt_text,
                    )
                    for s in snapshots
                ],
            )
        )
    return RunComparisonResponse(runs=comparison_items)


@router.get(
    "/runs/{run_id}",
    response_model=OptimizationRunResponse,
    responses=cast(
        OpenAPIResponses,
        {
            **AUTH_ERROR_RESPONSES,
            404: {"description": "Run not found."},
        },
    ),
)
async def get_run(
    config_deps: ConfigDepsDep,
    identity: HTTPIdentityDep,
    persistence: PersistenceDep,
    run_id: Annotated[str, ApiPath(description="Identifier of the optimization run to fetch.")],
) -> OptimizationRunResponse:
    """Get a single optimization run by ID."""
    persisted_identity = await _resolve_persisted_identity(
        config_deps=config_deps,
        persistence=persistence,
        identity=identity,
    )
    run_uuid = parse_run_uuid(run_id)
    row = await persistence.get_optimization_run(
        tenant_id=persisted_identity.tenant_id,
        run_id=run_uuid,
        workspace_id=persisted_identity.workspace_id,
        created_by_user_id=persisted_identity.user_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail=f"Optimization run {run_id} not found.")
    return _db_run_to_response(row)


@router.get(
    "/runs/{run_id}/details",
    response_model=OptimizationRunDetailResponse,
    responses=cast(
        OpenAPIResponses,
        {
            **AUTH_ERROR_RESPONSES,
            404: {"description": "Run not found."},
        },
    ),
)
async def get_run_details(
    config_deps: ConfigDepsDep,
    identity: HTTPIdentityDep,
    persistence: PersistenceDep,
    run_id: Annotated[str, ApiPath(description="Identifier of the optimization run to inspect.")],
) -> OptimizationRunDetailResponse:
    """Get a detailed GEPA improvement report for a single optimization run."""
    persisted_identity = await _resolve_persisted_identity(
        config_deps=config_deps,
        persistence=persistence,
        identity=identity,
    )
    run_uuid = parse_run_uuid(run_id)
    row = await persistence.get_optimization_run(
        tenant_id=persisted_identity.tenant_id,
        run_id=run_uuid,
        workspace_id=persisted_identity.workspace_id,
        created_by_user_id=persisted_identity.user_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail=f"Optimization run {run_id} not found.")
    snapshots = await persistence.get_prompt_snapshots(
        tenant_id=persisted_identity.tenant_id,
        run_id=run_uuid,
        workspace_id=persisted_identity.workspace_id,
        created_by_user_id=persisted_identity.user_id,
    )
    return build_optimization_run_detail(
        run=_db_run_to_response(row),
        prompt_snapshots=snapshots,
    )


@router.post(
    "/runs/{run_id}/promotion-drafts",
    response_model=OptimizationPromotionDraftResponse,
    responses=cast(
        OpenAPIResponses,
        {
            **AUTH_ERROR_RESPONSES,
            404: {"description": "Run not found."},
        },
    ),
)
async def create_run_promotion_draft(
    config_deps: ConfigDepsDep,
    identity: HTTPIdentityDep,
    persistence: PersistenceDep,
    run_id: Annotated[str, ApiPath(description="Identifier of the optimization run to draft for promotion.")],
) -> OptimizationPromotionDraftResponse:
    """Create or load a non-mutating draft promotion artifact for an optimization run."""
    persisted_identity = await _resolve_persisted_identity(
        config_deps=config_deps,
        persistence=persistence,
        identity=identity,
    )
    run_uuid = parse_run_uuid(run_id)
    row = await persistence.get_optimization_run(
        tenant_id=persisted_identity.tenant_id,
        run_id=run_uuid,
        workspace_id=persisted_identity.workspace_id,
        created_by_user_id=persisted_identity.user_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail=f"Optimization run {run_id} not found.")
    status = row.status.value if hasattr(row.status, "value") else str(row.status)
    if status != OptimizationRunStatus.COMPLETED.value:
        raise HTTPException(
            status_code=409,
            detail="Promotion drafts are only available for completed optimization runs.",
        )
    return create_or_load_promotion_draft(
        _db_run_to_response(row),
        tenant_id=str(persisted_identity.tenant_id),
        workspace_id=str(persisted_identity.workspace_id),
    )


# ── Evaluation result + run comparison endpoints ─────────────────────


@router.get(
    "/runs/{run_id}/results",
    response_model=EvaluationResultsResponse,
    responses=cast(
        OpenAPIResponses,
        {
            **AUTH_ERROR_RESPONSES,
            404: {"description": "Run not found."},
        },
    ),
)
async def get_run_results(
    config_deps: ConfigDepsDep,
    identity: HTTPIdentityDep,
    persistence: PersistenceDep,
    run_id: Annotated[
        str,
        ApiPath(description="Identifier of the optimization run whose results to list."),
    ],
    limit: Annotated[
        int,
        Query(ge=1, le=500, description="Maximum number of evaluation rows to return."),
    ] = 100,
    offset: Annotated[int, Query(ge=0, description="Pagination offset into the evaluation results.")] = 0,
) -> EvaluationResultsResponse:
    """Return per-example evaluation results for an optimization run."""
    persisted_identity = await _resolve_persisted_identity(
        config_deps=config_deps,
        persistence=persistence,
        identity=identity,
    )
    run_uuid = parse_run_uuid(run_id)
    if (
        await persistence.get_optimization_run(
            tenant_id=persisted_identity.tenant_id,
            run_id=run_uuid,
            workspace_id=persisted_identity.workspace_id,
            created_by_user_id=persisted_identity.user_id,
        )
        is None
    ):
        raise HTTPException(status_code=404, detail=f"Optimization run {run_id} not found.")
    items, total = await persistence.get_evaluation_results(
        tenant_id=persisted_identity.tenant_id,
        run_id=run_uuid,
        workspace_id=persisted_identity.workspace_id,
        created_by_user_id=persisted_identity.user_id,
        limit=limit,
        offset=offset,
    )
    return EvaluationResultsResponse(
        items=[
            EvaluationResultItem(
                id=str(r.id),
                example_index=r.example_index,
                input_data=json.dumps(r.input_data),
                expected_output=r.expected_output,
                predicted_output=r.predicted_output,
                score=r.score,
            )
            for r in items
        ],
        total=total,
        offset=offset,
        limit=limit,
        has_more=(offset + limit) < total,
    )
