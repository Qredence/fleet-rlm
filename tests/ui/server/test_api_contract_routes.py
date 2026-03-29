from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from starlette.routing import WebSocketRoute


_REQUIRED_HTTP_PATHS = {
    "/api/v1/auth/me",
    "/api/v1/runtime/settings",
    "/api/v1/runtime/tests/modal",
    "/api/v1/runtime/tests/lm",
    "/api/v1/runtime/status",
    "/api/v1/sessions/state",
    "/api/v1/traces/feedback",
}

_REQUIRED_WS_PATHS = {
    "/api/v1/ws/chat",
    "/api/v1/ws/execution",
}


def test_required_http_and_websocket_routes_are_registered(
    local_client: TestClient,
) -> None:
    http_paths = {
        route.path for route in local_client.app.routes if isinstance(route, APIRoute)
    }
    ws_paths = {
        route.path
        for route in local_client.app.routes
        if isinstance(route, WebSocketRoute)
    }

    assert _REQUIRED_HTTP_PATHS.issubset(http_paths)
    assert _REQUIRED_WS_PATHS.issubset(ws_paths)


def test_health_endpoint_and_request_id_header(local_client: TestClient) -> None:
    response = local_client.get("/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert isinstance(payload["version"], str)
    assert payload["version"]
    assert "X-Request-ID" in response.headers


def test_ready_endpoint_reports_missing_planner(local_client: TestClient) -> None:
    local_client.app.state.server_state.planner_lm = None
    response = local_client.get("/ready")
    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {
        "ready",
        "planner_configured",
        "planner",
        "database",
        "database_required",
        "sandbox_provider",
    }
    assert payload["ready"] is False
    assert payload["planner_configured"] is False
    assert payload["planner"] == "missing"
    assert payload["database"] in {"disabled", "missing", "ready"}
    assert "sandbox_provider" in payload


def test_ws_router_split_modules_import() -> None:
    """Regression guard for ws module decomposition import stability."""
    import fleet_rlm.api.routers.ws as ws
    import fleet_rlm.api.routers.ws.artifacts as ws_artifacts
    import fleet_rlm.api.routers.ws.commands as ws_commands
    import fleet_rlm.api.routers.ws.completion as ws_completion
    import fleet_rlm.api.routers.ws.execution_support as ws_execution_support
    import fleet_rlm.api.routers.ws.endpoint as ws_endpoint
    import fleet_rlm.api.routers.ws.errors as ws_errors
    import fleet_rlm.api.routers.ws.failures as ws_failures
    import fleet_rlm.api.routers.ws.hitl as ws_hitl
    import fleet_rlm.api.routers.ws.lifecycle as ws_lifecycle
    import fleet_rlm.api.routers.ws.loop_exit as ws_loop_exit
    import fleet_rlm.api.routers.ws.manifest as ws_manifest
    import fleet_rlm.api.routers.ws.messages as ws_messages
    import fleet_rlm.api.routers.ws.persistence as ws_persistence
    import fleet_rlm.api.routers.ws.runtime as ws_runtime
    import fleet_rlm.api.routers.ws.session as ws_session
    import fleet_rlm.api.routers.ws.stream as ws_stream
    import fleet_rlm.api.routers.ws.task_control as ws_task_control
    import fleet_rlm.api.routers.ws.terminal as ws_terminal
    import fleet_rlm.api.routers.ws.turn_lifecycle as ws_turn_lifecycle
    import fleet_rlm.api.routers.ws.turn_setup as ws_turn_setup
    import fleet_rlm.api.routers.ws.types as ws_types

    assert ws.router is not None
    assert ws_endpoint.chat_streaming is not None
    assert ws_artifacts.is_artifact_tracking_command is not None
    assert ws_commands._handle_command is not None
    assert ws_completion.build_execution_completion_summary is not None
    assert ws_execution_support.get_execution_emitter is not None
    assert ws_errors.handle_stream_error is not None
    assert ws_failures.classify_stream_failure is not None
    assert ws_hitl.handle_resolve_hitl is not None
    assert ws_lifecycle.ExecutionLifecycleManager is not None
    assert ws_loop_exit.handle_chat_disconnect is not None
    assert ws_manifest._manifest_path is not None
    assert ws_messages.parse_ws_message_or_send_error is not None
    assert ws_persistence.persist_session_state is not None
    assert ws_runtime._prepare_chat_runtime is not None
    assert ws_session.switch_session_if_needed is not None
    assert ws_stream._chat_message_loop is not None
    assert ws_task_control.cancel_task is not None
    assert ws_terminal.handle_terminal_stream_event is not None
    assert ws_turn_lifecycle.initialize_turn_lifecycle is not None
    assert ws_turn_setup.prepare_chat_message_turn is not None
    assert ws_types.ChatAgentProtocol is not None


def test_ws_router_registers_expected_websocket_routes() -> None:
    import fleet_rlm.api.routers.ws as ws

    websocket_paths = {
        route.path for route in ws.router.routes if getattr(route, "path", None)
    }
    assert "/ws/chat" in websocket_paths
    assert "/ws/execution" in websocket_paths


def test_sessions_state_endpoint_exists_and_returns_expected_shape(
    default_client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    response = default_client.get("/api/v1/sessions/state", headers=auth_headers)
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert isinstance(payload["sessions"], list)


def test_runtime_contract_endpoints_remain_available(
    local_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Keep this contract test offline/deterministic and avoid touching live Modal
    # config files or credentials while checking route availability.
    monkeypatch.delenv("MODAL_TOKEN_ID", raising=False)
    monkeypatch.delenv("MODAL_TOKEN_SECRET", raising=False)
    monkeypatch.delenv("DSPY_LM_MODEL", raising=False)
    monkeypatch.delenv("DSPY_LLM_API_KEY", raising=False)
    monkeypatch.delenv("DSPY_LM_API_KEY", raising=False)
    monkeypatch.setattr("fleet_rlm.api.routers.runtime.load_modal_config", lambda: {})

    settings = local_client.get("/api/v1/runtime/settings")
    status = local_client.get("/api/v1/runtime/status")
    modal = local_client.post("/api/v1/runtime/tests/modal")
    lm = local_client.post("/api/v1/runtime/tests/lm")

    assert settings.status_code == 200
    assert status.status_code == 200
    assert modal.status_code == 200
    assert lm.status_code == 200


def test_openapi_excludes_legacy_one_shot_and_task_schemas(
    local_client: TestClient,
) -> None:
    schema_names = set(local_client.app.openapi()["components"]["schemas"])

    assert "ChatRequest" not in schema_names
    assert "ChatResponse" not in schema_names
    assert "TaskRequest" not in schema_names
    assert "TaskResponse" not in schema_names


def _patch_main_resolve(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from fleet_rlm.api import main as server_main

    def fake_resolve(self, *args, **kwargs):  # noqa: ARG001
        return Path(tmp_path / "repo" / "src" / "fleet_rlm" / "server" / "main.py")

    monkeypatch.setattr(server_main.Path, "resolve", fake_resolve)


def test_resolve_ui_dist_dir_prefers_frontend_when_both_exist(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from fleet_rlm.api import main as server_main

    _patch_main_resolve(monkeypatch, tmp_path)

    original_exists = server_main.Path.exists

    def fake_exists(self: Path) -> bool:
        path_str = str(self)
        if path_str.endswith("/ui/dist"):
            return True
        if path_str.endswith("/src/frontend/dist"):
            return True
        return original_exists(self)

    monkeypatch.setattr(server_main.Path, "exists", fake_exists)

    resolved = server_main._resolve_ui_dist_dir()

    assert resolved is not None
    assert str(resolved).endswith("/src/frontend/dist")


def test_resolve_ui_dist_dir_falls_back_to_packaged_dist(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from fleet_rlm.api import main as server_main

    _patch_main_resolve(monkeypatch, tmp_path)

    original_exists = server_main.Path.exists

    def fake_exists(self: Path) -> bool:
        path_str = str(self)
        if path_str.endswith("/src/frontend/dist"):
            return False
        if path_str.endswith("/ui/dist"):
            return True
        return original_exists(self)

    monkeypatch.setattr(server_main.Path, "exists", fake_exists)

    resolved = server_main._resolve_ui_dist_dir()

    assert resolved is not None
    assert str(resolved).endswith("/ui/dist")


def test_create_app_serves_spa_index_from_frontend_dist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fleet_rlm.api import main as server_main
    from fleet_rlm.api.config import ServerRuntimeConfig
    from fleet_rlm.api.main import create_app

    ui_dist = tmp_path / "src" / "frontend" / "dist"
    assets_dir = ui_dist / "assets"
    branding_dir = ui_dist / "branding"
    assets_dir.mkdir(parents=True)
    branding_dir.mkdir(parents=True)
    (ui_dist / "index.html").write_text("<html><body>Fleet UI</body></html>")
    (branding_dir / "logo-mark.svg").write_text("<svg>logo</svg>")

    monkeypatch.setattr(server_main, "_resolve_ui_dist_dir", lambda: ui_dist)

    app = create_app(
        config=ServerRuntimeConfig(
            app_env="local",
            database_required=False,
        )
    )
    client = TestClient(app)

    response = client.get("/")
    assert response.status_code == 200
    assert "Fleet UI" in response.text

    logo = client.get("/branding/logo-mark.svg")
    assert logo.status_code == 200
    assert logo.text == "<svg>logo</svg>"


def test_create_app_returns_helpful_root_response_when_ui_assets_are_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fleet_rlm.api import main as server_main
    from fleet_rlm.api.config import ServerRuntimeConfig
    from fleet_rlm.api.main import create_app

    monkeypatch.setattr(server_main, "_resolve_ui_dist_dir", lambda: None)

    app = create_app(
        config=ServerRuntimeConfig(
            app_env="local",
            database_required=False,
        )
    )
    client = TestClient(app)

    response = client.get("/")
    assert response.status_code == 503
    payload = response.json()
    assert payload["error"]
    assert payload["hint"]


def _fake_trace_feedback_trace(
    *,
    trace_id: str,
    client_request_id: str,
    user_id: str = "user-a",
    workspace_id: str = "tenant-a",
):
    info = {
        "trace_id": trace_id,
        "client_request_id": client_request_id,
        "trace_metadata": {
            "mlflow.trace.user": user_id,
            "fleet_rlm.workspace_id": workspace_id,
        },
    }
    return SimpleNamespace(
        info=SimpleNamespace(
            trace_id=trace_id,
            client_request_id=client_request_id,
            trace_metadata=info["trace_metadata"],
        ),
        to_dict=lambda: {"info": info},
    )


def test_trace_feedback_logs_feedback_by_trace_id(
    default_client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []
    fake_trace = _fake_trace_feedback_trace(
        trace_id="trace-1",
        client_request_id="req-1",
    )

    monkeypatch.setenv("MLFLOW_ENABLED", "true")
    monkeypatch.setattr(
        "fleet_rlm.api.routers.traces.resolve_trace",
        lambda **kwargs: fake_trace,
    )
    monkeypatch.setattr(
        "fleet_rlm.api.routers.traces.log_trace_feedback",
        lambda **kwargs: (
            calls.append(kwargs)
            or {"feedback_logged": True, "expectation_logged": True}
        ),
    )

    response = default_client.post(
        "/api/v1/traces/feedback",
        headers=auth_headers,
        json={
            "trace_id": "trace-1",
            "is_correct": True,
            "comment": "Looks good",
            "expected_response": "Expected answer",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["trace_id"] == "trace-1"
    assert payload["client_request_id"] == "req-1"
    assert payload["feedback_logged"] is True
    assert payload["expectation_logged"] is True
    assert calls[0]["source_id"] == "user-a"
    assert calls[0]["metadata"]["tenant_claim"] == "tenant-a"


def test_trace_feedback_resolves_by_client_request_id(
    default_client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, object]] = []
    fake_trace = _fake_trace_feedback_trace(
        trace_id="trace-2",
        client_request_id="req-2",
    )

    monkeypatch.setenv("MLFLOW_ENABLED", "true")
    monkeypatch.setattr(
        "fleet_rlm.api.routers.traces.resolve_trace",
        lambda **kwargs: captured.append(kwargs) or fake_trace,
    )
    monkeypatch.setattr(
        "fleet_rlm.api.routers.traces.log_trace_feedback",
        lambda **kwargs: {"feedback_logged": True, "expectation_logged": False},
    )

    response = default_client.post(
        "/api/v1/traces/feedback",
        headers=auth_headers,
        json={
            "client_request_id": "req-2",
            "is_correct": False,
        },
    )

    assert response.status_code == 200
    assert captured[0]["client_request_id"] == "req-2"
    assert response.json()["trace_id"] == "trace-2"


def test_trace_feedback_returns_403_for_other_users_trace(
    default_client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_trace = _fake_trace_feedback_trace(
        trace_id="trace-3",
        client_request_id="req-3",
        user_id="someone-else",
    )

    monkeypatch.setenv("MLFLOW_ENABLED", "true")
    monkeypatch.setattr(
        "fleet_rlm.api.routers.traces.resolve_trace",
        lambda **kwargs: fake_trace,
    )
    monkeypatch.setattr(
        "fleet_rlm.api.routers.traces.log_trace_feedback",
        lambda **kwargs: pytest.fail(
            "feedback logging should not run for another user's trace"
        ),
    )

    response = default_client.post(
        "/api/v1/traces/feedback",
        headers=auth_headers,
        json={
            "trace_id": "trace-3",
            "is_correct": True,
        },
    )

    assert response.status_code == 403
    assert "not allowed" in response.json()["detail"]


def test_trace_feedback_returns_503_when_mlflow_disabled(
    default_client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MLFLOW_ENABLED", "false")

    response = default_client.post(
        "/api/v1/traces/feedback",
        headers=auth_headers,
        json={
            "trace_id": "trace-disabled",
            "is_correct": True,
        },
    )

    assert response.status_code == 503
    assert "MLFLOW_ENABLED=false" in response.json()["detail"]


def test_trace_feedback_returns_404_when_trace_missing(
    default_client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MLFLOW_ENABLED", "true")
    monkeypatch.setattr(
        "fleet_rlm.api.routers.traces.resolve_trace",
        lambda **kwargs: None,
    )

    response = default_client.post(
        "/api/v1/traces/feedback",
        headers=auth_headers,
        json={
            "client_request_id": "missing",
            "is_correct": True,
        },
    )

    assert response.status_code == 404


def test_trace_feedback_returns_503_when_lookup_raises(
    default_client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MLFLOW_ENABLED", "true")
    monkeypatch.setattr(
        "fleet_rlm.api.routers.traces.resolve_trace",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("mlflow down")),
    )

    response = default_client.post(
        "/api/v1/traces/feedback",
        headers=auth_headers,
        json={
            "trace_id": "trace-err",
            "is_correct": True,
        },
    )

    assert response.status_code == 503
    assert "Failed to resolve MLflow trace" in response.json()["detail"]
