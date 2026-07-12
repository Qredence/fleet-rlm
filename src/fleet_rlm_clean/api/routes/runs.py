"""Run control routes: cancel (authenticated, ownership-checked, idempotent)."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from fleet_rlm_clean.api.dependencies import OptionalSessionRepositoryDep
from fleet_rlm_clean.api.identity import RequestIdentity, get_request_identity
from fleet_rlm_clean.rlm.cancel import get_run_cancel_registry

router = APIRouter(tags=["runs"])


class CancelRunResponse(BaseModel):
    run_id: UUID
    cancelled: bool
    already_cancelled: bool


@router.post("/api/runs/{run_id}/cancel", response_model=CancelRunResponse)
async def cancel_run(
    run_id: UUID,
    identity: Annotated[RequestIdentity, Depends(get_request_identity)],
    repo: OptionalSessionRepositoryDep,
) -> CancelRunResponse:
    """Request cancellation of an owned in-flight Run. Idempotent.

    Missing or foreign runs return 404 (never 403). Durable cancel intent is
    recorded when a SessionRepository is configured; the in-process registry
    always receives the signal for the local worker.
    """
    registry = get_run_cancel_registry()
    if repo is not None:
        outcome = await repo.request_cancel(
            run_id,
            user_id=identity.user_id,
            workspace_id=identity.workspace_id,
        )
        if outcome == "not_found":
            raise HTTPException(status_code=404, detail="run not found")
        newly = registry.request_cancel(run_id)
        already = outcome == "already_cancelled" or not newly
        return CancelRunResponse(
            run_id=run_id,
            cancelled=True,
            already_cancelled=already,
        )

    owned = registry.authorize(
        run_id,
        user_id=identity.user_id,
        workspace_id=identity.workspace_id,
    )
    if owned is None:
        raise HTTPException(status_code=404, detail="run not found")
    if owned.terminal:
        return CancelRunResponse(
            run_id=run_id,
            cancelled=True,
            already_cancelled=True,
        )
    newly = registry.request_cancel(run_id)
    return CancelRunResponse(
        run_id=run_id,
        cancelled=True,
        already_cancelled=not newly,
    )
