"""Shared optimization run persistence for blocking and async paths."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fleet_rlm.integrations.database.repository_identity import IdentityUpsertResult

logger = logging.getLogger(__name__)


async def persist_optimization_run_success(
    *,
    persistence: Any,
    persisted_identity: IdentityUpsertResult,
    run_uuid: uuid.UUID,
    result: dict[str, Any],
) -> None:
    """Persist evaluation results, prompt snapshots, and completion metadata."""
    await persistence.save_evaluation_results(
        tenant_id=persisted_identity.tenant_id,
        run_id=run_uuid,
        workspace_id=persisted_identity.workspace_id,
        created_by_user_id=persisted_identity.user_id,
        results=result.get("evaluation_results", []),
    )
    await persistence.save_prompt_snapshots(
        tenant_id=persisted_identity.tenant_id,
        run_id=run_uuid,
        workspace_id=persisted_identity.workspace_id,
        created_by_user_id=persisted_identity.user_id,
        snapshots=result.get("prompt_snapshots", []),
    )
    await persistence.complete_optimization_run(
        tenant_id=persisted_identity.tenant_id,
        run_id=run_uuid,
        workspace_id=persisted_identity.workspace_id,
        created_by_user_id=persisted_identity.user_id,
        train_examples=result.get("train_examples", 0),
        validation_examples=result.get("validation_examples", 0),
        validation_score=result.get("validation_score"),
        output_path=result.get("output_path"),
        manifest_path=result.get("manifest_path"),
        metadata_json=result.get("run_metadata"),
    )


async def persist_optimization_run_failure(
    *,
    persistence: Any,
    persisted_identity: IdentityUpsertResult,
    run_uuid: uuid.UUID,
    error: str,
) -> None:
    """Mark an optimization run as failed."""
    await persistence.fail_optimization_run(
        tenant_id=persisted_identity.tenant_id,
        run_id=run_uuid,
        workspace_id=persisted_identity.workspace_id,
        created_by_user_id=persisted_identity.user_id,
        error=error,
    )


__all__ = ["persist_optimization_run_failure", "persist_optimization_run_success"]
