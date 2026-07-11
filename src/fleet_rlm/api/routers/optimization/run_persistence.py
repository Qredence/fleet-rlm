"""Shared optimization run persistence for blocking and async paths."""

from __future__ import annotations

import hashlib
import logging
import uuid
from pathlib import Path
from typing import Any

from fleet_rlm.integrations.database.repository_identity import IdentityUpsertResult
from fleet_rlm.integrations.database.repository_optimization import OptimizationArtifactCreateRequest
from fleet_rlm.integrations.persistence_protocol import UnsupportedLocalCapabilityError

logger = logging.getLogger(__name__)


def _resolve_target_from_result(result: dict[str, Any]) -> tuple[str, str, str]:
    """Return (target_kind, target_id, artifact_kind) from runner result metadata."""
    metadata = result.get("run_metadata") if isinstance(result.get("run_metadata"), dict) else {}
    target = metadata.get("target") if isinstance(metadata.get("target"), dict) else {}
    kind = str(target.get("kind") or metadata.get("target_kind") or "module")
    if kind not in {"module", "skill"}:
        kind = "module"
    target_id = str(
        target.get("target_id")
        or target.get("id")
        or metadata.get("module_slug")
        or metadata.get("skill_name")
        or result.get("module_slug")
        or "unknown"
    )
    artifact_kind = "skill_markdown" if kind == "skill" else "module_state_json"
    return kind, target_id, artifact_kind


async def persist_optimization_run_success(
    *,
    persistence: Any,
    persisted_identity: IdentityUpsertResult,
    run_uuid: uuid.UUID,
    result: dict[str, Any],
) -> None:
    """Persist evaluation results, prompt snapshots, completion metadata, and candidate artifact."""
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

    output_path = result.get("output_path")
    if not output_path:
        return
    path = Path(str(output_path))
    if not path.is_file():
        logger.warning("Optimization output missing on disk; skipping candidate artifact for run %s", run_uuid)
        return
    target_kind, target_id, artifact_kind = _resolve_target_from_result(result)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    try:
        await persistence.create_optimization_artifact_version(
            OptimizationArtifactCreateRequest(
                tenant_id=persisted_identity.tenant_id,
                workspace_id=persisted_identity.workspace_id,
                optimization_run_id=run_uuid,
                target_kind=target_kind,
                target_id=target_id,
                artifact_kind=artifact_kind,
                artifact_path=str(path),
                artifact_sha256=digest,
                created_by_user_id=persisted_identity.user_id,
                status="candidate",
            )
        )
    except UnsupportedLocalCapabilityError:
        logger.debug("Local store does not persist optimization artifact versions for run %s", run_uuid)
    except Exception:
        logger.exception("Failed to persist candidate artifact for optimization run %s", run_uuid)


def sanitize_optimization_client_error(exc: Exception | str) -> str:
    """Map exceptions to stable client-safe optimization error strings."""
    if isinstance(exc, str):
        text = exc.strip()
    else:
        text = str(exc).strip()
    lowered = text.lower()
    if "cancel" in lowered:
        return "Optimization cancelled."
    if "timeout" in lowered or "timed out" in lowered:
        return "Optimization timed out."
    if "fingerprint" in lowered:
        return "Optimization resume fingerprint mismatch."
    if "dataset" in lowered and ("not found" in lowered or "missing" in lowered):
        return "Optimization dataset is unavailable."
    # Never forward raw provider/auth/path details to clients.
    return "Optimization failed."


async def persist_optimization_run_failure(
    *,
    persistence: Any,
    persisted_identity: IdentityUpsertResult,
    run_uuid: uuid.UUID,
    error: str,
    cancelled: bool = False,
) -> None:
    """Mark an optimization run as failed or cancelled with a sanitized message."""
    public_error = sanitize_optimization_client_error(error)
    if cancelled or public_error == "Optimization cancelled.":
        cancel = getattr(persistence, "cancel_optimization_run", None)
        if callable(cancel):
            await cancel(
                tenant_id=persisted_identity.tenant_id,
                run_id=run_uuid,
                workspace_id=persisted_identity.workspace_id,
                created_by_user_id=persisted_identity.user_id,
                error=public_error,
            )
            return
    await persistence.fail_optimization_run(
        tenant_id=persisted_identity.tenant_id,
        run_id=run_uuid,
        workspace_id=persisted_identity.workspace_id,
        created_by_user_id=persisted_identity.user_id,
        error=public_error,
    )


__all__ = [
    "persist_optimization_run_failure",
    "persist_optimization_run_success",
    "sanitize_optimization_client_error",
]
