"""Status and module listing endpoints for GEPA optimization."""

from __future__ import annotations

from fastapi import APIRouter

from ...dependencies import HTTPIdentityDep, require_http_identity
from ...schemas.core import GEPAModuleInfo, GEPAStatusResponse
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
