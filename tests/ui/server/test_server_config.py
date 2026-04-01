import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from importlib.metadata import version
from types import SimpleNamespace

from fleet_rlm.api.config import ServerRuntimeConfig, resolve_server_volume_name
from fleet_rlm.api.dependencies import ServerState, get_server_state, session_key
from fleet_rlm.api.server_utils import owner_fingerprint, sanitize_id
from fleet_rlm.api.schemas import (
    AuthMeResponse,
    HealthResponse,
    RuntimeActiveModels,
    RuntimeStatusResponse,
    RuntimeTestCache,
    WSMessage,
)
from fleet_rlm.integrations.config.env import AppConfig


def test_default_config(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("VOLUME_NAME", raising=False)
    monkeypatch.delenv("DSPY_LM_MODEL", raising=False)
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
    assert cfg.agent_model is None
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
    assert cfg.allow_debug_auth is True
    assert cfg.allow_query_auth_tokens is True
    assert cfg.cors_allowed_origins == ["*"]
    assert cfg.ws_execution_max_queue == 256
    assert cfg.ws_execution_drop_policy == "drop_oldest"
    assert (
        cfg.entra_issuer_template == "https://login.microsoftonline.com/{tenantid}/v2.0"
    )


def test_default_config_uses_volume_name_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("VOLUME_NAME", "alt-volume")
    cfg = ServerRuntimeConfig()
    assert cfg.volume_name == "alt-volume"


def test_default_config_uses_agent_model_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DSPY_LM_MODEL", "openai/gpt-4.1-mini")
    cfg = ServerRuntimeConfig()
    assert cfg.agent_model == "openai/gpt-4.1-mini"


def test_resolve_server_volume_name_defaults_to_persistent_volume() -> None:
    config = AppConfig()
    assert resolve_server_volume_name(config) == "rlm-volume-dspy"


def test_server_runtime_config_from_app_config_maps_shared_settings() -> None:
    config = AppConfig(
        interpreter={
            "secrets": ["ALT_SECRET"],
            "timeout": 321,
            "async_execute": False,
        },
        agent={
            "model": "openai/gpt-4.1-mini",
            "delegate_model": "openai/gpt-4.1-nano",
            "delegate_max_tokens": 2048,
            "rlm_max_iterations": 17,
            "guardrail_mode": "warn",
            "min_substantive_chars": 55,
        },
        rlm_settings={
            "max_iters": 12,
            "deep_max_iters": 21,
            "enable_adaptive_iters": False,
            "max_llm_calls": 88,
            "max_depth": 4,
            "delegate_max_calls_per_turn": 3,
            "delegate_result_truncation_chars": 987,
            "max_output_chars": 4321,
        },
    )

    cfg = ServerRuntimeConfig.from_app_config(config)

    assert cfg.secret_name == "ALT_SECRET"
    assert cfg.volume_name == "rlm-volume-dspy"
    assert cfg.timeout == 321
    assert cfg.react_max_iters == 12
    assert cfg.deep_react_max_iters == 21
    assert cfg.enable_adaptive_iters is False
    assert cfg.rlm_max_iterations == 17
    assert cfg.rlm_max_llm_calls == 88
    assert cfg.rlm_max_depth == 4
    assert cfg.delegate_max_calls_per_turn == 3
    assert cfg.delegate_result_truncation_chars == 987
    assert cfg.interpreter_async_execute is False
    assert cfg.agent_guardrail_mode == "warn"
    assert cfg.agent_min_substantive_chars == 55
    assert cfg.agent_max_output_chars == 4321
    assert cfg.agent_model == "openai/gpt-4.1-mini"
    assert cfg.agent_delegate_model == "openai/gpt-4.1-nano"
    assert cfg.agent_delegate_max_tokens == 2048
    assert cfg.db_validate_on_startup is True


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
        allow_query_auth_tokens=True,
        cors_allowed_origins=["https://app.example.com"],
        dev_jwt_secret="secret",
        database_url="postgresql://localhost:5432/test",
        database_required=True,
        ws_execution_max_queue=512,
        ws_execution_drop_policy="drop_newest",
        db_validate_on_startup=True,
        entra_jwks_url="https://login.microsoftonline.com/tenant/discovery/v2.0/keys",
        entra_issuer_template="https://login.microsoftonline.com/{tenantid}/v2.0",
        entra_audience="api://fleet-rlm",
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
    assert cfg.allow_debug_auth is False
    assert cfg.allow_query_auth_tokens is True
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


def test_validate_startup_requires_entra_settings() -> None:
    cfg = ServerRuntimeConfig(
        auth_mode="entra",
        auth_required=True,
        database_required=True,
        database_url="postgresql://localhost:5432/test",
    )
    with pytest.raises(ValueError, match="ENTRA_JWKS_URL"):
        cfg.validate_startup_or_raise()


def test_validate_startup_requires_database_for_entra() -> None:
    cfg = ServerRuntimeConfig(auth_mode="entra", auth_required=True)
    with pytest.raises(ValueError, match="DATABASE_REQUIRED"):
        cfg.validate_startup_or_raise()


def test_validate_startup_rejects_fixed_entra_issuer_template() -> None:
    cfg = ServerRuntimeConfig(
        auth_mode="entra",
        auth_required=True,
        database_required=True,
        database_url="postgresql://localhost:5432/test",
        entra_jwks_url="https://login.microsoftonline.com/common/discovery/v2.0/keys",
        entra_audience="api://fleet-rlm",
        entra_issuer_template="https://login.microsoftonline.com/static/v2.0",
    )
    with pytest.raises(ValueError, match="tenantid"):
        cfg.validate_startup_or_raise()


def test_server_state_init() -> None:
    state = ServerState()
    assert state.planner_lm is None
    assert state.config is not None
    assert state.is_ready is False
    assert state.sessions == {}
    assert state.repository is None
    assert state.auth_provider is None


def test_server_state_ready() -> None:
    state = ServerState()
    state.config.database_required = False
    state.planner_lm = "mock_lm"
    assert state.is_ready is True


def test_server_state_ready_from_optional_planner_status() -> None:
    state = ServerState()
    state.config.database_required = False
    state.optional_service_status["planner_lm"] = "ready"
    assert state.is_ready is True


def test_get_server_state_missing_raises_http_503() -> None:
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    with pytest.raises(HTTPException) as exc:
        get_server_state(request)
    assert exc.value.status_code == 503


def test_session_key() -> None:
    owner_id = owner_fingerprint("workspace", "user")
    assert session_key("workspace", "user") == f"owner:{owner_id}:__default__"
    assert session_key("workspace", "user", "session-1") == (
        f"owner:{owner_id}:session-1"
    )


def test_sanitize_id_rejects_dot_only_segments() -> None:
    assert sanitize_id(".", "default") == "default"
    assert sanitize_id("...", "default") == "default"
    assert sanitize_id("---", "default") == "default"


def test_sanitize_id_strips_boundary_dots() -> None:
    assert sanitize_id(".workspace.", "default") == "workspace"


def test_health_response() -> None:
    response = HealthResponse()
    assert response.ok is True
    assert response.version == version("fleet-rlm")


def test_auth_me_response_shape() -> None:
    me = AuthMeResponse(
        tenant_claim="tenant-1",
        user_claim="user-1",
        email="alice@example.com",
        name="Alice",
    )
    assert me.tenant_claim == "tenant-1"
    assert me.user_claim == "user-1"


def test_runtime_status_shape() -> None:
    status = RuntimeStatusResponse(
        app_env="local",
        write_enabled=True,
        ready=False,
        active_models=RuntimeActiveModels(planner="openai/gpt-4o-mini"),
        tests=RuntimeTestCache(),
    )
    assert status.active_models.planner == "openai/gpt-4o-mini"
    assert status.active_models.delegate == ""


def test_ws_message_defaults() -> None:
    msg = WSMessage()
    assert msg.type == "message"
    assert msg.content == ""
    assert msg.session_id is None


def test_ws_message_rejects_legacy_identity_fields() -> None:
    with pytest.raises(ValidationError) as exc_info:
        WSMessage(
            workspace_id="legacy-workspace",
            user_id="legacy-user",
        )

    assert "unsupported_identity_fields" in str(exc_info.value)
