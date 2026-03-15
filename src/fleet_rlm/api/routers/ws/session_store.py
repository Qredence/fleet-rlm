"""Session manifest normalization and persistence helpers."""

from __future__ import annotations

from typing import Any

from fleet_rlm.db import FleetRepository
from fleet_rlm.db.models import MemoryKind, MemoryScope, MemorySource
from fleet_rlm.db.types import IdentityUpsertResult, MemoryItemCreateRequest

from ...deps import ServerState
from .helpers import _now_iso, _sanitize_for_log
from .lifecycle import PersistenceRequiredError


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
                "timestamp": _now_iso(),
                "user_message": latest_user_message,
                "history_turns": len(exported_state.get("history", [])),
            }
        )
        memory.append(
            {
                "timestamp": _now_iso(),
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
    metadata["updated_at"] = _now_iso()
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
    """Propagate exported state into session record and state cache.

    Note: this mutates *session_record* in-place, which updates the
    ``ServerState.sessions`` cache by reference.  This is safe under
    the current single-writer async model (one event-loop, one WebSocket
    handler per session key at a time).  If concurrent writes for the
    same session key ever become possible, external synchronisation
    (e.g., an asyncio.Lock keyed by session key) will be required.
    """
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
    logger,
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
