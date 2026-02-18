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
    assert cfg.auth_mode == "dev"
    assert cfg.db_validate_on_startup is False


def test_custom_config():
    cfg = ServerRuntimeConfig(
        secret_name="CUSTOM",
        timeout=60,
        volume_name="vol",
        ws_default_workspace_id="team-a",
        ws_default_user_id="alice",
        auth_mode="entra",
        dev_jwt_secret="secret",
        database_url="postgresql://localhost:5432/test",
        db_validate_on_startup=True,
    )
    assert cfg.secret_name == "CUSTOM"
    assert cfg.timeout == 60
    assert cfg.volume_name == "vol"
    assert cfg.rlm_max_iterations == 30
    assert cfg.rlm_max_llm_calls == 50
    assert cfg.ws_default_workspace_id == "team-a"
    assert cfg.ws_default_user_id == "alice"
    assert cfg.auth_mode == "entra"
    assert cfg.dev_jwt_secret == "secret"
    assert cfg.database_url == "postgresql://localhost:5432/test"
    assert cfg.db_validate_on_startup is True
