"""WebSocket message parsing and session switch/restore helpers."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import WebSocket
from pydantic import ValidationError

from ...dependencies import ServerState, session_key
from ...schemas import WSMessage
from .contracts import ChatAgentProtocol, LocalPersistFn
from .helpers import _error_envelope, _try_send_json
from .session import _manifest_path, _volume_load_manifest


async def parse_ws_message_or_send_error(
    *,
    websocket: WebSocket,
    raw_payload: object,
) -> WSMessage | None:
    """Parse a websocket payload into WSMessage, sending error envelopes on failure."""
    payload: dict[str, Any]
    if isinstance(raw_payload, dict):
        payload = {
            str(key): value
            for key, value in raw_payload.items()
            if isinstance(key, str)
        }
    else:
        payload = {}
    try:
        return WSMessage.model_validate(payload)
    except ValidationError as exc:
        raw_type = str(payload.get("type", "")).strip()
        if raw_type and raw_type not in {"message", "cancel", "command"}:
            await _try_send_json(
                websocket,
                {
                    "type": "error",
                    "message": f"Unknown message type: {raw_type}",
                },
            )
            return None
        errors = exc.errors()
        error_types = {str(error.get("type", "")) for error in errors}
        if "daytona_repo_ref_requires_repo" in error_types:
            await _try_send_json(
                websocket,
                _error_envelope(
                    code="daytona_repo_ref_requires_repo",
                    message="Daytona repo_ref requires repo_url.",
                ),
            )
            return None
        if "daytona_max_depth_removed" in error_types:
            await _try_send_json(
                websocket,
                _error_envelope(
                    code="daytona_max_depth_removed",
                    message=(
                        "Daytona websocket requests no longer accept max_depth; "
                        "use the server-configured recursion depth."
                    ),
                ),
            )
            return None
        message = "; ".join(
            error.get("msg", "Invalid websocket payload") for error in errors
        )
        await _try_send_json(
            websocket,
            {"type": "error", "message": f"Invalid payload: {message}"},
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
    agent: ChatAgentProtocol,
    interpreter: object | None,
    workspace_id: str,
    user_id: str,
    sess_id: str,
    active_key: str | None,
    session_record: dict[str, Any] | None,
    last_loaded_docs_path: str | None,
    local_persist: LocalPersistFn,
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
