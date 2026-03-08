"""WebSocket message parsing and session switch/restore helpers."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import WebSocket
from pydantic import ValidationError

from ...deps import ServerState, session_key
from ...schemas import WSMessage
from .helpers import _try_send_json
from .session import _manifest_path, _volume_load_manifest


async def parse_ws_message_or_send_error(
    *,
    websocket: WebSocket,
    raw_payload: Any,
) -> WSMessage | None:
    """Parse a websocket payload into WSMessage, sending error envelopes on failure."""
    payload = raw_payload if isinstance(raw_payload, dict) else {}
    try:
        return WSMessage(**payload)
    except ValidationError as exc:
        raw_type = str(payload.get("type", "")).strip()
        if raw_type:
            await _try_send_json(
                websocket,
                {
                    "type": "error",
                    "message": f"Unknown message type: {raw_type}",
                },
            )
            return None
        await _try_send_json(
            websocket,
            {"type": "error", "message": f"Invalid payload: {exc}"},
        )
        return None


def resolve_session_identity(
    *,
    msg: WSMessage,
    workspace_id: str,
    user_id: str,
) -> tuple[str, str, str]:
    """Resolve canonical workspace/user and message session id."""
    sess_id = msg.session_id or str(uuid.uuid4())
    return workspace_id, user_id, sess_id


async def switch_session_if_needed(
    *,
    state: ServerState,
    agent: Any,
    interpreter: Any,
    workspace_id: str,
    user_id: str,
    sess_id: str,
    active_key: str | None,
    session_record: dict[str, Any] | None,
    last_loaded_docs_path: str | None,
    local_persist: Callable[..., Awaitable[None]],
) -> tuple[str, str, dict[str, Any], str | None]:
    """Switch and restore session state when session identity changed."""
    key = session_key(workspace_id, user_id, sess_id)
    manifest_path = _manifest_path(workspace_id, user_id, sess_id)

    if active_key == key and session_record is not None:
        return key, manifest_path, session_record, last_loaded_docs_path

    if session_record is not None:
        await local_persist(include_volume_save=True)

    cached = state.sessions.get(key)
    if cached is None:
        manifest = (
            await _volume_load_manifest(agent, manifest_path)
            if interpreter is not None
            else {}
        )
        cached = {
            "key": key,
            "workspace_id": workspace_id,
            "user_id": user_id,
            "session_id": sess_id,
            "manifest": manifest if isinstance(manifest, dict) else {},
            "session": {"state": {}, "session_id": sess_id},
        }

    cached["session_id"] = sess_id
    state.sessions[key] = cached

    session_data = cached.get("session")
    restored_state: Any = (
        session_data.get("state", {}) if isinstance(session_data, dict) else {}
    )
    manifest_data = cached.get("manifest")
    if not restored_state and isinstance(manifest_data, dict):
        restored_state = manifest_data.get("state", {})

    if isinstance(restored_state, dict) and restored_state:
        agent.import_session_state(restored_state)
    else:
        # No saved state — reset agent to prevent cross-session leakage.
        agent.reset(clear_sandbox_buffers=True)

    return key, manifest_path, cached, None
