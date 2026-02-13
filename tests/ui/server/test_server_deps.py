from fleet_rlm.server.deps import ServerState, get_config, get_planner_lm, session_key


def test_server_state_init():
    state = ServerState()
    assert state.planner_lm is None
    assert state.config is not None
    assert state.is_ready is False
    assert state.sessions == {}


def test_server_state_ready():
    state = ServerState()
    state.planner_lm = "mock_lm"
    assert state.is_ready is True


def test_get_config():
    cfg = get_config()
    assert cfg.secret_name == "LITELLM"


def test_get_planner_lm_default():
    assert get_planner_lm() is None


def test_session_key():
    assert session_key("workspace", "user") == "workspace:user"
