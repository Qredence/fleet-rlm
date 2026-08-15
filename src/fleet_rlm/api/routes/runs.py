"""Durable idempotent Run cancellation resource."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel

from fleet_rlm.api.dependencies import LocalScopeDep, RunLifecycleDep
from fleet_rlm.api.errors import http_error
from fleet_rlm.chat.run_lifecycle import RunNotFoundError
from fleet_rlm.posthog_client import get_client, get_distinct_id
from fleet_rlm.sessions.models import TurnAccess

router = APIRouter(prefix="/api/runs", tags=["runs"])


class CancellationResponse(BaseModel):
    run_id: UUID
    state: Literal["requested", "already_requested", "already_terminal"]


@router.put(
    "/{run_id}/cancellation",
    response_model=CancellationResponse,
    operation_id="request_run_cancellation",
)
async def request_run_cancellation(
    run_id: UUID,
    identity: LocalScopeDep,
    lifecycle: RunLifecycleDep,
) -> CancellationResponse:
    """
    Request cancellation for a run.
    
    Parameters:
    	run_id (UUID): The identifier of the run to cancel.
    	identity (LocalScopeDep): The authenticated user's workspace scope.
    	lifecycle (RunLifecycleDep): The run lifecycle service.
    
    Returns:
    	CancellationResponse: The run identifier and resulting cancellation state.
    """
    try:
        status = await lifecycle.request_cancel(TurnAccess(identity.user_id, identity.workspace_id), run_id)
    except RunNotFoundError as exc:
        raise http_error(404, "run_not_found", "Run not found") from exc
    ph = get_client()
    if ph is not None:
        ph.capture(
            distinct_id=get_distinct_id(),
            event="run_cancellation_requested",
            properties={
                "workspace_id": str(identity.workspace_id),
                "run_id": str(run_id),
                "cancellation_state": status,
            },
        )
    return CancellationResponse(run_id=run_id, state=status)
