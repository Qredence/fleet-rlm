"""GEPA prompt optimization endpoints."""

from __future__ import annotations

import logging
import os
from functools import partial
from pathlib import Path
from typing import Any, Literal, TypeAlias, cast

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query

from ..dependencies import HTTPIdentityDep, require_http_identity
from ..runtime_services.common import run_blocking
from ..schemas.core import (
    GEPAModuleInfo,
    GEPAOptimizationRequest,
    GEPAOptimizationResponse,
    GEPAStatusResponse,
    OptimizationRunCreatedResponse,
    OptimizationRunResponse,
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

    base_root = os.path.realpath(os.fspath(OPTIMIZATION_DATA_ROOT))
    safe_root = os.path.join(base_root, "")

    if os.path.isabs(request.dataset_path):
        raise HTTPException(
            status_code=400,
            detail="Absolute paths are not allowed. Use a relative path.",
        )
    dataset = os.path.realpath(os.path.join(safe_root, request.dataset_path))
    if not dataset.startswith(safe_root):
        raise HTTPException(
            status_code=400,
            detail="Path escapes the allowed data directory.",
        )
    if not os.path.exists(dataset):
        raise HTTPException(
            status_code=400,
            detail=f"Dataset file not found: {request.dataset_path}",
        )

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

        db_run_id = _db_create_run(
            program_spec=effective_program_spec,
            auto=request.auto,
            train_ratio=request.train_ratio,
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
                    dataset_path=Path(dataset),
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
                    dataset_path=Path(dataset),
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

                fail_optimization_run(db_run_id, error=str(exc))
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

            complete_optimization_run(
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

    def _on_phase(phase: str) -> None:
        try:
            update_optimization_run_phase(run_id, phase=phase)
        except Exception:
            logger.debug("Failed to update phase for run %s", run_id)

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
            )

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
    base_root = os.path.realpath(os.fspath(OPTIMIZATION_DATA_ROOT))
    safe_root = os.path.join(base_root, "")

    if os.path.isabs(request.dataset_path):
        raise HTTPException(
            status_code=400,
            detail="Absolute paths are not allowed. Use a relative path.",
        )
    dataset = os.path.realpath(os.path.join(safe_root, request.dataset_path))
    if not dataset.startswith(safe_root):
        raise HTTPException(
            status_code=400, detail="Path escapes the allowed data directory."
        )
    if not os.path.exists(dataset):
        raise HTTPException(
            status_code=400, detail=f"Dataset file not found: {request.dataset_path}"
        )

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
                status_code=400, detail="Path escapes the allowed data directory."
            )
        output_path = Path(resolved_output)

    # Create DB record
    from fleet_rlm.integrations.local_store import (
        create_optimization_run as _db_create_run,
    )

    db_row = _db_create_run(
        program_spec=effective_program_spec,
        auto=request.auto,
        train_ratio=request.train_ratio,
        module_slug=request.module_slug,
        dataset_path=request.dataset_path,
    )

    # Spawn background task — db_row.id is always set after a successful insert
    run_id = db_row.id or 0
    background_tasks.add_task(
        _run_optimization_background,
        run_id=run_id,
        module_slug=request.module_slug,
        dataset_path=Path(dataset),
        program_spec=effective_program_spec,
        output_path=output_path,
        default_output_root=OPTIMIZATION_DATA_ROOT,
        auto=request.auto,
        train_ratio=request.train_ratio,
    )

    return OptimizationRunCreatedResponse(run_id=db_row.id or 0, status="running")


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
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
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

    runs = list_optimization_runs(status=status_filter, limit=limit, offset=offset)
    return [_db_run_to_response(r) for r in runs]


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
    run_id: int,
    identity: HTTPIdentityDep,
) -> OptimizationRunResponse:
    """Get a single optimization run by ID."""
    _ = identity
    from fleet_rlm.integrations.local_store import get_optimization_run

    row = get_optimization_run(run_id)
    if row is None:
        raise HTTPException(
            status_code=404, detail=f"Optimization run {run_id} not found."
        )
    return _db_run_to_response(row)
