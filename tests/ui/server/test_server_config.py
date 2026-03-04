import pytest
from fastapi import HTTPException

from importlib.metadata import version
from types import SimpleNamespace

from fleet_rlm.server.config import ServerRuntimeConfig
from fleet_rlm.server.deps import ServerState, get_server_state, session_key
from fleet_rlm.server.schemas import (
    AuthLoginResponse,
    AuthLogoutResponse,
    ChatRequest,
    ChatResponse,
    HealthResponse,
    RuntimeActiveModels,
    RuntimeStatusResponse,
    RuntimeTestCache,
    TaskRequest,
    WSMessage,
)


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


def test_default_config_uses_volume_name_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("VOLUME_NAME", "alt-volume")
    cfg = ServerRuntimeConfig()
    assert cfg.volume_name == "alt-volume"


def test_default_config_uses_agent_model_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DSPY_LM_MODEL", "openai/gpt-4.1-mini")
    cfg = ServerRuntimeConfig()
    assert cfg.agent_model == "openai/gpt-4.1-mini"


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


def test_get_server_state_missing_raises_http_503() -> None:
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    with pytest.raises(HTTPException) as exc:
        get_server_state(request)
    assert exc.value.status_code == 503


def test_session_key() -> None:
    assert session_key("workspace", "user") == "workspace:user:__default__"
    assert session_key("workspace", "user", "session-1") == "workspace:user:session-1"


def test_chat_request_defaults() -> None:
    req = ChatRequest(message="hello")
    assert req.docs_path is None
    assert req.trace is False


def test_chat_response() -> None:
    response = ChatResponse(assistant_response="hi")
    assert response.history_turns == 0
    assert response.trajectory is None


def test_health_response() -> None:
    response = HealthResponse()
    assert response.ok is True
    assert response.version == version("fleet-rlm")


def test_task_request_defaults() -> None:
    req = TaskRequest(task_type="basic", question="test")
    assert req.max_iterations == 15
    assert req.timeout == 600


def test_auth_response_defaults() -> None:
    login = AuthLoginResponse(token="dummy")
    logout = AuthLogoutResponse()
    assert login.token == "dummy"
    assert logout.status == "ok"


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
    assert msg.workspace_id == "default"
    assert msg.user_id == "anonymous"
    assert msg.session_id is None
