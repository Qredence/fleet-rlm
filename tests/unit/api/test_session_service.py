from __future__ import annotations

from types import SimpleNamespace

from fleet_rlm.api.runtime_services.session_service import SessionService


def test_list_session_state_counts_exported_turns_schema() -> None:
    service = SessionService(persistence=None)
    identity = SimpleNamespace(tenant_claim="tenant", user_claim="user")
    session_cache = {
        "owner:abc:session-1": {
            "owner_tenant_claim": "tenant",
            "owner_user_claim": "user",
            "workspace_id": "default",
            "user_id": "anonymous",
            "session_id": "session-1",
            "session": {
                "state": {
                    "turns": [
                        {"user_message": "remember HISTORY_CHECK", "response": "ok"},
                        {"user_message": "what marker?", "response": "HISTORY_CHECK"},
                    ]
                }
            },
        }
    }

    response = service.list_session_state(session_cache=session_cache, identity=identity)

    assert response.ok is True
    assert len(response.sessions) == 1
    assert response.sessions[0].history_turns == 2
