from fleet_rlm.server.config import ServerRuntimeConfig


def test_default_config():
    cfg = ServerRuntimeConfig()
    assert cfg.secret_name == "LITELLM"
    assert cfg.timeout == 900
    assert cfg.react_max_iters == 5
    assert cfg.rlm_max_depth == 2
    assert cfg.volume_name is None
    assert cfg.ws_default_workspace_id == "default"
    assert cfg.ws_default_user_id == "anonymous"
    assert cfg.ws_default_execution_profile == "ROOT_INTERLOCUTOR"


def test_custom_config():
    cfg = ServerRuntimeConfig(
        secret_name="CUSTOM",
        timeout=60,
        volume_name="vol",
        ws_default_workspace_id="team-a",
        ws_default_user_id="alice",
    )
    assert cfg.secret_name == "CUSTOM"
    assert cfg.timeout == 60
    assert cfg.volume_name == "vol"
    assert cfg.rlm_max_iterations == 30
    assert cfg.rlm_max_llm_calls == 50
    assert cfg.ws_default_workspace_id == "team-a"
    assert cfg.ws_default_user_id == "alice"
