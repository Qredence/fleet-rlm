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


def _validate_path(user_path: str, base: Path) -> Path:
    """Resolve *user_path* relative to *base* and reject traversals."""
    candidate = Path(user_path)
    if candidate.is_absolute():
        raise HTTPException(
            status_code=400,
            detail="Absolute paths are not allowed. Use a relative path.",
        )
    resolved = (base / candidate).resolve()
    if not resolved.is_relative_to(base):
        raise HTTPException(
            status_code=400,
            detail="Path escapes the allowed data directory.",
        )
    return resolved


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
    dataset_path: str,
    program_spec: str,
    output_path: str | None,
    auto: str,
    train_ratio: float,
) -> dict:
    """Blocking wrapper around optimize_program_with_gepa."""
    from fleet_rlm.integrations.observability.gepa_optimization import (
        optimize_program_with_gepa,
    )

    return optimize_program_with_gepa(
        dataset_path=Path(dataset_path),
        program_spec=program_spec,
        output_path=Path(output_path) if output_path else None,
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

    dataset = _validate_path(request.dataset_path, OPTIMIZATION_DATA_ROOT)
    if not dataset.exists():
        raise HTTPException(
            status_code=400,
            detail=f"Dataset file not found: {request.dataset_path}",
        )

    output_str: str | None = None
    if request.output_path:
        output = _validate_path(request.output_path, OPTIMIZATION_DATA_ROOT)
        output_str = str(output)

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
    except Exception:
        pass

    try:
        result = await run_blocking(
            partial(
                _run_gepa_optimization,
                dataset_path=str(dataset),
                program_spec=request.program_spec,
                output_path=output_str,
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
            except Exception:
                pass
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
