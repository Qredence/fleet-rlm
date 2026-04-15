from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch
import uuid

from fleet_rlm.api.runtime_services.chat_runtime import (
    ChatSessionState as _ChatSessionState,
)
from fleet_rlm.api.routers.ws.turn_setup import prepare_chat_message_turn
from fleet_rlm.api.routers.ws.worker_request import build_workspace_task_request
from fleet_rlm.api.schemas import WSMessage
from tests.ui.fixtures_ui import FakeChatAgent


class _RecordingWebSocket:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.sent.append(payload)


def test_prepare_chat_message_turn_rejects_empty_content() -> None:
    async def scenario() -> None:
        websocket = _RecordingWebSocket()
        session = _ChatSessionState(
            canonical_workspace_id="workspace",
            canonical_user_id="user",
            owner_tenant_claim="workspace",
            owner_user_claim="user",
            cancel_flag={"cancelled": False},
            last_loaded_docs_path="docs/last.md",
        )
        persist_calls: list[dict[str, Any]] = []

        async def local_persist(**kwargs: Any) -> None:
            persist_calls.append(kwargs)

        prepared = await prepare_chat_message_turn(
            websocket=cast(Any, websocket),
            msg=WSMessage(type="message", content="   "),
            agent=cast(Any, FakeChatAgent()),
            session=session,
            local_persist=cast(Any, local_persist),
            runtime=cast(
                Any,
                SimpleNamespace(
                    planner_lm=object(),
                    cfg=SimpleNamespace(app_env="test"),
                    repository=None,
                    identity_rows=None,
                    persistence_required=False,
                ),
            ),
            workspace_id="workspace",
            user_id="user",
            sess_id="session",
            execution_emitter=cast(Any, object()),
        )

        assert prepared is None
        assert persist_calls == []
        assert websocket.sent == [
            {"type": "error", "message": "Message content cannot be empty"}
        ]

    asyncio.run(scenario())


def test_prepare_chat_message_turn_initializes_daytona_turn(monkeypatch) -> None:
    async def scenario() -> None:
        websocket = _RecordingWebSocket()
        agent = FakeChatAgent()
        session = _ChatSessionState(
            canonical_workspace_id="workspace",
            canonical_user_id="user",
            owner_tenant_claim="workspace",
            owner_user_claim="user",
            cancel_flag={"cancelled": True},
            session_record={"id": "session-record"},
            last_loaded_docs_path="docs/last.md",
        )
        persist_calls: list[dict[str, Any]] = []
        lifecycle = SimpleNamespace()
        step_builder = object()
        active_run_db_id = uuid.uuid4()
        init_calls: list[dict[str, Any]] = []
        workspace_config_calls: list[dict[str, Any]] = []

        async def fake_initialize_turn_lifecycle(**kwargs: Any):
            init_calls.append(kwargs)
            return lifecycle, step_builder, "run-123", active_run_db_id

        monkeypatch.setattr(
            "fleet_rlm.api.routers.ws.turn_setup.initialize_turn_lifecycle",
            fake_initialize_turn_lifecycle,
        )

        async def fake_aconfigure_workspace(**kwargs: Any) -> None:
            workspace_config_calls.append(kwargs)

        agent.interpreter.aconfigure_workspace = fake_aconfigure_workspace  # type: ignore[attr-defined]

        async def local_persist(**kwargs: Any) -> None:
            persist_calls.append(kwargs)

        prepared = await prepare_chat_message_turn(
            websocket=cast(Any, websocket),
            msg=WSMessage(
                type="message",
                content=" hello ",
                docs_path="docs/current.md",
                trace=False,
                execution_mode="tools_only",
                runtime_mode="daytona_pilot",
                repo_url="https://github.com/example/repo.git",
                repo_ref="main",
                context_paths=["src", " ", "docs"],
                batch_concurrency=4,
            ),
            agent=cast(Any, agent),
            session=session,
            local_persist=cast(Any, local_persist),
            runtime=cast(
                Any,
                SimpleNamespace(
                    planner_lm=object(),
                    cfg=SimpleNamespace(app_env="test"),
                    repository=None,
                    identity_rows=None,
                    persistence_required=False,
                ),
            ),
            workspace_id="workspace",
            user_id="user",
            sess_id="session",
            execution_emitter=cast(Any, object()),
        )

        assert prepared is not None
        # Turn setup should carry the requested mode without eagerly mutating the agent.
        assert agent.execution_mode == "auto"
        assert persist_calls == [
            {"include_volume_save": True, "latest_user_message": "hello"}
        ]
        assert session.cancel_flag["cancelled"] is False
        assert session.lifecycle is lifecycle
        assert session.active_run_db_id == active_run_db_id
        assert prepared.message == "hello"
        assert prepared.docs_path == "docs/current.md"
        assert prepared.trace is False
        assert prepared.execution_mode == "tools_only"
        assert prepared.workspace_id == "workspace"
        assert prepared.repo_url == "https://github.com/example/repo.git"
        assert prepared.repo_ref == "main"
        assert prepared.context_paths == ["src", "docs"]
        assert prepared.batch_concurrency == 4
        assert prepared.last_loaded_docs_path == "docs/last.md"
        assert prepared.analytics_enabled is None
        assert prepared.prepare_worker is not None
        await prepared.prepare_worker()
        assert workspace_config_calls == [
            {
                "repo_url": "https://github.com/example/repo.git",
                "repo_ref": "main",
                "context_paths": ["src", "docs", "docs/current.md"],
                "volume_name": "workspace",
            }
        ]
        assert getattr(agent, "batch_concurrency") == 4
        assert prepared.mlflow_trace_context is not None
        assert prepared.mlflow_trace_context.session_id == "workspace:user:session"
        assert init_calls and init_calls[0]["sandbox_provider"] == "daytona"
        assert init_calls[0]["turn_index"] == 1

    asyncio.run(scenario())


def test_build_workspace_task_request_uses_prepared_turn_inputs() -> None:
    async def fake_initialize_turn_lifecycle(**_: Any):
        return SimpleNamespace(), object(), "run-123", uuid.uuid4()

    async def scenario() -> None:
        websocket = _RecordingWebSocket()
        agent = FakeChatAgent()
        session = _ChatSessionState(
            canonical_workspace_id="workspace",
            canonical_user_id="user",
            owner_tenant_claim="workspace",
            owner_user_claim="user",
            cancel_flag={"cancelled": False},
            session_record={"id": "session-record"},
            last_loaded_docs_path=None,
        )

        async def local_persist(**kwargs: Any) -> None:
            _ = kwargs

        prepared = await prepare_chat_message_turn(
            websocket=cast(Any, websocket),
            msg=WSMessage(
                type="message",
                content=" hello ",
                execution_mode="tools_only",
                trace=True,
                repo_url="https://github.com/example/repo.git",
            ),
            agent=cast(Any, agent),
            session=session,
            local_persist=cast(Any, local_persist),
            runtime=cast(
                Any,
                SimpleNamespace(
                    planner_lm=object(),
                    cfg=SimpleNamespace(app_env="test"),
                    repository=None,
                    identity_rows=None,
                    persistence_required=False,
                ),
            ),
            workspace_id="workspace",
            user_id="user",
            sess_id="session",
            execution_emitter=cast(Any, object()),
        )
        assert prepared is not None

        request = build_workspace_task_request(
            agent=agent,
            prepared_turn=prepared,
            cancel_check=lambda: False,
        )

        assert request.agent is agent
        assert request.message == "hello"
        assert request.execution_mode == "tools_only"
        assert request.trace is True
        assert request.repo_url == "https://github.com/example/repo.git"
        assert request.repo_ref is None
        assert request.context_paths is None
        assert request.batch_concurrency is None
        assert request.workspace_id == "workspace"
        assert request.prepare is prepared.prepare_worker

    with patch(
        "fleet_rlm.api.routers.ws.turn_setup.initialize_turn_lifecycle",
        fake_initialize_turn_lifecycle,
    ):
        asyncio.run(scenario())
