"""Health and readiness endpoints."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter

from ..dependencies import ConfigDepsDep, LmDepsDep, PersistenceDepsDep
from ..schemas.base import HealthResponse, ReadyResponse

router = APIRouter(tags=["health"])
logger = logging.getLogger(__name__)

_READY_DB_PING_TIMEOUT_SECONDS = 2.0


@router.get(
    "/health",
    response_model=HealthResponse,
    responses={503: {"description": "Health status could not be determined."}},
)
def health() -> HealthResponse:
    """Report a lightweight server health signal and package version."""
    return HealthResponse()


@router.get(
    "/ready",
    response_model=ReadyResponse,
    responses={503: {"description": "Readiness evaluation could not complete."}},
)
async def ready(
    config_deps: ConfigDepsDep,
    lm_deps: LmDepsDep,
    persistence_deps: PersistenceDepsDep,
) -> ReadyResponse:
    """Report whether critical startup dependencies are ready for requests.

    Verifies DB connectivity with a short-timeout ping so a sleeping Neon
    compute reports ``degraded`` instead of ``ready``.
    """
    cfg = config_deps.config
    planner_ready = lm_deps.planner_lm is not None

    if persistence_deps.db_manager is not None:
        try:
            await asyncio.wait_for(
                persistence_deps.db_manager.ping(),
                timeout=_READY_DB_PING_TIMEOUT_SECONDS,
            )
            database_status = "ready"
        except TimeoutError:
            database_status = "degraded"
        except Exception as exc:
            logger.warning("ready_db_ping_failed", exc_info=exc)
            database_status = "degraded"
    elif cfg.database_required:
        database_status = "missing"
    else:
        database_status = "disabled"

    overall_ready = planner_ready and (database_status == "ready" or not cfg.database_required)

    return ReadyResponse(
        ready=overall_ready,
        planner_configured=planner_ready,
        planner="ready" if planner_ready else "missing",
        database=database_status,
        database_required=cfg.database_required,
        sandbox_provider=cfg.sandbox_provider,
    )
