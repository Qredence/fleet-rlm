"""Durable idempotent Run cancellation resource."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel

from fleet_rlm.api.dependencies import LocalScopeDep, TurnLifecycleDep
from fleet_rlm.api.errors import http_error
from fleet_rlm.chat.turn_lifecycle import TurnNotFoundError
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
    lifecycle: TurnLifecycleDep,
) -> CancellationResponse:
    try:
        status = await lifecycle.request_cancel(TurnAccess(identity.user_id, identity.workspace_id), run_id)
    except TurnNotFoundError as exc:
        raise http_error(404, "run_not_found", "Run not found") from exc
    return CancellationResponse(run_id=run_id, state=status)
