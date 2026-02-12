"""Health and readiness endpoints."""

from fastapi import APIRouter

from ..deps import server_state
from ..schemas import HealthResponse, ReadyResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health():
    return HealthResponse()


@router.get("/ready", response_model=ReadyResponse)
async def ready():
    return ReadyResponse(
        ready=server_state.is_ready,
        planner_configured=server_state.planner_lm is not None,
    )
