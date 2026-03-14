"""WebSocket session state persistence and volume manifest I/O."""

import json
import logging
import uuid
from typing import Any

from dspy.primitives.code_interpreter import FinalOutput

from fleet_rlm.core.interpreter import ExecutionProfile
from fleet_rlm.db import FleetRepository
from fleet_rlm.db.types import IdentityUpsertResult

from ...deps import ServerState
from .contracts import ChatAgentProtocol
from .helpers import _sanitize_id
from .lifecycle import PersistenceRequiredError
from .session_store import (
    ensure_manifest_shape,
    persist_memory_item_if_needed,
    sync_session_record_state,
    update_manifest_from_exported_state,
)

logger = logging.getLogger(__name__)


# ── Manifest path ─────────────────────────────────────────────────────


def _manifest_path(workspace_id: str, user_id: str, session_id: str) -> str:
    safe_session_id = _sanitize_id(session_id, "default-session")
    return (
        f"workspaces/{workspace_id}/users/{user_id}/memory/"
        f"react-session-{safe_session_id}.json"
    )


# ── Volume I/O ─────────────────────────────────────────────────────────


async def _volume_load_manifest(agent: ChatAgentProtocol, path: str) -> dict:
    """Best-effort manifest load from Modal volume; returns empty dict if absent."""
    interpreter = agent.interpreter
    if interpreter is None:
        return {}
    result = await interpreter.aexecute(
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
    agent: ChatAgentProtocol,
    path: str,
    manifest: dict,
) -> str | None:
    """Best-effort manifest save to Modal volume."""
    interpreter = agent.interpreter
    if interpreter is None:
        return None
    payload = json.dumps(manifest, ensure_ascii=False, default=str)
    result = await interpreter.aexecute(
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

    await persist_memory_item_if_needed(
        repository=repository,
        identity_rows=identity_rows,
        active_run_db_id=active_run_db_id,
        latest_user_message=latest_user_message,
        persistence_required=persistence_required,
        logger=logger,
    )
