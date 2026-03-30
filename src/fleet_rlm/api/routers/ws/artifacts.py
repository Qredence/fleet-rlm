"""Session artifact tracking and artifact metadata persistence helpers."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fleet_rlm.integrations.database import FleetRepository
from fleet_rlm.integrations.database import ArtifactKind
from fleet_rlm.integrations.database.types import (
    ArtifactCreateRequest,
    IdentityUpsertResult,
)

from .execution_support import now_iso
from .failures import PersistenceRequiredError
from .helpers import _sanitize_for_log

logger = logging.getLogger(__name__)

_ARTIFACT_TRACKING_COMMANDS = {"save_buffer", "load_volume", "write_to_file"}


def is_artifact_tracking_command(command: str) -> bool:
    return command in _ARTIFACT_TRACKING_COMMANDS


def append_session_artifact(
    *,
    session_record: dict[str, Any],
    command: str,
    args: dict[str, Any],
    result: dict[str, Any],
) -> str:
    manifest = session_record.get("manifest")
    if not isinstance(manifest, dict):
        manifest = {}
        session_record["manifest"] = manifest

    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        artifacts = []
        manifest["artifacts"] = artifacts

    artifact_uri = str(
        result.get("saved_path") or args.get("path") or result.get("alias") or ""
    )
    artifacts.append(
        {
            "timestamp": now_iso(),
            "command": command,
            "path": artifact_uri or None,
        }
    )
    return artifact_uri


async def persist_artifact_metadata(
    *,
    repository: FleetRepository,
    identity_rows: IdentityUpsertResult,
    session_record: dict[str, Any],
    command: str,
    args: dict[str, Any],
    artifact_uri: str,
) -> None:
    run_id_raw = session_record.get("last_run_db_id")
    if not run_id_raw:
        return

    run_id = uuid.UUID(str(run_id_raw))
    step_id = session_record.get("last_step_db_id")
    step_uuid = uuid.UUID(str(step_id)) if step_id else None
    await repository.store_artifact(
        ArtifactCreateRequest(
            tenant_id=identity_rows.tenant_id,
            run_id=run_id,
            step_id=step_uuid,
            kind=ArtifactKind.FILE,
            uri=artifact_uri or "memory://unknown",
            metadata_json={
                "command": command,
                "args": args,
            },
        )
    )


async def track_command_artifact_if_needed(
    *,
    session_record: dict[str, Any] | None,
    command: str,
    args: dict[str, Any],
    result: Any,
    repository: FleetRepository | None,
    identity_rows: IdentityUpsertResult | None,
    persistence_required: bool,
) -> None:
    """Track command-produced artifacts in session metadata and durable storage."""
    if (
        session_record is None
        or not isinstance(result, dict)
        or not is_artifact_tracking_command(command)
    ):
        return

    result_dict = result
    artifact_uri = append_session_artifact(
        session_record=session_record,
        command=command,
        args=args,
        result=result_dict,
    )
    if repository is None or identity_rows is None:
        return

    try:
        await persist_artifact_metadata(
            repository=repository,
            identity_rows=identity_rows,
            session_record=session_record,
            command=command,
            args=args,
            artifact_uri=artifact_uri,
        )
    except Exception as exc:
        if persistence_required:
            raise PersistenceRequiredError(
                "artifact_persist_failed",
                f"Failed to persist artifact metadata: {exc}",
            ) from exc
        logger.warning(
            "Failed to persist artifact metadata: %s",
            _sanitize_for_log(exc),
        )
