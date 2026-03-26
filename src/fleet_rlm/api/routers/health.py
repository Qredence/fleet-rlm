"""Health and readiness endpoints."""

from fastapi import APIRouter

from ..dependencies import ServerStateDep
from ..schemas.core import HealthResponse, ReadyResponse

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    responses={503: {"description": "Health status could not be determined."}},
)
async def health() -> HealthResponse:
    """Report a lightweight server health signal and package version."""
    return HealthResponse()


@router.get(
    "/ready",
    response_model=ReadyResponse,
    responses={503: {"description": "Readiness evaluation could not complete."}},
)
async def ready(state: ServerStateDep) -> ReadyResponse:
    """Report whether critical startup dependencies are ready for requests."""
    cfg = state.config
    planner_ready = state.planner_lm is not None

    if state.repository is not None:
        database_status = "ready"
    elif cfg.database_required:
        database_status = "missing"
    else:
        database_status = "disabled"

    overall_ready = planner_ready and (
        database_status == "ready" or not cfg.database_required
    )

    return ReadyResponse(
        ready=overall_ready,
        planner_configured=planner_ready,
        planner="ready" if planner_ready else "missing",
        database=database_status,
        database_required=cfg.database_required,
        sandbox_provider=cfg.sandbox_provider,
    )
