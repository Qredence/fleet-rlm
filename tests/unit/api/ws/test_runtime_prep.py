from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from fleet_rlm.api.routers.ws.endpoint import _prepare_chat_runtime
from fleet_rlm.api.runtime_services.chat_runtime import (
    PreparedChatRuntime as _PreparedChatRuntime,
)
from fleet_rlm.api.runtime_services.chat_runtime import (
    build_chat_agent_context as _build_chat_agent_context,
)
from fleet_rlm.api.runtime_services.chat_runtime import (
    new_chat_session_state as _new_chat_session_state,
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
        rlm_child_isolation_mode="auto",
        rlm_child_fork_fallback="clean",
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
    async def fake_ensure_runtime_models(
        lm_deps: object, config_deps: object, diagnostics_deps: object
    ) -> tuple[str, str]:
        return ("planner-lm", "delegate-lm")

    fake_cfg = _runtime_cfg()
    identity_rows = SimpleNamespace(tenant_id="tenant-row")
    fake_repo = _FakeRepository(identity_rows)
    config_deps = SimpleNamespace(config=fake_cfg)
    lm_deps = SimpleNamespace()
    persistence_deps = SimpleNamespace(repository=fake_repo)
    diagnostics_deps = SimpleNamespace()
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
            config_deps=config_deps,
            lm_deps=lm_deps,
            persistence_deps=persistence_deps,
            diagnostics_deps=diagnostics_deps,
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
    async def failing_ensure_runtime_models(
        lm_deps: object, config_deps: object, diagnostics_deps: object
    ) -> tuple[object, object]:
        raise RuntimeError("planner boom")

    fake_cfg = _runtime_cfg()
    config_deps = SimpleNamespace(config=fake_cfg)
    lm_deps = SimpleNamespace()
    persistence_deps = SimpleNamespace(repository=None)
    diagnostics_deps = SimpleNamespace()
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
            config_deps=config_deps,
            lm_deps=lm_deps,
            persistence_deps=persistence_deps,
            diagnostics_deps=diagnostics_deps,
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


def test_build_chat_agent_context_uses_canonical_builder(monkeypatch) -> None:
    daytona_agent = object()
    calls: list[dict[str, Any]] = []

    def _fake_builder(**kwargs: Any) -> object:
        calls.append(kwargs)
        return daytona_agent

    monkeypatch.setattr(
        "fleet_rlm.api.runtime_services.chat_runtime.build_chat_agent",
        _fake_builder,
    )

    async def _fake_acquire(_self: object, _cfg: object) -> None:
        return None

    monkeypatch.setattr(
        "fleet_rlm.api.runtime_services.interpreter_pool.InterpreterPool.acquire",
        _fake_acquire,
    )

    runtime = _PreparedChatRuntime(
        cfg=_runtime_cfg(),
        planner_lm="planner-lm",
        delegate_lm="delegate-lm",
        repository=None,
        persistence=None,
        persistence_required=False,
        identity_rows=None,
    )

    result = asyncio.run(_build_chat_agent_context(runtime))
    assert result is daytona_agent
    assert calls == [
        {
            "react_max_iters": 5,
            "deep_react_max_iters": 9,
            "enable_adaptive_iters": True,
            "rlm_max_iterations": 11,
            "rlm_max_llm_calls": 17,
            "max_depth": 4,
            "rlm_child_isolation_mode": "auto",
            "rlm_child_fork_fallback": "clean",
            "timeout": 123,
            "secret_name": "secret",
            "volume_name": "volume",
            "interpreter_async_execute": True,
            "guardrail_mode": "warn",
            "max_output_chars": 1200,
            "min_substantive_chars": 40,
            "planner_lm": "planner-lm",
            "delegate_lm": "delegate-lm",
            "delegate_max_calls_per_turn": 3,
            "delegate_result_truncation_chars": 500,
            "repository": None,
        }
    ]


def test_new_chat_session_state_uses_identity_or_defaults() -> None:
    runtime = _PreparedChatRuntime(
        cfg=_runtime_cfg(),
        planner_lm="planner-lm",
        delegate_lm=None,
        repository=None,
        persistence=None,
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


def test_daytona_builder_returns_none_on_import_error(monkeypatch) -> None:
    """When Daytona import fails, InterpreterPool.acquire returns None without UnboundLocalError."""
    from fleet_rlm.api.runtime_services.interpreter_pool import InterpreterPool

    async def _fail_acquire(_self: object, _cfg: object) -> None:
        return None

    monkeypatch.setattr(
        "fleet_rlm.api.runtime_services.interpreter_pool.InterpreterPool.acquire",
        _fail_acquire,
    )

    result = asyncio.run(InterpreterPool().acquire(_runtime_cfg()))
    assert result is None
