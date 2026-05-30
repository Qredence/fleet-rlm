from __future__ import annotations


def test_session_conversation_path_uses_phase_one_sessions_root() -> None:
    from fleet_rlm.api.runtime_services.session_paths import (
        session_conversation_path,
        session_root_path,
        session_scratchpad_path,
        session_workspace_link_path,
    )

    assert session_root_path("session/with spaces") == "sessions/session-with-spaces"
    assert session_conversation_path("session/with spaces") == "sessions/session-with-spaces/conversation.json"
    assert session_scratchpad_path("session/with spaces") == "sessions/session-with-spaces/scratchpad"
    assert session_workspace_link_path("session/with spaces") == "sessions/session-with-spaces/workspace"


def test_switch_manifest_path_uses_phase_one_sessions_root() -> None:
    from fleet_rlm.api.routers.ws.session import _legacy_switch_manifest_path, _switch_manifest_path

    assert (
        _switch_manifest_path(
            owner_id="owner-1",
            workspace_id="workspace-1",
            session_id="session-1",
        )
        == "sessions/session-1/conversation.json"
    )
    assert (
        _legacy_switch_manifest_path(
            owner_id="owner-1",
            workspace_id="workspace-1",
            session_id="session-1",
        )
        == "meta/workspaces/owner-1/users/workspace-1/react-session-session-1.json"
    )
