"""Session restore/switch policy isolated from websocket transport."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..dependencies import ServerState, session_key
from ..server_utils import owner_fingerprint
from ..routers.ws.manifest import _manifest_path, load_manifest_from_volume
from ..routers.ws.types import ChatAgentProtocol, LocalPersistFn


@dataclass(slots=True)
class SessionSwitchOutcome:
    """Resolved session target and restored in-memory session record."""

    key: str
    manifest_path: str
    session_record: dict[str, Any]
    last_loaded_docs_path: str | None


async def _restore_agent_state(
    *,
    agent: ChatAgentProtocol,
    restored_state: Any,
) -> None:
    if isinstance(restored_state, dict) and restored_state:
        await agent.aimport_session_state(restored_state)
        return
    await agent.areset(clear_sandbox_buffers=True)


def _restoreable_session_state(session_record: dict[str, Any]) -> Any:
    session_data = session_record.get("session")
    restored_state: Any = (
        session_data.get("state", {}) if isinstance(session_data, dict) else {}
    )
    manifest_data = session_record.get("manifest")
    if not restored_state and isinstance(manifest_data, dict):
        restored_state = manifest_data.get("state", {})
    return restored_state


async def switch_execution_session(
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
) -> SessionSwitchOutcome:
    """Apply websocket session-switch policy without owning socket transport."""

    key = session_key(owner_tenant_claim, owner_user_claim, sess_id)
    owner_id = owner_fingerprint(owner_tenant_claim, owner_user_claim)
    manifest_path = _manifest_path(owner_id, workspace_id, sess_id)

    if active_key == key and session_record is not None:
        return SessionSwitchOutcome(
            key=key,
            manifest_path=manifest_path,
            session_record=session_record,
            last_loaded_docs_path=last_loaded_docs_path,
        )

    if session_record is not None:
        await local_persist(include_volume_save=True)

    cached = state.sessions.get(key)
    if cached is None:
        manifest = (
            await load_manifest_from_volume(agent, manifest_path)
            if interpreter is not None
            else {}
        )
        cached: dict[str, Any] = {
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
        try:
            from fleet_rlm.integrations.local_store import (
                create_session as _db_create,
            )

            cached["db_session_id"] = _db_create(title=sess_id).id
        except Exception:
            # Best-effort local DB session creation; continue without db_session_id.
            pass

    cached["session_id"] = sess_id
    cached["workspace_id"] = workspace_id
    cached["user_id"] = user_id
    cached["owner_tenant_claim"] = owner_tenant_claim
    cached["owner_user_claim"] = owner_user_claim
    cached["owner_fingerprint"] = owner_id
    state.sessions[key] = cached

    await _restore_agent_state(
        agent=agent,
        restored_state=_restoreable_session_state(cached),
    )

    return SessionSwitchOutcome(
        key=key,
        manifest_path=manifest_path,
        session_record=cached,
        last_loaded_docs_path=None,
    )
