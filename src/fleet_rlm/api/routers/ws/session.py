"""Websocket chat session switching and state restoration."""

from __future__ import annotations

from typing import Any

from ...dependencies import ServerState, session_key
from ...server_utils import owner_fingerprint
from .manifest import _manifest_path, load_manifest_from_volume
from .types import ChatAgentProtocol, LocalPersistFn


async def switch_session_if_needed(
    *,
    state: ServerState,
    agent: ChatAgentProtocol,
    interpreter: object | None,
    workspace_id: str,
    user_id: str,
    sess_id: str,
    owner_tenant_claim: str,
    owner_user_claim: str,
    active_key: str | None,
    session_record: dict[str, Any] | None,
    last_loaded_docs_path: str | None,
    local_persist: LocalPersistFn,
) -> tuple[str, str, dict[str, Any], str | None]:
    """Switch and restore session state when session identity changed."""
    key = session_key(owner_tenant_claim, owner_user_claim, sess_id)
    manifest_path = _manifest_path(workspace_id, user_id, sess_id)

    if active_key == key and session_record is not None:
        return key, manifest_path, session_record, last_loaded_docs_path

    if session_record is not None:
        await local_persist(include_volume_save=True)

    cached = state.sessions.get(key)
    if cached is None:
        manifest = (
            await load_manifest_from_volume(agent, manifest_path)
            if interpreter is not None
            else {}
        )
        cached = {
            "key": key,
            "workspace_id": workspace_id,
            "user_id": user_id,
            "owner_tenant_claim": owner_tenant_claim,
            "owner_user_claim": owner_user_claim,
            "owner_fingerprint": owner_id,
            "session_id": sess_id,
            "manifest": manifest if isinstance(manifest, dict) else {},
            "session": {"state": {}, "session_id": sess_id},
        }

    cached["session_id"] = sess_id
    cached["workspace_id"] = workspace_id
    cached["user_id"] = user_id
    cached["owner_tenant_claim"] = owner_tenant_claim
    cached["owner_user_claim"] = owner_user_claim
    cached["owner_fingerprint"] = owner_id
    state.sessions[key] = cached

    session_data = cached.get("session")
    restored_state: Any = (
        session_data.get("state", {}) if isinstance(session_data, dict) else {}
    )
    manifest_data = cached.get("manifest")
    if not restored_state and isinstance(manifest_data, dict):
        restored_state = manifest_data.get("state", {})

    if isinstance(restored_state, dict) and restored_state:
        await agent.aimport_session_state(restored_state)
    else:
        await agent.areset(clear_sandbox_buffers=True)

    return key, manifest_path, cached, None
