"""Liveness and readiness probes for the Fleet RLM backend."""

from __future__ import annotations

from fastapi import APIRouter

from fleet_rlm import __version__
from fleet_rlm.api.dependencies import RuntimeInventoryIfReadyDep, SettingsDep
from fleet_rlm.api.errors import http_error
from fleet_rlm.api.schemas import HealthLivenessResponse, HealthReadinessResponse

router = APIRouter(prefix="/health", tags=["health"])


@router.get(
    "",
    response_model=HealthLivenessResponse,
    operation_id="get_health",
)
def get_health(settings: SettingsDep) -> HealthLivenessResponse:
    """Liveness: the process is serving HTTP regardless of composition state."""
    return HealthLivenessResponse(status="ok", app=settings.app_name, version=__version__)


@router.get(
    "/ready",
    response_model=HealthReadinessResponse,
    operation_id="get_readiness",
    responses={503: {"description": "Service is not ready"}},
)
async def get_readiness(inventory: RuntimeInventoryIfReadyDep) -> HealthReadinessResponse:
    """Readiness: composition installed and the configured database answers."""
    if inventory is None:
        raise http_error(503, "service_not_ready", "Service is not ready")
    database = await inventory.database.readiness()
    if database == "unreachable":
        raise http_error(503, "service_not_ready", "Service is not ready")
    if database == "ok":
        return HealthReadinessResponse(status="ready", database="ok")
    return HealthReadinessResponse(status="ready", database="not_configured")
