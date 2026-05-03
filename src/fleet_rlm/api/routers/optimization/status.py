"""Status and module listing endpoints for GEPA optimization."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter

from ...dependencies import HTTPIdentityDep
from ...schemas.optimization import GEPAModuleInfo, GEPAStatusResponse
from ._deps import AUTH_ERROR_RESPONSES, _check_gepa_available, _get_mlflow_status

router = APIRouter()


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
    gepa_installed = await asyncio.to_thread(_check_gepa_available)
    mlflow_configured, mlflow_enabled = await asyncio.to_thread(_get_mlflow_status)
    module_optimization_available = gepa_installed
    mlflow_dataset_optimization_available = gepa_installed and mlflow_enabled
    mlflow_logging_available = mlflow_enabled
    available = mlflow_dataset_optimization_available

    guidance: list[str] = []
    if not gepa_installed:
        guidance.append(
            "GEPA teleprompt module is not installed. "
            "Install dspy with GEPA support to enable optimization."
        )
    if not mlflow_enabled:
        if not mlflow_configured:
            guidance.append(
                "MLflow is not enabled. Registered module optimization can run "
                "without MLflow, but tracking and MLflow dataset optimization "
                "require MLFLOW_ENABLED=true and MLFLOW_TRACKING_URI."
            )
        else:
            guidance.append(
                "MLflow is configured but unavailable. Verify the tracking URI, "
                "server health, and any required MLflow auth credentials. "
                "Registered module optimization can continue without tracking."
            )

    return GEPAStatusResponse(
        available=available,
        module_optimization_available=module_optimization_available,
        mlflow_dataset_optimization_available=mlflow_dataset_optimization_available,
        mlflow_logging_available=mlflow_logging_available,
        mlflow_configured=mlflow_configured,
        mlflow_enabled=mlflow_enabled,
        gepa_installed=gepa_installed,
        guidance=guidance,
    )


@router.get(
    "/modules",
    response_model=list[GEPAModuleInfo],
    responses=AUTH_ERROR_RESPONSES,
)
def list_optimization_modules(
    identity: HTTPIdentityDep,
) -> list[GEPAModuleInfo]:
    """Return the list of registered optimizable DSPy modules."""
    _ = identity
    from fleet_rlm.runtime.quality.module_registry import list_module_metadata

    return [
        GEPAModuleInfo(
            slug=m["slug"],
            label=m["label"],
            description=m.get("description", ""),
            program_spec=m["program_spec"],
            required_dataset_keys=m["required_dataset_keys"],
        )
        for m in list_module_metadata()
    ]
