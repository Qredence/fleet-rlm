from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from fleet_rlm.api.routers.ws.runtime import (
    _PreparedChatRuntime,
    _build_chat_agent_context,
    _new_chat_session_state,
    _prepare_chat_runtime,
)


class _RecordingWebSocket:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.closed_code: int | None = None

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.sent.append(payload)

    async def close(self, code: int = 1000) -> None:
        self.closed_code = code


class _FakeRepository:
    def __init__(self, identity_rows: object) -> None:
        self.identity_rows = identity_rows
        self.calls: list[dict[str, Any]] = []

    async def upsert_identity(self, **kwargs: Any) -> object:
        self.calls.append(kwargs)
        return self.identity_rows


def _runtime_cfg() -> SimpleNamespace:
    return SimpleNamespace(
        auth_mode="dev",
        database_required=False,
        timeout=123,
        rlm_max_depth=4,
        interpreter_async_execute=True,
        react_max_iters=5,
        deep_react_max_iters=9,
        enable_adaptive_iters=True,
        rlm_max_iterations=11,
        rlm_max_llm_calls=17,
        secret_name="secret",
        volume_name="volume",
        agent_guardrail_mode="warn",
        agent_max_output_chars=1200,
        agent_min_substantive_chars=40,
        delegate_max_calls_per_turn=3,
        delegate_result_truncation_chars=500,
        ws_default_workspace_id="default-workspace",
        ws_default_user_id="default-user",
        ws_default_execution_profile="root_interlocutor",
    )


def test_prepare_chat_runtime_returns_prepared_runtime(monkeypatch) -> None:
    async def fake_ensure_runtime_models(state: object) -> tuple[str, str]:
        assert state is fake_state
        return ("planner-lm", "delegate-lm")

    fake_cfg = _runtime_cfg()
    identity_rows = SimpleNamespace(tenant_id="tenant-row")
    fake_repo = _FakeRepository(identity_rows)
    fake_state = SimpleNamespace(config=fake_cfg, repository=fake_repo)
    fake_identity = SimpleNamespace(
        tenant_claim="tenant-123",
        user_claim="user-456",
        email="user@example.com",
        name="User Example",
    )
    websocket = _RecordingWebSocket()

    monkeypatch.setattr(
        "fleet_rlm.api.bootstrap.ensure_runtime_models",
        fake_ensure_runtime_models,
    )

    runtime = asyncio.run(
        _prepare_chat_runtime(
            websocket=websocket,
            state=fake_state,
            identity=fake_identity,
        )
    )

    assert runtime is not None
    assert runtime.cfg is fake_cfg
    assert runtime.planner_lm == "planner-lm"
    assert runtime.delegate_lm == "delegate-lm"
    assert runtime.repository is fake_repo
    assert runtime.persistence_required is False
    assert runtime.identity_rows is identity_rows
    assert fake_repo.calls == [
        {
            "entra_tenant_id": "tenant-123",
            "entra_user_id": "user-456",
            "email": "user@example.com",
            "full_name": "User Example",
        }
    ]
    assert websocket.sent == []
    assert websocket.closed_code is None


def test_prepare_chat_runtime_reports_planner_initialization_failure(
    monkeypatch,
) -> None:
    async def failing_ensure_runtime_models(state: object) -> tuple[object, object]:
        _ = state
        raise RuntimeError("planner boom")

    fake_state = SimpleNamespace(config=_runtime_cfg(), repository=None)
    fake_identity = SimpleNamespace(
        tenant_claim="tenant-123",
        user_claim="user-456",
        email="user@example.com",
        name="User Example",
    )
    websocket = _RecordingWebSocket()

    monkeypatch.setattr(
        "fleet_rlm.api.bootstrap.ensure_runtime_models",
        failing_ensure_runtime_models,
    )

    runtime = asyncio.run(
        _prepare_chat_runtime(
            websocket=websocket,
            state=fake_state,
            identity=fake_identity,
        )
    )

    assert runtime is None
    assert websocket.closed_code == 1011
    assert websocket.sent == [
        {
            "type": "error",
            "code": "planner_initialization_failed",
            "message": "Planner initialization failed: planner boom",
        }
    ]


def test_build_chat_agent_context_uses_runtime_mode_builder(monkeypatch) -> None:
    react_agent = object()
    daytona_agent = object()
    calls: list[dict[str, Any]] = []

    def _fake_builder(**kwargs: Any) -> object:
        calls.append(kwargs)
        return react_agent if kwargs["runtime_mode"] == "modal_chat" else daytona_agent

    monkeypatch.setattr(
        "fleet_rlm.cli.runners.build_chat_agent_for_runtime_mode",
        _fake_builder,
    )

    runtime = _PreparedChatRuntime(
        cfg=_runtime_cfg(),
        planner_lm="planner-lm",
        delegate_lm="delegate-lm",
        repository=None,
        persistence_required=False,
        identity_rows=None,
    )

    assert _build_chat_agent_context(runtime, runtime_mode="modal_chat") is react_agent
    assert (
        _build_chat_agent_context(runtime, runtime_mode="daytona_pilot")
        is daytona_agent
    )
    assert [call["runtime_mode"] for call in calls] == [
        "modal_chat",
        "daytona_pilot",
    ]
    assert all(call["timeout"] == 123 for call in calls)
    assert all(call["max_depth"] == 4 for call in calls)
    assert all(call["secret_name"] == "secret" for call in calls)
    assert all(call["volume_name"] == "volume" for call in calls)
    assert all(call["guardrail_mode"] == "warn" for call in calls)
    assert all(call["planner_lm"] == "planner-lm" for call in calls)
    assert all(call["delegate_lm"] == "delegate-lm" for call in calls)


def test_new_chat_session_state_uses_identity_or_defaults() -> None:
    runtime = _PreparedChatRuntime(
        cfg=_runtime_cfg(),
        planner_lm="planner-lm",
        delegate_lm=None,
        repository=None,
        persistence_required=False,
        identity_rows=None,
    )

    session = _new_chat_session_state(
        runtime,
        SimpleNamespace(
            tenant_claim="workspace-123",
            user_claim="user-456",
        ),
    )
    assert session.canonical_workspace_id == "workspace-123"
    assert session.canonical_user_id == "user-456"
    assert session.cancel_flag == {"cancelled": False}

    fallback_session = _new_chat_session_state(
        runtime,
        SimpleNamespace(
            tenant_claim="",
            user_claim="",
        ),
    )
    assert fallback_session.canonical_workspace_id == "default-workspace"
    assert fallback_session.canonical_user_id == "default-user"
