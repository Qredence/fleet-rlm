"""Durable idempotent Run cancellation resource."""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from fleet_rlm.api.dependencies import TurnLifecycleDep
from fleet_rlm.api.identity import RequestIdentity, get_request_identity
from fleet_rlm.chat.turn_lifecycle import TurnNotFoundError
from fleet_rlm.sessions.models import TurnAccess

router = APIRouter(tags=["runs"])


class CancellationResponse(BaseModel):
    run_id: UUID
    state: Literal["requested", "already_requested", "already_terminal"]


@router.put(
    "/api/runs/{run_id}/cancellation",
    response_model=CancellationResponse,
    operation_id="request_run_cancellation",
)
async def request_run_cancellation(
    run_id: UUID,
    identity: Annotated[RequestIdentity, Depends(get_request_identity)],
    lifecycle: TurnLifecycleDep,
) -> CancellationResponse:
    try:
        status = await lifecycle.request_cancel(TurnAccess(identity.user_id, identity.workspace_id), run_id)
    except TurnNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "run_not_found", "message": "Run not found"},
        ) from exc
    return CancellationResponse(run_id=run_id, state=status)
