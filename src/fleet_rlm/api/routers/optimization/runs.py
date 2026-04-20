"""Run endpoints for GEPA optimization."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from functools import partial
from pathlib import Path
from typing import Any, Literal, cast

from fastapi import (
    APIRouter,
    BackgroundTasks,
    HTTPException,
    Path as ApiPath,
    Query,
)

from fleet_rlm.integrations.database import OptimizationRunStatus
from fleet_rlm.integrations.database.types import OptimizationRunCreateRequest

from ...dependencies import HTTPIdentityDep, RepositoryDep, ServerStateDep
from ...runtime_services.common import run_blocking
from ...schemas.core import (
    EvaluationResultItem,
    EvaluationResultsResponse,
    GEPAOptimizationRequest,
    GEPAOptimizationResponse,
    OptimizationRunCreatedResponse,
    OptimizationRunResponse,
    PromptSnapshotItem,
    RunComparisonItem,
    RunComparisonResponse,
)
from ._deps import (
    AUTH_ERROR_RESPONSES,
    OPTIMIZATION_DATA_ROOT,
    OPTIMIZATION_TIMEOUT_SECONDS,
    OpenAPIResponses,
    _check_gepa_available,
    _db_run_to_response,
    _extract_metadata_str,
    _get_mlflow_status,
    _parse_uuid_id,
    _require_workspace_id,
    _resolve_dataset_request,
    _resolve_persisted_identity,
)
from .background import run_optimization_background

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Blocking optimization helpers
# ---------------------------------------------------------------------------


def _run_gepa_optimization(
    *,
    dataset_path: Path,
    program_spec: str,
    output_path: Path | None,
    auto: Literal["light", "medium", "heavy"],
    train_ratio: float,
) -> dict:
    """Blocking wrapper around optimize_program_with_gepa."""
    from fleet_rlm.runtime.quality.gepa_optimization import (
        optimize_program_with_gepa,
    )

    return optimize_program_with_gepa(
        dataset_path=dataset_path,
        program_spec=program_spec,
        output_path=output_path,
        auto=auto,
        train_ratio=train_ratio,
    )


def _run_module_optimization(
    *,
    module_slug: str,
    dataset_path: Path,
    output_path: Path | None,
    default_output_root: Path | None,
    auto: Literal["light", "medium", "heavy"],
    train_ratio: float,
    run_id: int | None = None,
) -> dict:
    """Blocking wrapper for registry-based module optimization."""
    from fleet_rlm.runtime.quality.module_registry import get_module_spec
    from fleet_rlm.runtime.quality.optimization_runner import run_module_optimization

    spec = get_module_spec(module_slug)
    if spec is None:
        raise ValueError(f"Unknown module slug: {module_slug!r}")
    return dict(
        run_module_optimization(
            spec,
            dataset_path=dataset_path,
            output_path=output_path,
            default_output_root=default_output_root,
            train_ratio=train_ratio,
            auto=auto,
            run_id=run_id,
        )
    )


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
            503: {
                "description": "GEPA optimization is unavailable in this environment."
            },
        },
    ),
)
async def run_optimization(
    request: GEPAOptimizationRequest,
    state: ServerStateDep,
    identity: HTTPIdentityDep,
    repository: RepositoryDep,
) -> GEPAOptimizationResponse:
    """Trigger a GEPA prompt optimization run."""
    persisted_identity = await _resolve_persisted_identity(
        state=state,
        repository=repository,
        identity=identity,
    )
    if not _check_gepa_available():
        raise HTTPException(
            status_code=503,
            detail="GEPA teleprompt module is not available.",
        )
    mlflow_configured, mlflow_enabled = _get_mlflow_status()
    if not mlflow_enabled:
        detail = (
            "MLflow is not enabled. GEPA optimization requires MLflow."
            if not mlflow_configured
            else "MLflow is unavailable. GEPA optimization requires a reachable MLflow tracking server."
        )
        raise HTTPException(
            status_code=503,
            detail=detail,
        )

    # Resolve program_spec from module_slug when provided
    effective_program_spec = request.program_spec
    if request.module_slug:
        from fleet_rlm.runtime.quality.module_registry import get_module_spec

        spec = get_module_spec(request.module_slug)
        if spec is None:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown module slug: {request.module_slug!r}",
            )
        effective_program_spec = spec.program_spec
    elif not request.program_spec:
        raise HTTPException(
            status_code=400,
            detail="Either module_slug or program_spec must be provided.",
        )

    dataset, dataset_ref = await _resolve_dataset_request(
        request,
        repository=repository,
        persisted_identity=persisted_identity,
    )
    base_root = os.path.realpath(os.fspath(OPTIMIZATION_DATA_ROOT))
    safe_root = os.path.join(base_root, "")

    output_path: Path | None = None
    if request.output_path:
        if os.path.isabs(request.output_path):
            raise HTTPException(
                status_code=400,
                detail="Absolute paths are not allowed. Use a relative path.",
            )
        resolved_output = os.path.realpath(
            os.path.join(safe_root, request.output_path)
        )
        if not resolved_output.startswith(safe_root):
            raise HTTPException(
                status_code=400,
                detail="Path escapes the allowed data directory.",
            )
        output_path = Path(resolved_output)

    db_run_id: str | int | None = None
    if repository is not None and persisted_identity is not None:
        try:
            workspace_id = _require_workspace_id(persisted_identity)
            dataset_uuid = (
                _parse_uuid_id(
                    request.dataset_id,
                    detail=f"Dataset {request.dataset_id} not found.",
                )
                if request.dataset_id is not None
                else None
            )
            created_run = await repository.create_optimization_run(
                OptimizationRunCreateRequest(
                    tenant_id=persisted_identity.tenant_id,
                    workspace_id=workspace_id,
                    created_by_user_id=persisted_identity.user_id,
                    optimizer="GEPA",
                    program_spec=effective_program_spec,
                    module_slug=request.module_slug,
                    dataset_id=dataset_uuid,
                    auto=request.auto,
                    train_ratio=request.train_ratio,
                    metadata_json={"dataset_path": dataset_ref},
                )
            )
            db_run_id = str(created_run.id)
        except Exception as exc:
            logger.exception(
                "Failed to create optimization run in Postgres", exc_info=exc
            )
    else:
        try:
            from fleet_rlm.integrations.local_store import (
                create_optimization_run as _db_create_run,
            )

            db_run_id = (
                await asyncio.to_thread(
                    _db_create_run,
                    program_spec=effective_program_spec,
                    auto=request.auto,
                    train_ratio=request.train_ratio,
                    module_slug=request.module_slug,
                    dataset_id=int(request.dataset_id)
                    if request.dataset_id is not None
                    else None,
                    dataset_path=dataset_ref,
                )
            ).id
        except Exception as exc:
            logger.exception(
                "Failed to create optimization run in local database", exc_info=exc
            )

    try:
        if request.module_slug:
            result = await run_blocking(
                partial(
                    _run_module_optimization,
                    module_slug=request.module_slug,
                    dataset_path=dataset,
                    output_path=output_path,
                    default_output_root=OPTIMIZATION_DATA_ROOT,
                    auto=request.auto,
                    train_ratio=request.train_ratio,
                    run_id=db_run_id if isinstance(db_run_id, int) else None,
                ),
                timeout=OPTIMIZATION_TIMEOUT_SECONDS,
            )
        else:
            result = await run_blocking(
                partial(
                    _run_gepa_optimization,
                    dataset_path=dataset,
                    program_spec=effective_program_spec,
                    output_path=output_path,
                    auto=request.auto,
                    train_ratio=request.train_ratio,
                ),
                timeout=OPTIMIZATION_TIMEOUT_SECONDS,
            )
    except Exception as exc:
        logger.exception("GEPA optimization failed")
        if db_run_id is not None:
            if (
                repository is not None
                and persisted_identity is not None
                and isinstance(db_run_id, str)
            ):
                try:
                    await repository.fail_optimization_run(
                        tenant_id=persisted_identity.tenant_id,
                        run_id=uuid.UUID(db_run_id),
                        workspace_id=persisted_identity.workspace_id,
                        created_by_user_id=persisted_identity.user_id,
                        error=str(exc),
                    )
                except Exception:
                    logger.exception(
                        "Failed to mark GEPA optimization run %s as failed in Postgres",
                        db_run_id,
                    )
            else:
                try:
                    from fleet_rlm.integrations.local_store import (
                        fail_optimization_run,
                    )

                    assert isinstance(db_run_id, int)
                    await asyncio.to_thread(
                        fail_optimization_run, db_run_id, error=str(exc)
                    )
                except Exception:
                    logger.exception(
                        "Failed to mark GEPA optimization run %s as failed in database",
                        db_run_id,
                    )
        return GEPAOptimizationResponse(
            ok=False,
            program_spec=effective_program_spec,
            train_examples=0,
            validation_examples=0,
            module_slug=request.module_slug,
            error=str(exc),
        )

    if db_run_id is not None:
        if (
            repository is not None
            and persisted_identity is not None
            and isinstance(db_run_id, str)
        ):
            try:
                run_uuid = uuid.UUID(db_run_id)
                await repository.save_evaluation_results(
                    tenant_id=persisted_identity.tenant_id,
                    run_id=run_uuid,
                    workspace_id=persisted_identity.workspace_id,
                    created_by_user_id=persisted_identity.user_id,
                    results=result.get("evaluation_results", []),
                )
                await repository.save_prompt_snapshots(
                    tenant_id=persisted_identity.tenant_id,
                    run_id=run_uuid,
                    workspace_id=persisted_identity.workspace_id,
                    created_by_user_id=persisted_identity.user_id,
                    snapshots=result.get("prompt_snapshots", []),
                )
                await repository.complete_optimization_run(
                    tenant_id=persisted_identity.tenant_id,
                    run_id=run_uuid,
                    workspace_id=persisted_identity.workspace_id,
                    created_by_user_id=persisted_identity.user_id,
                    train_examples=result.get("train_examples", 0),
                    validation_examples=result.get("validation_examples", 0),
                    validation_score=result.get("validation_score"),
                    output_path=result.get("output_path"),
                    manifest_path=result.get("manifest_path"),
                )
            except Exception:
                logger.exception(
                    "Failed to mark GEPA optimization run %s as complete in Postgres",
                    db_run_id,
                )
        else:
            try:
                from fleet_rlm.integrations.local_store import (
                    complete_optimization_run,
                )

                assert isinstance(db_run_id, int)
                await asyncio.to_thread(
                    complete_optimization_run,
                    db_run_id,
                    train_examples=result.get("train_examples", 0),
                    validation_examples=result.get("validation_examples", 0),
                    validation_score=result.get("validation_score"),
                    output_path=result.get("output_path"),
                    manifest_path=result.get("manifest_path"),
                )
            except Exception:
                logger.exception(
                    "Failed to mark GEPA optimization run %s as complete", db_run_id
                )

    return GEPAOptimizationResponse(
        ok=True,
        optimizer=result.get("optimizer", "GEPA"),
        program_spec=result.get("program_spec", effective_program_spec),
        train_examples=result.get("train_examples", 0),
        validation_examples=result.get("validation_examples", 0),
        validation_score=result.get("validation_score"),
        output_path=result.get("output_path"),
        manifest_path=result.get("manifest_path"),
        module_slug=request.module_slug,
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
            503: {
                "description": "GEPA optimization is unavailable in this environment."
            },
        },
    ),
)
async def create_optimization_run(
    request: GEPAOptimizationRequest,
    background_tasks: BackgroundTasks,
    state: ServerStateDep,
    identity: HTTPIdentityDep,
    repository: RepositoryDep,
) -> OptimizationRunCreatedResponse:
    """Create a non-blocking GEPA optimization run.

    Returns immediately with the run_id.  The optimization executes as a
    background task.  Poll ``GET /runs/{run_id}`` for progress and results.
    """
    persisted_identity = await _resolve_persisted_identity(
        state=state,
        repository=repository,
        identity=identity,
    )
    if not _check_gepa_available():
        raise HTTPException(
            status_code=503, detail="GEPA teleprompt module is not available."
        )
    mlflow_configured, mlflow_enabled = _get_mlflow_status()
    if not mlflow_enabled:
        detail = (
            "MLflow is not enabled. GEPA optimization requires MLflow."
            if not mlflow_configured
            else "MLflow is unavailable. GEPA optimization requires a reachable MLflow tracking server."
        )
        raise HTTPException(status_code=503, detail=detail)

    # Resolve program spec
    effective_program_spec = request.program_spec
    if request.module_slug:
        from fleet_rlm.runtime.quality.module_registry import get_module_spec

        spec = get_module_spec(request.module_slug)
        if spec is None:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown module slug: {request.module_slug!r}",
            )
        effective_program_spec = spec.program_spec
    elif not request.program_spec:
        raise HTTPException(
            status_code=400,
            detail="Either module_slug or program_spec must be provided.",
        )

    # Path validation
    dataset, dataset_ref = await _resolve_dataset_request(
        request,
        repository=repository,
        persisted_identity=persisted_identity,
    )
    base_root = os.path.realpath(os.fspath(OPTIMIZATION_DATA_ROOT))
    safe_root = os.path.join(base_root, "")

    output_path: Path | None = None
    if request.output_path:
        if os.path.isabs(request.output_path):
            raise HTTPException(
                status_code=400,
                detail="Absolute paths are not allowed. Use a relative path.",
            )
        resolved_output = os.path.realpath(
            os.path.join(safe_root, request.output_path)
        )
        try:
            stays_under_data_root = (
                os.path.commonpath([base_root, resolved_output]) == base_root
            )
        except ValueError:
            stays_under_data_root = False
        if not stays_under_data_root:
            raise HTTPException(
                status_code=400, detail="Path escapes the allowed data directory."
            )
        output_path = Path(resolved_output)

    # Create DB record
    if repository is not None and persisted_identity is not None:
        workspace_id = _require_workspace_id(persisted_identity)
        dataset_uuid = (
            _parse_uuid_id(
                request.dataset_id,
                detail=f"Dataset {request.dataset_id} not found.",
            )
            if request.dataset_id is not None
            else None
        )
        db_row = await repository.create_optimization_run(
            OptimizationRunCreateRequest(
                tenant_id=persisted_identity.tenant_id,
                workspace_id=workspace_id,
                created_by_user_id=persisted_identity.user_id,
                optimizer="GEPA",
                program_spec=effective_program_spec,
                module_slug=request.module_slug,
                dataset_id=dataset_uuid,
                auto=request.auto,
                train_ratio=request.train_ratio,
                metadata_json={"dataset_path": dataset_ref},
            )
        )
        run_id = str(db_row.id)
        background_tasks.add_task(
            run_optimization_background,
            run_id=run_id,
            persistence="repo",
            repository=repository,
            identity=persisted_identity,
            module_slug=request.module_slug,
            dataset_path=dataset,
            program_spec=effective_program_spec,
            output_path=output_path,
            default_output_root=OPTIMIZATION_DATA_ROOT,
            auto=request.auto,
            train_ratio=request.train_ratio,
        )
        return OptimizationRunCreatedResponse(run_id=run_id, status="running")

    from fleet_rlm.integrations.local_store import (
        create_optimization_run as _db_create_run,
    )

    db_row = await asyncio.to_thread(
        _db_create_run,
        program_spec=effective_program_spec,
        auto=request.auto,
        train_ratio=request.train_ratio,
        module_slug=request.module_slug,
        dataset_id=int(request.dataset_id) if request.dataset_id is not None else None,
        dataset_path=dataset_ref,
    )

    run_id = str(db_row.id or 0)
    background_tasks.add_task(
        run_optimization_background,
        run_id=db_row.id or 0,
        persistence="local",
        module_slug=request.module_slug,
        dataset_path=dataset,
        program_spec=effective_program_spec,
        output_path=output_path,
        default_output_root=OPTIMIZATION_DATA_ROOT,
        auto=request.auto,
        train_ratio=request.train_ratio,
    )

    return OptimizationRunCreatedResponse(run_id=run_id, status="running")


@router.get(
    "/runs",
    response_model=list[OptimizationRunResponse],
    responses=AUTH_ERROR_RESPONSES,
)
async def list_runs(
    state: ServerStateDep,
    identity: HTTPIdentityDep,
    repository: RepositoryDep,
    status: str | None = Query(
        default=None, description="Filter by status: running, completed, failed"
    ),
    limit: int = Query(
        default=50, ge=1, le=200, description="Maximum number of runs to return."
    ),
    offset: int = Query(
        default=0, ge=0, description="Pagination offset into the run list."
    ),
) -> list[OptimizationRunResponse]:
    """List optimization runs, most recent first."""
    persisted_identity = await _resolve_persisted_identity(
        state=state,
        repository=repository,
        identity=identity,
    )
    if repository is not None and persisted_identity is not None:
        status_filter = None
        if status:
            try:
                status_filter = OptimizationRunStatus(status)
            except ValueError as exc:
                raise HTTPException(
                    status_code=400, detail=f"Invalid status filter: {status!r}"
                ) from exc
        runs = await repository.list_optimization_runs(
            tenant_id=persisted_identity.tenant_id,
            workspace_id=persisted_identity.workspace_id,
            created_by_user_id=persisted_identity.user_id,
            status=status_filter,
            limit=limit,
            offset=offset,
        )
        return [_db_run_to_response(r) for r in runs]

    from fleet_rlm.integrations.local_store import (
        RunStatus,
        list_optimization_runs,
    )

    status_filter = None
    if status:
        try:
            status_filter = RunStatus(status)
        except ValueError:
            raise HTTPException(
                status_code=400, detail=f"Invalid status filter: {status!r}"
            )

    runs = await asyncio.to_thread(
        list_optimization_runs, status=status_filter, limit=limit, offset=offset
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
    state: ServerStateDep,
    identity: HTTPIdentityDep,
    repository: RepositoryDep,
    run_ids: str = Query(description="Comma-separated run IDs to compare (max 5)."),
) -> RunComparisonResponse:
    """Compare prompt diffs and scores across optimization runs."""
    persisted_identity = await _resolve_persisted_identity(
        state=state,
        repository=repository,
        identity=identity,
    )

    raw_ids = [s.strip() for s in run_ids.split(",") if s.strip()]
    if not raw_ids:
        raise HTTPException(status_code=400, detail="run_ids is required.")
    if len(raw_ids) > 5:
        raise HTTPException(
            status_code=400, detail="Maximum 5 runs can be compared at once."
        )

    if repository is not None and persisted_identity is not None:
        comparison_items: list[RunComparisonItem] = []
        for raw_id in raw_ids:
            run_uuid = _parse_uuid_id(raw_id, detail=f"Run {raw_id} not found.")
            run_row = await repository.get_optimization_run(
                tenant_id=persisted_identity.tenant_id,
                run_id=run_uuid,
                workspace_id=persisted_identity.workspace_id,
                created_by_user_id=persisted_identity.user_id,
            )
            if run_row is None:
                raise HTTPException(
                    status_code=400, detail=f"Run {raw_id} not found."
                )
            snapshots = await repository.get_prompt_snapshots(
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
                            prompt_type=s.prompt_type.value
                            if hasattr(s.prompt_type, "value")
                            else str(s.prompt_type),
                            prompt_text=s.prompt_text,
                        )
                        for s in snapshots
                    ],
                )
            )
        return RunComparisonResponse(runs=comparison_items)

    try:
        id_list = [int(x) for x in raw_ids]
    except ValueError:
        raise HTTPException(
            status_code=400, detail="run_ids must be comma-separated run identifiers."
        )

    from fleet_rlm.integrations.local_store import (
        get_optimization_run,
        get_prompt_snapshots,
    )

    def _build_comparison_items(
        run_ids: list[int],
    ) -> list[RunComparisonItem]:
        items: list[RunComparisonItem] = []
        for rid in run_ids:
            run_row = get_optimization_run(rid)
            if run_row is None:
                raise HTTPException(
                    status_code=400, detail=f"Run {rid} not found."
                )
            snapshots = get_prompt_snapshots(rid)
            items.append(
                RunComparisonItem(
                    run_id=str(run_row.id or 0),
                    program_spec=run_row.program_spec,
                    validation_score=run_row.validation_score,
                    prompt_snapshots=[
                        PromptSnapshotItem(
                            predictor_name=s.predictor_name,
                            prompt_type=s.prompt_type,
                            prompt_text=s.prompt_text,
                        )
                        for s in snapshots
                    ],
                )
            )
        return items

    comparison_items = await asyncio.to_thread(_build_comparison_items, id_list)
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
    state: ServerStateDep,
    identity: HTTPIdentityDep,
    repository: RepositoryDep,
    run_id: str = ApiPath(description="Identifier of the optimization run to fetch."),
) -> OptimizationRunResponse:
    """Get a single optimization run by ID."""
    persisted_identity = await _resolve_persisted_identity(
        state=state,
        repository=repository,
        identity=identity,
    )
    if repository is not None and persisted_identity is not None:
        row = await repository.get_optimization_run(
            tenant_id=persisted_identity.tenant_id,
            run_id=_parse_uuid_id(
                run_id,
                detail=f"Optimization run {run_id} not found.",
            ),
            workspace_id=persisted_identity.workspace_id,
            created_by_user_id=persisted_identity.user_id,
        )
        if row is None:
            raise HTTPException(
                status_code=404, detail=f"Optimization run {run_id} not found."
            )
        return _db_run_to_response(row)

    from fleet_rlm.integrations.local_store import get_optimization_run

    try:
        legacy_run_id = int(run_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=404, detail=f"Optimization run {run_id} not found."
        ) from exc
    row = await asyncio.to_thread(get_optimization_run, legacy_run_id)
    if row is None:
        raise HTTPException(
            status_code=404, detail=f"Optimization run {run_id} not found."
        )
    return _db_run_to_response(row)


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
    state: ServerStateDep,
    identity: HTTPIdentityDep,
    repository: RepositoryDep,
    run_id: str = ApiPath(
        description="Identifier of the optimization run whose results to list."
    ),
    limit: int = Query(
        default=100,
        ge=1,
        le=500,
        description="Maximum number of evaluation rows to return.",
    ),
    offset: int = Query(
        default=0, ge=0, description="Pagination offset into the evaluation results."
    ),
) -> EvaluationResultsResponse:
    """Return per-example evaluation results for an optimization run."""
    persisted_identity = await _resolve_persisted_identity(
        state=state,
        repository=repository,
        identity=identity,
    )
    if repository is not None and persisted_identity is not None:
        run_uuid = _parse_uuid_id(
            run_id, detail=f"Optimization run {run_id} not found."
        )
        if (
            await repository.get_optimization_run(
                tenant_id=persisted_identity.tenant_id,
                run_id=run_uuid,
                workspace_id=persisted_identity.workspace_id,
                created_by_user_id=persisted_identity.user_id,
            )
            is None
        ):
            raise HTTPException(
                status_code=404, detail=f"Optimization run {run_id} not found."
            )
        items, total = await repository.get_evaluation_results(
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

    from fleet_rlm.integrations.local_store import (
        get_evaluation_results,
        get_optimization_run,
    )

    try:
        legacy_run_id = int(run_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=404, detail=f"Optimization run {run_id} not found."
        ) from exc

    if await asyncio.to_thread(get_optimization_run, legacy_run_id) is None:
        raise HTTPException(
            status_code=404, detail=f"Optimization run {run_id} not found."
        )

    items, total = await asyncio.to_thread(
        get_evaluation_results, legacy_run_id, limit=limit, offset=offset
    )
    return EvaluationResultsResponse(
        items=[
            EvaluationResultItem(
                id=str(r.id or 0),
                example_index=r.example_index,
                input_data=r.input_data,
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
