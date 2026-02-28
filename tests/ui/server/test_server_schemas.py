from importlib.metadata import version

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


def test_chat_request_defaults():
    req = ChatRequest(message="hello")
    assert req.docs_path is None
    assert req.trace is False


def test_chat_response():
    r = ChatResponse(assistant_response="hi")
    assert r.history_turns == 0
    assert r.trajectory is None


def test_health_response():
    r = HealthResponse()
    assert r.ok is True
    assert r.version == version("fleet-rlm")


def test_task_request_defaults():
    req = TaskRequest(task_type="basic", question="test")
    assert req.max_iterations == 15
    assert req.timeout == 600


def test_auth_response_defaults():
    login = AuthLoginResponse(token="dummy")
    logout = AuthLogoutResponse()
    assert login.token == "dummy"
    assert logout.status == "ok"


def test_runtime_status_shape():
    status = RuntimeStatusResponse(
        app_env="local",
        write_enabled=True,
        ready=False,
        active_models=RuntimeActiveModels(planner="openai/gpt-4o-mini"),
        tests=RuntimeTestCache(),
    )
    assert status.active_models.planner == "openai/gpt-4o-mini"
    assert status.active_models.delegate == ""


def test_ws_message_defaults():
    msg = WSMessage()
    assert msg.type == "message"
    assert msg.content == ""
    assert msg.workspace_id == "default"
    assert msg.user_id == "anonymous"
    assert msg.session_id is None
