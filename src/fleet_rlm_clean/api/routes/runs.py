"""Run control routes: cancel (authenticated, idempotent)."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel

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
) -> CancelRunResponse:
    """Request cancellation of an in-flight run. Idempotent.

    Identity is required (dev headers or Neon JWT). Foundation registry is
    process-local and does not re-check run ownership beyond auth presence.
    """
    _ = identity  # auth gate; ownership binding deferred until run store is public
    registry = get_run_cancel_registry()
    newly = registry.request_cancel(run_id)
    return CancelRunResponse(
        run_id=run_id,
        cancelled=True,
        already_cancelled=not newly,
    )
