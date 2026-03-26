from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast
import uuid

from fleet_rlm.api.routers.ws.runtime import _ChatSessionState
from fleet_rlm.api.routers.ws.turn_setup import prepare_chat_message_turn
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
        assert agent.execution_mode == "tools_only"
        assert persist_calls == [
            {"include_volume_save": True, "latest_user_message": "hello"}
        ]
        assert session.cancel_flag["cancelled"] is False
        assert session.lifecycle is lifecycle
        assert session.active_run_db_id == active_run_db_id
        assert prepared.message == "hello"
        assert prepared.docs_path == "docs/current.md"
        assert prepared.trace is False
        assert prepared.last_loaded_docs_path == "docs/last.md"
        assert prepared.analytics_enabled is None
        assert prepared.prepare_stream is not None
        await prepared.prepare_stream()
        assert workspace_config_calls == [
            {
                "repo_url": "https://github.com/example/repo.git",
                "repo_ref": "main",
                "context_paths": ["src", "docs", "docs/current.md"],
                "volume_name": "workspace",
            }
        ]
        assert getattr(agent, "daytona_batch_concurrency") == 4
        assert prepared.mlflow_trace_context is not None
        assert prepared.mlflow_trace_context.session_id == "workspace:user:session"
        assert init_calls and init_calls[0]["sandbox_provider"] == "daytona"
        assert init_calls[0]["turn_index"] == 1

    asyncio.run(scenario())
