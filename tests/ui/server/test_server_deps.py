from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from fleet_rlm.server.deps import ServerState, get_server_state, session_key


def test_server_state_init():
    state = ServerState()
    assert state.planner_lm is None
    assert state.config is not None
    assert state.is_ready is False
    assert state.sessions == {}
    assert state.repository is None
    assert state.auth_provider is None


def test_server_state_ready():
    state = ServerState()
    state.config.database_required = False
    state.planner_lm = "mock_lm"
    assert state.is_ready is True


def test_get_server_state_missing_raises_http_503():
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    with pytest.raises(HTTPException) as exc:
        get_server_state(request)
    assert exc.value.status_code == 503


def test_session_key():
    assert session_key("workspace", "user") == "workspace:user:__default__"
    assert session_key("workspace", "user", "session-1") == "workspace:user:session-1"
