"""Run service encapsulating execution run step retrieval."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import HTTPException

from fleet_rlm.integrations.database.repository_identity import IdentityUpsertResult

from ..schemas.sandbox import RunStepItem, RunStepListResponse


def _parse_run_uuid(run_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Run not found.") from exc


class RunService:
    """Encapsulates run step retrieval logic."""

    def __init__(self, persistence: Any) -> None:
        self._persistence = persistence

    async def get_run_steps(
        self,
        *,
        persisted_identity: IdentityUpsertResult,
        run_id: str,
        limit: int,
        offset: int,
    ) -> RunStepListResponse:
        """Return paginated execution steps for a run."""
        run_uuid = _parse_run_uuid(run_id)

        run = await self._persistence.get_run(
            tenant_id=persisted_identity.tenant_id,
            run_id=run_uuid,
            workspace_id=persisted_identity.workspace_id,
            created_by_user_id=persisted_identity.user_id,
        )
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found.")

        steps, total = await self._persistence.get_run_steps_paginated(
            tenant_id=persisted_identity.tenant_id,
            run_id=run_uuid,
            workspace_id=persisted_identity.workspace_id,
            created_by_user_id=persisted_identity.user_id,
            limit=limit,
            offset=offset,
        )

        return RunStepListResponse(
            items=[
                RunStepItem(
                    id=str(s.id),
                    step_index=s.step_index,
                    step_type=s.step_type.value
                    if hasattr(s.step_type, "value")
                    else str(s.step_type),
                    tool_name=s.tool_name,
                    tokens_in=s.tokens_in,
                    tokens_out=s.tokens_out,
                    latency_ms=s.latency_ms,
                    created_at=s.created_at.isoformat(),
                )
                for s in steps
            ],
            total=total,
            offset=offset,
            limit=limit,
            has_more=(offset + limit) < total,
        )
