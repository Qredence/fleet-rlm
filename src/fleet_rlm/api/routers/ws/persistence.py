"""WebSocket chat manifest/state persistence orchestration."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fleet_rlm.integrations.database import FleetRepository
from fleet_rlm.integrations.database.models import (
    MemoryKind,
    MemoryScope,
    MemorySource,
)
from fleet_rlm.integrations.database.types import (
    IdentityUpsertResult,
    MemoryItemCreateRequest,
)

from ...dependencies import ServerState
from .execution_support import now_iso
from .failures import PersistenceRequiredError
from .helpers import _sanitize_for_log
from .manifest import load_manifest_from_volume, save_manifest_to_volume
from .types import ChatAgentProtocol

logger = logging.getLogger(__name__)


def ensure_manifest_shape(manifest: dict[str, Any]) -> dict[str, Any]:
    """Normalize mutable manifest structure and expected keys."""
    if not isinstance(manifest.get("logs"), list):
        manifest["logs"] = []
    if not isinstance(manifest.get("memory"), list):
        manifest["memory"] = []
    if not isinstance(manifest.get("generated_docs"), list):
        manifest["generated_docs"] = []
    if not isinstance(manifest.get("artifacts"), list):
        manifest["artifacts"] = []
    if not isinstance(manifest.get("metadata"), dict):
        manifest["metadata"] = {}
    return manifest


def update_manifest_from_exported_state(
    *,
    manifest: dict[str, Any],
    exported_state: dict[str, Any],
    latest_user_message: str,
) -> tuple[int, int]:
    """Update manifest with latest state snapshot and optional user message entry."""
    ensure_manifest_shape(manifest)

    logs = manifest["logs"]
    memory = manifest["memory"]
    generated_docs = manifest["generated_docs"]
    artifacts = manifest["artifacts"]
    metadata = manifest["metadata"]

    if latest_user_message:
        logs.append(
            {
                "timestamp": now_iso(),
                "user_message": latest_user_message,
                "history_turns": len(exported_state.get("history", [])),
            }
        )
        memory.append(
            {
                "timestamp": now_iso(),
                "content": latest_user_message[:400],
            }
        )

    generated_docs[:] = sorted(list(exported_state.get("documents", {}).keys()))

    previous_rev_raw = manifest.get("rev", 0)
    previous_rev_candidate = (
        previous_rev_raw if isinstance(previous_rev_raw, (int, float, str)) else 0
    )
    try:
        previous_rev = int(previous_rev_candidate)
    except (TypeError, ValueError):
        previous_rev = 0

    next_rev = previous_rev + 1
    manifest["rev"] = next_rev
    metadata["updated_at"] = now_iso()
    metadata["history_turns"] = len(exported_state.get("history", []))
    metadata["document_count"] = len(exported_state.get("documents", {}))
    metadata["artifact_count"] = len(artifacts)
    manifest["state"] = exported_state
    return previous_rev, next_rev


def sync_session_record_state(
    *,
    state: ServerState,
    session_record: dict[str, Any],
    exported_state: dict[str, Any],
) -> None:
    """Propagate exported state into session record and state cache."""
    session_data = session_record.get("session")
    if not isinstance(session_data, dict):
        session_data = {}
        session_record["session"] = session_data
    session_data["state"] = exported_state
    session_data["session_id"] = session_record.get("session_id")

    record_key = session_record.get("key")
    if isinstance(record_key, str):
        state.sessions[record_key] = session_record


async def persist_memory_item_if_needed(
    *,
    repository: FleetRepository | None,
    identity_rows: IdentityUpsertResult | None,
    active_run_db_id: Any,
    latest_user_message: str,
    persistence_required: bool,
) -> None:
    """Persist a user-input memory item when repository context is available."""
    if not latest_user_message or repository is None or identity_rows is None:
        return
    try:
        await repository.store_memory_item(
            MemoryItemCreateRequest(
                tenant_id=identity_rows.tenant_id,
                scope=MemoryScope.RUN
                if active_run_db_id is not None
                else MemoryScope.USER,
                scope_id=str(active_run_db_id or identity_rows.user_id),
                kind=MemoryKind.NOTE,
                source=MemorySource.USER_INPUT,
                content_text=latest_user_message[:1000],
                tags=["ws", "chat"],
            )
        )
    except Exception as exc:
        if persistence_required:
            raise PersistenceRequiredError(
                "memory_item_persist_failed",
                f"Failed to persist memory item: {exc}",
            ) from exc
        logger.warning("Failed to persist memory item: %s", _sanitize_for_log(exc))


async def persist_session_state(
    *,
    state: ServerState,
    agent: ChatAgentProtocol,
    session_record: dict[str, Any] | None,
    active_manifest_path: str | None,
    active_run_db_id: uuid.UUID | None,
    interpreter: Any | None,
    repository: FleetRepository | None,
    identity_rows: IdentityUpsertResult | None,
    persistence_required: bool,
    include_volume_save: bool = True,
    latest_user_message: str = "",
) -> None:
    """Persist current session state to in-memory cache, volume, and DB."""
    if session_record is None:
        return
    exported_state = agent.export_session_state()
    manifest = session_record.get("manifest")
    if not isinstance(manifest, dict):
        manifest = {}
        session_record["manifest"] = manifest

    ensure_manifest_shape(manifest)
    previous_rev, _next_rev = update_manifest_from_exported_state(
        manifest=manifest,
        exported_state=exported_state,
        latest_user_message=latest_user_message,
    )
    sync_session_record_state(
        state=state,
        session_record=session_record,
        exported_state=exported_state,
    )

    if include_volume_save and active_manifest_path and interpreter is not None:
        remote_manifest = await load_manifest_from_volume(agent, active_manifest_path)
        remote_rev_raw = remote_manifest.get("rev", 0)
        remote_rev_candidate = (
            remote_rev_raw if isinstance(remote_rev_raw, (int, float, str)) else 0
        )
        try:
            remote_rev = int(remote_rev_candidate)
        except (TypeError, ValueError):
            remote_rev = 0

        if remote_rev > previous_rev:
            message = (
                "Session manifest revision conflict detected "
                f"(remote_rev={remote_rev}, local_rev={previous_rev})"
            )
            if persistence_required:
                raise PersistenceRequiredError("manifest_conflict", message)
            logger.warning(message)
        else:
            saved_path = await save_manifest_to_volume(
                agent, active_manifest_path, manifest
            )
            if saved_path is None:
                message = (
                    "Failed to save session manifest to volume "
                    f"(path={active_manifest_path})"
                )
                if persistence_required:
                    raise PersistenceRequiredError("manifest_write_failed", message)
                logger.warning(message)

    await persist_memory_item_if_needed(
        repository=repository,
        identity_rows=identity_rows,
        active_run_db_id=active_run_db_id,
        latest_user_message=latest_user_message,
        persistence_required=persistence_required,
    )
