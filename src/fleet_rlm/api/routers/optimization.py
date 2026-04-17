"""GEPA prompt optimization endpoints."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from functools import partial
from pathlib import Path, PurePosixPath
from typing import Any, Literal, TypeAlias, cast

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    Path as ApiPath,
    Query,
    UploadFile,
)
from fastapi.params import Form

from ..dependencies import HTTPIdentityDep, require_http_identity
from ..runtime_services.common import run_blocking
from ..schemas.core import (
    DatasetDetailResponse,
    DatasetListResponse,
    DatasetResponse,
    EvaluationResultItem,
    EvaluationResultsResponse,
    GEPAModuleInfo,
    GEPAOptimizationRequest,
    GEPAOptimizationResponse,
    GEPAStatusResponse,
    OptimizationRunCreatedResponse,
    OptimizationRunResponse,
    PromptSnapshotItem,
    RunComparisonItem,
    RunComparisonResponse,
    TranscriptDatasetRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/optimization",
    tags=["optimization"],
    dependencies=[Depends(require_http_identity)],
)

OpenAPIResponses: TypeAlias = dict[int | str, dict[str, Any]]


AUTH_ERROR_RESPONSES: OpenAPIResponses = {
    401: {
        "description": "Authentication is required or the provided token is invalid."
    },
}

OPTIMIZATION_TIMEOUT_SECONDS = 900

OPTIMIZATION_DATA_ROOT = Path(
    os.environ.get("FLEET_RLM_OPTIMIZATION_DATA_ROOT", os.getcwd())
).resolve()


def _resolve_relative_dataset_lookup(relative_path: str) -> str:
    """Normalize a user-provided dataset lookup into a safe relative key."""
    normalized = PurePosixPath(relative_path.replace("\\", "/"))
    if normalized.is_absolute():
        raise HTTPException(
            status_code=400,
            detail="Absolute paths are not allowed. Use a relative path.",
        )

    parts = normalized.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise HTTPException(
            status_code=400, detail="Path escapes the allowed data directory."
        )
    return normalized.as_posix()


def _find_dataset_under_root(root: Path, relative_path: str) -> Path | None:
    """Return a dataset file under ``root`` that matches the relative lookup key."""
    root_resolved = root.resolve()
    for candidate in root.rglob("*"):
        if not candidate.is_file():
            continue
        try:
            candidate_relative = candidate.relative_to(root).as_posix()
            resolved_candidate = candidate.resolve(strict=True)
            resolved_candidate.relative_to(root_resolved)
        except (OSError, ValueError):
            continue
        if candidate_relative == relative_path:
            return resolved_candidate
    return None


def _check_gepa_available() -> bool:
    """Return True if the GEPA teleprompt module is importable."""
    try:
        from dspy.teleprompt import GEPA  # noqa: F401

        return True
    except Exception:
        return False


def _get_mlflow_status() -> tuple[bool, bool]:
    """Return whether MLflow is configured and whether it is reachable."""
    try:
        from fleet_rlm.integrations.observability.config import MlflowConfig
        from fleet_rlm.integrations.observability.mlflow_runtime import (
            initialize_mlflow,
        )

        config = MlflowConfig.from_env()
        if not config.enabled:
            return False, False
        return True, initialize_mlflow(config)
    except Exception:
        return False, False


async def _resolve_dataset_request(
    request: GEPAOptimizationRequest,
) -> tuple[Path, str]:
    """Resolve a dataset request into an executable path and stored reference."""
    from fleet_rlm.integrations.local_store import get_dataset

    if request.dataset_id is not None:
        dataset_row = await asyncio.to_thread(get_dataset, request.dataset_id)
        if dataset_row is None:
            raise HTTPException(
                status_code=400,
                detail=f"Dataset {request.dataset_id} not found.",
            )
        dataset = Path(dataset_row.uri).resolve()
        if not dataset.exists():
            raise HTTPException(
                status_code=400,
                detail=f"Dataset file not found for dataset {request.dataset_id}.",
            )
        return dataset, str(dataset)

    dataset_path = (request.dataset_path or "").strip()
    dataset_lookup = _resolve_relative_dataset_lookup(dataset_path)
    dataset = _find_dataset_under_root(OPTIMIZATION_DATA_ROOT, dataset_lookup)
    if dataset is None:
        raise HTTPException(
            status_code=400, detail=f"Dataset file not found: {dataset_path}"
        )
    return dataset, dataset_lookup


@router.get(
    "/status",
    response_model=GEPAStatusResponse,
    responses=AUTH_ERROR_RESPONSES,
)
async def get_optimization_status(
    identity: HTTPIdentityDep,
) -> GEPAStatusResponse:
    """Return GEPA optimization availability and prerequisites."""
    _ = identity
    gepa_installed = _check_gepa_available()
    mlflow_configured, mlflow_enabled = _get_mlflow_status()
    available = gepa_installed and mlflow_enabled

    guidance: list[str] = []
    if not gepa_installed:
        guidance.append(
            "GEPA teleprompt module is not installed. "
            "Install dspy with GEPA support to enable optimization."
        )
    if not mlflow_enabled:
        if not mlflow_configured:
            guidance.append(
                "MLflow is not enabled. Set MLFLOW_ENABLED=true and configure "
                "MLFLOW_TRACKING_URI to enable optimization tracking."
            )
        else:
            guidance.append(
                "MLflow is configured but unavailable. Verify the tracking URI, "
                "server health, and any required MLflow auth credentials."
            )

    return GEPAStatusResponse(
        available=available,
        mlflow_enabled=mlflow_enabled,
        gepa_installed=gepa_installed,
        guidance=guidance,
    )


@router.get(
    "/modules",
    response_model=list[GEPAModuleInfo],
    responses=AUTH_ERROR_RESPONSES,
)
async def list_optimization_modules(
    identity: HTTPIdentityDep,
) -> list[GEPAModuleInfo]:
    """Return the list of registered optimizable DSPy modules."""
    _ = identity
    from fleet_rlm.runtime.quality.module_registry import list_module_metadata

    return [
        GEPAModuleInfo(
            slug=m["slug"],
            label=m["label"],
            program_spec=m["program_spec"],
            required_dataset_keys=m["required_dataset_keys"],
        )
        for m in list_module_metadata()
    ]


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
    identity: HTTPIdentityDep,
) -> GEPAOptimizationResponse:
    """Trigger a GEPA prompt optimization run."""
    _ = identity
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

    dataset, dataset_ref = await _resolve_dataset_request(request)
    base_root = os.path.realpath(os.fspath(OPTIMIZATION_DATA_ROOT))
    safe_root = os.path.join(base_root, "")

    output_path: Path | None = None
    if request.output_path:
        if os.path.isabs(request.output_path):
            raise HTTPException(
                status_code=400,
                detail="Absolute paths are not allowed. Use a relative path.",
            )
        resolved_output = os.path.realpath(os.path.join(safe_root, request.output_path))
        if not resolved_output.startswith(safe_root):
            raise HTTPException(
                status_code=400,
                detail="Path escapes the allowed data directory.",
            )
        output_path = Path(resolved_output)

    db_run_id = None
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
                dataset_id=request.dataset_id,
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
                    run_id=db_run_id,
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
            try:
                from fleet_rlm.integrations.local_store import (
                    fail_optimization_run,
                )

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
        try:
            from fleet_rlm.integrations.local_store import (
                complete_optimization_run,
            )

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


def _db_run_to_response(row: Any) -> OptimizationRunResponse:
    """Convert an OptimizationRun SQLModel row to an API response."""
    return OptimizationRunResponse(
        id=row.id,
        status=row.status.value if hasattr(row.status, "value") else str(row.status),
        module_slug=getattr(row, "module_slug", None),
        program_spec=row.program_spec,
        optimizer=row.optimizer.value
        if hasattr(row.optimizer, "value")
        else str(row.optimizer),
        auto=row.auto,
        train_ratio=row.train_ratio,
        dataset_path=getattr(row, "dataset_path", None),
        train_examples=row.train_examples,
        validation_examples=row.validation_examples,
        validation_score=row.validation_score,
        output_path=row.output_path,
        manifest_path=getattr(row, "manifest_path", None),
        error=row.error,
        phase=getattr(row, "phase", None),
        started_at=row.started_at.isoformat() if row.started_at else "",
        completed_at=row.completed_at.isoformat() if row.completed_at else None,
    )


def _run_optimization_background(
    *,
    run_id: int,
    module_slug: str | None,
    dataset_path: Path,
    program_spec: str,
    output_path: Path | None,
    default_output_root: Path | None,
    auto: Literal["light", "medium", "heavy"],
    train_ratio: float,
) -> None:
    """Execute GEPA optimization synchronously (runs in background thread)."""
    from fleet_rlm.integrations.local_store import (
        complete_optimization_run,
        fail_optimization_run,
        update_optimization_run_phase,
    )
    from fleet_rlm.runtime.quality.gepa_optimization import (
        log_gepa_mlflow_run_metadata,
    )

    def _on_phase(phase: str) -> None:
        try:
            update_optimization_run_phase(run_id, phase=phase)
        except Exception:
            logger.debug("Failed to update phase for run %s", run_id)

    # -- MLflow autologging (best-effort, never blocks the run) -----------
    # The module-registry GEPA path needs an explicit parent MLflow run here
    # because ``run_module_optimization()`` is intentionally MLflow-agnostic.
    # The custom-program path delegates to ``optimize_program_with_gepa()``,
    # which already initializes MLflow and starts its own GEPA run.
    mlflow_ctx: Any = None
    _mlflow_log_metric: Any = None
    _mlflow_log_params: Any = None
    _mlflow_set_tags: Any = None
    if module_slug:
        try:
            import mlflow
        except ImportError:
            logger.debug("MLflow package unavailable for run %s", run_id, exc_info=True)
        else:
            from fleet_rlm.integrations.observability.config import MlflowConfig
            from fleet_rlm.integrations.observability.mlflow_runtime import (
                initialize_mlflow,
            )

            try:
                resolved_cfg = MlflowConfig.from_env().model_copy(
                    update={
                        "dspy_log_compiles": True,
                        "dspy_log_evals": True,
                        "dspy_log_traces_from_compile": True,
                        "dspy_log_traces_from_eval": True,
                    }
                )
                if initialize_mlflow(resolved_cfg):
                    start_run = getattr(mlflow, "start_run", None)
                    _mlflow_log_metric = getattr(mlflow, "log_metric", None)
                    _mlflow_log_params = getattr(mlflow, "log_params", None)
                    _mlflow_set_tags = getattr(mlflow, "set_tags", None)
                    run_label = f"GEPA::{module_slug}"
                    if start_run is not None:
                        mlflow_ctx = cast(Any, start_run)(run_name=run_label)
                        mlflow_ctx.__enter__()
                        log_gepa_mlflow_run_metadata(
                            dataset_path=dataset_path,
                            program_spec=program_spec,
                            auto=auto,
                            train_ratio=train_ratio,
                            module_slug=module_slug,
                            source="api_background",
                            log_params=cast(Any, _mlflow_log_params),
                            set_tags=cast(Any, _mlflow_set_tags),
                        )
                else:
                    logger.debug(
                        "MLflow unavailable for run %s — proceeding without tracking",
                        run_id,
                    )
            except Exception:
                logger.debug("MLflow setup skipped for run %s", run_id, exc_info=True)

    try:
        _on_phase("loading")

        if module_slug:
            from fleet_rlm.runtime.quality.module_registry import get_module_spec
            from fleet_rlm.runtime.quality.optimization_runner import (
                run_module_optimization,
            )

            spec = get_module_spec(module_slug)
            if spec is None:
                raise ValueError(f"Unknown module slug: {module_slug!r}")
            _on_phase("compiling")
            result = dict(
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
        else:
            from fleet_rlm.runtime.quality.gepa_optimization import (
                optimize_program_with_gepa,
            )

            _on_phase("compiling")
            result = optimize_program_with_gepa(
                dataset_path=dataset_path,
                program_spec=program_spec,
                output_path=output_path,
                auto=auto,
                train_ratio=train_ratio,
                source="api_background",
            )

        # Log validation score to MLflow when available
        try:
            if _mlflow_log_metric is not None:
                cast(Any, _mlflow_log_metric)(
                    "gepa_train_examples", result.get("train_examples", 0)
                )
                cast(Any, _mlflow_log_metric)(
                    "gepa_validation_examples", result.get("validation_examples", 0)
                )
                val_score = result.get("validation_score")
                if val_score is not None:
                    cast(Any, _mlflow_log_metric)("gepa_validation_score", val_score)
        except Exception:
            logger.debug("Failed to log GEPA metrics to MLflow for run %s", run_id)

        _on_phase("saving")
        complete_optimization_run(
            run_id,
            train_examples=result.get("train_examples", 0),
            validation_examples=result.get("validation_examples", 0),
            validation_score=result.get("validation_score"),
            output_path=result.get("output_path"),
            manifest_path=result.get("manifest_path"),
        )
    except Exception as exc:
        logger.exception("Background GEPA optimization failed for run %s", run_id)
        try:
            fail_optimization_run(run_id, error=str(exc))
        except Exception:
            logger.exception("Failed to mark run %s as failed", run_id)
    finally:
        # Clean up the MLflow run context if one was opened
        if mlflow_ctx is not None:
            try:
                mlflow_ctx.__exit__(None, None, None)
            except Exception:
                logger.debug("Failed to close MLflow run for run %s", run_id)


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
    identity: HTTPIdentityDep,
) -> OptimizationRunCreatedResponse:
    """Create a non-blocking GEPA optimization run.

    Returns immediately with the run_id.  The optimization executes as a
    background task.  Poll ``GET /runs/{run_id}`` for progress and results.
    """
    _ = identity
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
                status_code=400, detail=f"Unknown module slug: {request.module_slug!r}"
            )
        effective_program_spec = spec.program_spec
    elif not request.program_spec:
        raise HTTPException(
            status_code=400,
            detail="Either module_slug or program_spec must be provided.",
        )

    # Path validation
    dataset, dataset_ref = await _resolve_dataset_request(request)
    base_root = os.path.realpath(os.fspath(OPTIMIZATION_DATA_ROOT))
    safe_root = os.path.join(base_root, "")

    output_path: Path | None = None
    if request.output_path:
        if os.path.isabs(request.output_path):
            raise HTTPException(
                status_code=400,
                detail="Absolute paths are not allowed. Use a relative path.",
            )
        resolved_output = os.path.realpath(os.path.join(safe_root, request.output_path))
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
    from fleet_rlm.integrations.local_store import (
        create_optimization_run as _db_create_run,
    )

    db_row = await asyncio.to_thread(
        _db_create_run,
        program_spec=effective_program_spec,
        auto=request.auto,
        train_ratio=request.train_ratio,
        module_slug=request.module_slug,
        dataset_id=request.dataset_id,
        dataset_path=dataset_ref,
    )

    # Spawn background task — db_row.id is always set after a successful insert
    run_id = db_row.id or 0
    background_tasks.add_task(
        _run_optimization_background,
        run_id=run_id,
        module_slug=request.module_slug,
        dataset_path=dataset,
        program_spec=effective_program_spec,
        output_path=output_path,
        default_output_root=OPTIMIZATION_DATA_ROOT,
        auto=request.auto,
        train_ratio=request.train_ratio,
    )

    return OptimizationRunCreatedResponse(run_id=db_row.id or 0, status="running")


@router.post(
    "/transcript-datasets",
    response_model=DatasetResponse,
    responses=cast(
        OpenAPIResponses,
        {
            **AUTH_ERROR_RESPONSES,
            400: {"description": "Invalid transcript dataset payload."},
        },
    ),
)
@router.post(
    "/datasets/from-transcript",
    response_model=DatasetResponse,
    include_in_schema=False,
)
async def create_dataset_from_transcript(
    request: TranscriptDatasetRequest,
    identity: HTTPIdentityDep,
) -> DatasetResponse:
    """Convert transcript turns into a GEPA dataset."""
    _ = identity
    from fleet_rlm.integrations.local_store import create_transcript_dataset

    try:
        dataset = await asyncio.to_thread(
            create_transcript_dataset,
            module_slug=request.module_slug,
            turns=[
                (turn.user_message, turn.assistant_message) for turn in request.turns
            ],
            title=request.title,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return DatasetResponse(
        id=dataset.id or 0,
        name=dataset.name,
        row_count=dataset.row_count or 0,
        format=dataset.format or "jsonl",
        module_slug=dataset.module_slug,
        created_at=dataset.created_at.isoformat(),
    )


@router.get(
    "/runs",
    response_model=list[OptimizationRunResponse],
    responses=AUTH_ERROR_RESPONSES,
)
async def list_runs(
    identity: HTTPIdentityDep,
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
    _ = identity
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
    identity: HTTPIdentityDep,
    run_ids: str = Query(description="Comma-separated run IDs to compare (max 5)."),
) -> RunComparisonResponse:
    """Compare prompt diffs and scores across optimization runs."""
    _ = identity

    raw_ids = [s.strip() for s in run_ids.split(",") if s.strip()]
    if not raw_ids:
        raise HTTPException(status_code=400, detail="run_ids is required.")
    if len(raw_ids) > 5:
        raise HTTPException(
            status_code=400, detail="Maximum 5 runs can be compared at once."
        )

    try:
        id_list = [int(x) for x in raw_ids]
    except ValueError:
        raise HTTPException(
            status_code=400, detail="run_ids must be comma-separated integers."
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
                raise HTTPException(status_code=400, detail=f"Run {rid} not found.")
            snapshots = get_prompt_snapshots(rid)
            items.append(
                RunComparisonItem(
                    run_id=run_row.id or 0,
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
    identity: HTTPIdentityDep,
    run_id: int = ApiPath(description="Identifier of the optimization run to fetch."),
) -> OptimizationRunResponse:
    """Get a single optimization run by ID."""
    _ = identity
    from fleet_rlm.integrations.local_store import get_optimization_run

    row = await asyncio.to_thread(get_optimization_run, run_id)
    if row is None:
        raise HTTPException(
            status_code=404, detail=f"Optimization run {run_id} not found."
        )
    return _db_run_to_response(row)


# ── Dataset endpoints ────────────────────────────────────────────────

_MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB

_SAFE_NAME_RE = re.compile(r"[^a-zA-Z0-9_\-]")


def _sanitize_filename(name: str) -> str:
    """Return a filesystem-safe version of *name*."""
    stem = Path(name).stem
    return _SAFE_NAME_RE.sub("_", stem)[:120]


def _parse_rows(content: bytes, fmt: str) -> list[dict]:
    """Parse uploaded file content into a list of dicts."""
    text = content.decode("utf-8")
    if fmt == "jsonl":
        rows: list[dict] = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            rows.append(json.loads(stripped))
        return rows
    else:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return parsed
        raise ValueError("JSON file must contain a top-level array of objects")


@router.post(
    "/datasets",
    response_model=DatasetResponse,
    responses=cast(
        OpenAPIResponses,
        {
            **AUTH_ERROR_RESPONSES,
            400: {"description": "Invalid dataset file."},
        },
    ),
)
async def upload_dataset(
    identity: HTTPIdentityDep,
    file: UploadFile = File(
        description="Dataset file to upload in JSON or JSONL format."
    ),
    module_slug: str | None = Form(  # type: ignore
        default=None,
        description="Optional module slug used to validate required dataset keys.",
    ),
) -> DatasetResponse:
    """Upload and register a dataset file (.json or .jsonl)."""
    _ = identity

    if not file.filename:
        raise HTTPException(status_code=400, detail="Filename is required.")

    ext = Path(file.filename).suffix.lower()
    if ext not in (".json", ".jsonl"):
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported format '{ext}'. Allowed: .json, .jsonl",
        )

    content = await file.read()
    if len(content) > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"File exceeds the {_MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit.",
        )

    fmt = ext.lstrip(".")  # "json" or "jsonl"
    try:
        rows = _parse_rows(content, fmt)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to parse dataset: {exc}")

    if not rows:
        raise HTTPException(status_code=400, detail="Dataset is empty.")

    # Validate first row keys against module requirements if module_slug given
    if module_slug:
        from fleet_rlm.runtime.quality.module_registry import get_module_spec

        spec = get_module_spec(module_slug)
        if spec is None:
            raise HTTPException(
                status_code=400, detail=f"Unknown module slug: {module_slug!r}"
            )
        first_keys = set(rows[0].keys()) if isinstance(rows[0], dict) else set()
        missing = set(spec.required_dataset_keys) - first_keys
        if missing:
            raise HTTPException(
                status_code=400,
                detail=f"Dataset is missing required keys for module "
                f"'{module_slug}': {sorted(missing)}",
            )

    # Save file to dataset root
    from fleet_rlm.integrations.local_store import create_dataset, get_dataset_root

    ds_root = get_dataset_root()
    # Generate a temp ID from timestamp for the filename
    import time

    ts_id = int(time.time() * 1000) % 10_000_000
    safe_name = _sanitize_filename(file.filename)
    dest = ds_root / f"{ts_id}_{safe_name}.{fmt}"
    await asyncio.to_thread(dest.write_bytes, content)

    ds = await asyncio.to_thread(
        create_dataset,
        name=Path(file.filename).stem,
        row_count=len(rows),
        format=fmt,
        uri=str(dest),
        module_slug=module_slug,
    )

    return DatasetResponse(
        id=ds.id or 0,
        name=ds.name,
        row_count=ds.row_count or 0,
        format=ds.format or fmt,
        module_slug=ds.module_slug,
        created_at=ds.created_at.isoformat(),
    )


@router.get(
    "/datasets",
    response_model=DatasetListResponse,
    responses=AUTH_ERROR_RESPONSES,
)
async def list_datasets_endpoint(
    identity: HTTPIdentityDep,
    module_slug: str | None = Query(default=None, description="Filter by module slug"),
    limit: int = Query(
        default=50, ge=1, le=200, description="Maximum number of datasets to return."
    ),
    offset: int = Query(
        default=0, ge=0, description="Pagination offset into the dataset list."
    ),
) -> DatasetListResponse:
    """List registered datasets with optional module filter."""
    _ = identity
    from fleet_rlm.integrations.local_store import list_datasets

    items, total = await asyncio.to_thread(
        list_datasets, module_slug=module_slug, limit=limit, offset=offset
    )
    return DatasetListResponse(
        items=[
            DatasetResponse(
                id=d.id or 0,
                name=d.name,
                row_count=d.row_count or 0,
                format=d.format or "",
                module_slug=d.module_slug,
                created_at=d.created_at.isoformat(),
            )
            for d in items
        ],
        total=total,
        offset=offset,
        limit=limit,
        has_more=(offset + limit) < total,
    )


@router.get(
    "/datasets/{dataset_id}",
    response_model=DatasetDetailResponse,
    responses=cast(
        OpenAPIResponses,
        {
            **AUTH_ERROR_RESPONSES,
            404: {"description": "Dataset not found."},
        },
    ),
)
async def get_dataset_detail(
    identity: HTTPIdentityDep,
    dataset_id: int = ApiPath(description="Identifier of the dataset to inspect."),
) -> DatasetDetailResponse:
    """Return dataset metadata with the first 10 rows as preview."""
    _ = identity
    from fleet_rlm.integrations.local_store import get_dataset

    ds = await asyncio.to_thread(get_dataset, dataset_id)
    if ds is None:
        raise HTTPException(status_code=404, detail=f"Dataset {dataset_id} not found.")

    # Read sample rows from the file
    sample_rows: list[dict] = []
    uri_path = Path(ds.uri)
    if await asyncio.to_thread(uri_path.is_file):
        try:
            fmt = ds.format or ("jsonl" if uri_path.suffix == ".jsonl" else "json")
            raw = await asyncio.to_thread(uri_path.read_bytes)
            all_rows = _parse_rows(raw, fmt)
            sample_rows = all_rows[:10]
        except Exception:
            logger.debug("Failed to read sample rows from %s", ds.uri)

    return DatasetDetailResponse(
        id=ds.id or 0,
        name=ds.name,
        row_count=ds.row_count or 0,
        format=ds.format or "",
        module_slug=ds.module_slug,
        created_at=ds.created_at.isoformat(),
        sample_rows=sample_rows,
        uri=ds.uri,
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
    identity: HTTPIdentityDep,
    run_id: int = ApiPath(
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
    _ = identity
    from fleet_rlm.integrations.local_store import (
        get_evaluation_results,
        get_optimization_run,
    )

    if await asyncio.to_thread(get_optimization_run, run_id) is None:
        raise HTTPException(
            status_code=404, detail=f"Optimization run {run_id} not found."
        )

    items, total = await asyncio.to_thread(
        get_evaluation_results, run_id, limit=limit, offset=offset
    )
    return EvaluationResultsResponse(
        items=[
            EvaluationResultItem(
                id=r.id or 0,
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
