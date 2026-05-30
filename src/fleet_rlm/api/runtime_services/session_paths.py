from __future__ import annotations

import warnings

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


def legacy_session_manifest_path(
    *,
    workspace_id: str | None,
    user_id: str | None,
    session_id: str | None,
) -> str | None:
    warnings.warn(
        "legacy_session_manifest_path is deprecated; use session_conversation_path instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    if not workspace_id or not user_id or not session_id:
        return None
    safe_session_id = _sanitize_id(session_id, "default-session")
    return f"meta/workspaces/{workspace_id}/users/{user_id}/react-session-{safe_session_id}.json"
