"""WebSocket session state persistence and volume manifest I/O."""

import json
import logging
from typing import Any

from dspy.primitives.code_interpreter import FinalOutput

from fleet_rlm import runners
from fleet_rlm.core.interpreter import ExecutionProfile
from fleet_rlm.db import FleetRepository
from fleet_rlm.db.models import MemoryKind, MemoryScope, MemorySource
from fleet_rlm.db.types import IdentityUpsertResult, MemoryItemCreateRequest

from ..deps import ServerState
from .ws_helpers import _now_iso, _sanitize_for_log, _sanitize_id
from .ws_lifecycle import PersistenceRequiredError

logger = logging.getLogger(__name__)


# ── Manifest path ─────────────────────────────────────────────────────


def _manifest_path(workspace_id: str, user_id: str, session_id: str) -> str:
    safe_session_id = _sanitize_id(session_id, "default-session")
    return (
        f"workspaces/{workspace_id}/users/{user_id}/memory/"
        f"react-session-{safe_session_id}.json"
    )


# ── Volume I/O ─────────────────────────────────────────────────────────


async def _volume_load_manifest(agent: "runners.RLMReActChatAgent", path: str) -> dict:
    """Best-effort manifest load from Modal volume; returns empty dict if absent."""
    result = await agent.interpreter.aexecute(
        "text = load_from_volume(path)\nSUBMIT(text=text)",
        variables={"path": path},
        execution_profile=ExecutionProfile.MAINTENANCE,
    )
    if not isinstance(result, FinalOutput):
        return {}
    output = result.output if isinstance(result.output, dict) else {}
    text = str(output.get("text", ""))
    if not text or text.startswith("[file not found:") or text.startswith("[error:"):
        return {}
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


async def _volume_save_manifest(
    agent: "runners.RLMReActChatAgent", path: str, manifest: dict
) -> str | None:
    """Best-effort manifest save to Modal volume."""
    payload = json.dumps(manifest, ensure_ascii=False, default=str)
    result = await agent.interpreter.aexecute(
        "saved_path = save_to_volume(path, payload)\nSUBMIT(saved_path=saved_path)",
        variables={"path": path, "payload": payload},
        execution_profile=ExecutionProfile.MAINTENANCE,
    )
    if not isinstance(result, FinalOutput):
        return None
    output = result.output if isinstance(result.output, dict) else {}
    saved_path = str(output.get("saved_path", ""))
    if saved_path.startswith("["):
        return None
    return saved_path or None


# ── Session state persistence ──────────────────────────────────────────

import uuid  # noqa: E402


async def persist_session_state(
    *,
    state: ServerState,
    agent: "runners.RLMReActChatAgent",
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

    logs = manifest.get("logs")
    if not isinstance(logs, list):
        logs = []
        manifest["logs"] = logs

    memory = manifest.get("memory")
    if not isinstance(memory, list):
        memory = []
        manifest["memory"] = memory

    generated_docs = manifest.get("generated_docs")
    if not isinstance(generated_docs, list):
        generated_docs = []
        manifest["generated_docs"] = generated_docs

    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        artifacts = []
        manifest["artifacts"] = artifacts

    metadata = manifest.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
        manifest["metadata"] = metadata

    if latest_user_message:
        logs.append(
            {
                "timestamp": _now_iso(),
                "user_message": latest_user_message,
                "history_turns": len(exported_state.get("history", [])),
            }
        )
        # Lightweight conversational memory snapshot.
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
    manifest["rev"] = previous_rev + 1
    metadata["updated_at"] = _now_iso()
    metadata["history_turns"] = len(exported_state.get("history", []))
    metadata["document_count"] = len(exported_state.get("documents", {}))
    metadata["artifact_count"] = len(artifacts)
    manifest["state"] = exported_state  # Persist full state for volume restore (#24)
    session_data = session_record.get("session")
    if not isinstance(session_data, dict):
        session_data = {}
        session_record["session"] = session_data
    session_data["state"] = exported_state
    session_data["session_id"] = session_record.get("session_id")

    record_key = session_record.get("key")
    if isinstance(record_key, str):
        state.sessions[record_key] = session_record

    if include_volume_save and active_manifest_path and interpreter is not None:
        remote_manifest = await _volume_load_manifest(agent, active_manifest_path)
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
            saved_path = await _volume_save_manifest(
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

    if latest_user_message and repository is not None and identity_rows is not None:
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
            logger.warning(
                "Failed to persist memory item: %s",
                _sanitize_for_log(exc),
            )
