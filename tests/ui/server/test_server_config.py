from fleet_rlm.server.config import ServerRuntimeConfig
import pytest


def test_default_config(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("VOLUME_NAME", raising=False)
    monkeypatch.delenv("DSPY_DELEGATE_LM_MODEL", raising=False)
    monkeypatch.delenv("DSPY_DELEGATE_LM_MAX_TOKENS", raising=False)
    cfg = ServerRuntimeConfig()
    assert cfg.app_env == "local"
    assert cfg.secret_name == "LITELLM"
    assert cfg.timeout == 900
    assert cfg.react_max_iters == 15
    assert cfg.deep_react_max_iters == 35
    assert cfg.enable_adaptive_iters is True
    assert cfg.delegate_max_calls_per_turn == 8
    assert cfg.delegate_result_truncation_chars == 8000
    assert cfg.agent_delegate_model is None
    assert cfg.agent_delegate_max_tokens == 64000
    assert cfg.rlm_max_depth == 2
    assert cfg.volume_name is None
    assert cfg.ws_default_workspace_id == "default"
    assert cfg.ws_default_user_id == "anonymous"
    assert cfg.ws_default_execution_profile == "ROOT_INTERLOCUTOR"
    assert cfg.auth_mode == "dev"
    assert cfg.database_required is False
    assert cfg.db_validate_on_startup is False
    assert cfg.enable_legacy_sqlite_routes is True
    assert cfg.allow_debug_auth is True
    assert cfg.allow_query_auth_tokens is True
    assert cfg.cors_allowed_origins == ["*"]
    assert cfg.ws_execution_max_queue == 256
    assert cfg.ws_execution_drop_policy == "drop_oldest"


def test_default_config_uses_volume_name_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("VOLUME_NAME", "alt-volume")
    cfg = ServerRuntimeConfig()
    assert cfg.volume_name == "alt-volume"


def test_custom_config():
    cfg = ServerRuntimeConfig(
        app_env="production",
        secret_name="CUSTOM",
        timeout=60,
        volume_name="vol",
        ws_default_workspace_id="team-a",
        ws_default_user_id="alice",
        auth_mode="entra",
        auth_required=True,
        allow_debug_auth=False,
        allow_query_auth_tokens=False,
        cors_allowed_origins=["https://app.example.com"],
        dev_jwt_secret="secret",
        database_url="postgresql://localhost:5432/test",
        database_required=True,
        enable_legacy_sqlite_routes=False,
        ws_execution_max_queue=512,
        ws_execution_drop_policy="drop_newest",
        db_validate_on_startup=True,
    )
    assert cfg.app_env == "production"
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
    assert cfg.database_required is True
    assert cfg.enable_legacy_sqlite_routes is False
    assert cfg.allow_debug_auth is False
    assert cfg.allow_query_auth_tokens is False
    assert cfg.cors_allowed_origins == ["https://app.example.com"]
    assert cfg.ws_execution_max_queue == 512
    assert cfg.ws_execution_drop_policy == "drop_newest"
    assert cfg.db_validate_on_startup is True


def test_validate_startup_rejects_insecure_production():
    cfg = ServerRuntimeConfig(
        app_env="production",
        auth_required=False,
        database_url="postgresql://localhost:5432/test",
        database_required=True,
        allow_debug_auth=False,
        allow_query_auth_tokens=False,
        cors_allowed_origins=["https://app.example.com"],
    )
    with pytest.raises(ValueError, match="AUTH_REQUIRED"):
        cfg.validate_startup_or_raise()
