"""Health and readiness endpoints."""

from fastapi import APIRouter

from ..dependencies import ServerStateDep
from ..schemas.core import HealthResponse, ReadyResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse()


@router.get("/ready", response_model=ReadyResponse)
async def ready(state: ServerStateDep) -> ReadyResponse:
    cfg = state.config
    planner_ready = state.planner_lm is not None

    if state.repository is not None:
        database_status = "ready"
    elif cfg.database_required:
        database_status = "missing"
    else:
        database_status = "disabled"

    overall_ready = database_status == "ready" or not cfg.database_required

    return ReadyResponse(
        ready=overall_ready,
        planner_configured=planner_ready,
        planner="ready" if planner_ready else "missing",
        database=database_status,
        database_required=cfg.database_required,
        sandbox_provider=cfg.sandbox_provider,
    )
