from __future__ import annotations

from fleet_rlm.utils.identity import sanitize_id as _sanitize_id


def session_root_path(session_id: str | None) -> str | None:
    if session_id is None or not session_id.strip():
        return None
    safe_session_id = _sanitize_id(session_id, "default-session")
    return f"sessions/{safe_session_id}"


def session_conversation_path(session_id: str | None) -> str | None:
    root = session_root_path(session_id)
    if root is None:
        return None
    return f"{root}/conversation.json"


def session_scratchpad_path(session_id: str | None) -> str | None:
    root = session_root_path(session_id)
    if root is None:
        return None
    return f"{root}/scratchpad"


def session_workspace_link_path(session_id: str | None) -> str | None:
    root = session_root_path(session_id)
    if root is None:
        return None
    return f"{root}/workspace"
