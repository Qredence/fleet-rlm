"""GEPA prompt optimization endpoints."""

from __future__ import annotations

import logging
import os
from functools import partial
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from ..dependencies import HTTPIdentityDep, require_http_identity
from ..runtime_services.common import run_blocking
from ..schemas.core import (
    GEPAOptimizationRequest,
    GEPAOptimizationResponse,
    GEPAStatusResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/optimization",
    tags=["optimization"],
    dependencies=[Depends(require_http_identity)],
)

AUTH_ERROR_RESPONSES = {
    401: {
        "description": "Authentication is required or the provided token is invalid."
    },
}

OPTIMIZATION_TIMEOUT_SECONDS = 300

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


def _run_gepa_optimization(
    *,
    dataset_path: Path,
    program_spec: str,
    output_path: Path | None,
    auto: str,
    train_ratio: float,
) -> dict:
    """Blocking wrapper around optimize_program_with_gepa."""
    from fleet_rlm.integrations.observability.gepa_optimization import (
        optimize_program_with_gepa,
    )

    return optimize_program_with_gepa(
        dataset_path=dataset_path,
        program_spec=program_spec,
        output_path=output_path,
        auto=auto,  # type: ignore[arg-type]
        train_ratio=train_ratio,
    )


@router.post(
    "/run",
    response_model=GEPAOptimizationResponse,
    responses={
        **AUTH_ERROR_RESPONSES,
        400: {"description": "Invalid optimization parameters."},
        503: {"description": "GEPA optimization is unavailable in this environment."},
    },
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
        from fleet_rlm.integrations.database.local_store import (
            create_optimization_run as _db_create_run,
        )

        db_run_id = _db_create_run(
            program_spec=request.program_spec,
            auto=request.auto,
            train_ratio=request.train_ratio,
        ).id
    except Exception as exc:
        logger.exception("Failed to create optimization run in local database", exc_info=exc)

    try:
        result = await run_blocking(
            partial(
                _run_gepa_optimization,
                dataset_path=Path(dataset),
                program_spec=request.program_spec,
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
                from fleet_rlm.integrations.database.local_store import (
                    fail_optimization_run,
                )

                fail_optimization_run(db_run_id, error=str(exc))
            except Exception as db_exc:
                logger.exception(
                    "Failed to mark GEPA optimization run %s as failed in database",
                    db_run_id,
                )
        return GEPAOptimizationResponse(
            ok=False,
            program_spec=request.program_spec,
            train_examples=0,
            validation_examples=0,
            error=str(exc),
        )

    if db_run_id is not None:
        try:
            from fleet_rlm.integrations.database.local_store import (
                complete_optimization_run,
            )

            complete_optimization_run(
                db_run_id,
                train_examples=result.get("train_examples", 0),
                validation_examples=result.get("validation_examples", 0),
                validation_score=result.get("validation_score"),
                output_path=result.get("output_path"),
            )
        except Exception:
            pass

    return GEPAOptimizationResponse(
        ok=True,
        optimizer=result.get("optimizer", "GEPA"),
        program_spec=result.get("program_spec", request.program_spec),
        train_examples=result.get("train_examples", 0),
        validation_examples=result.get("validation_examples", 0),
        validation_score=result.get("validation_score"),
        output_path=result.get("output_path"),
    )
