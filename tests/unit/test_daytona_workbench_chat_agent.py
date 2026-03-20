from __future__ import annotations

import dspy
import pytest

from fleet_rlm.core.agent.chat_agent import RLMReActChatAgent
from fleet_rlm.core.models import StreamEvent
from fleet_rlm.infrastructure.providers.daytona.chat_agent import (
    DaytonaWorkbenchChatAgent,
)


class _FakeSession:
    def __init__(self) -> None:
        self.context_sources = []
        self.workspace_path = "/workspace/session"
        self.sandbox_id = "sbx-root"
        self.closed = 0
        self.deleted = 0
        self.driver_started = 0

    async def astart_driver(self, *, timeout: float) -> None:
        _ = timeout
        self.driver_started += 1

    def start_driver(self, *, timeout: float) -> None:
        _ = timeout
        self.driver_started += 1

    async def aclose_driver(self) -> None:
        self.closed += 1

    async def adelete(self) -> None:
        self.deleted += 1


class _FakeRuntime:
    def __init__(self, session: _FakeSession) -> None:
        self.session = session
        self._resolved_config = object()
        self.create_calls: list[
            tuple[str | None, str | None, list[str], str | None]
        ] = []
        self.resume_calls: list[tuple[str, str | None, str | None, str]] = []

    async def acreate_workspace_session(
        self,
        *,
        repo_url: str | None,
        ref: str | None,
        context_paths: list[str] | None = None,
        volume_name: str | None = None,
    ) -> _FakeSession:
        self.create_calls.append(
            (repo_url, ref, list(context_paths or []), volume_name)
        )
        return self.session

    async def aresume_workspace_session(
        self,
        *,
        sandbox_id: str,
        repo_url: str | None,
        ref: str | None,
        workspace_path: str,
        context_sources=None,
    ) -> _FakeSession:
        _ = context_sources
        self.resume_calls.append((sandbox_id, repo_url, ref, workspace_path))
        return self.session


@pytest.mark.asyncio
async def test_daytona_workbench_chat_agent_uses_shared_react_stream_with_daytona_runtime(
    monkeypatch,
) -> None:
    runtime = _FakeRuntime(_FakeSession())
    agent = DaytonaWorkbenchChatAgent(
        runtime=runtime,
    )
    assert isinstance(agent, RLMReActChatAgent)
    forwarded_calls: list[dict[str, object]] = []

    async def _fake_stream(self, *args, **kwargs):
        forwarded_calls.append(kwargs)
        yield StreamEvent(
            kind="final",
            text="Daytona done",
            payload={"history_turns": 1},
        )

    monkeypatch.setattr(RLMReActChatAgent, "aiter_chat_turn_stream", _fake_stream)

    events = [
        event
        async for event in agent.aiter_chat_turn_stream(
            "inspect recursive reasoning",
            repo_url="https://github.com/example/repo.git",
            repo_ref="main",
            context_paths=["docs/spec.md"],
            batch_concurrency=6,
            volume_name="tenant-a",
        )
    ]

    assert len(events) == 2
    assert events[0].kind == "status"
    assert events[0].payload["runtime"]["runtime_mode"] == "daytona_pilot"
    assert events[0].payload["runtime"]["volume_name"] == "tenant-a"
    assert events[1].kind == "final"
    assert events[1].payload["runtime_mode"] == "daytona_pilot"
    assert events[1].payload["runtime"]["runtime_mode"] == "daytona_pilot"
    assert runtime.create_calls == []
    assert forwarded_calls == [
        {
            "message": "inspect recursive reasoning",
            "trace": True,
            "cancel_check": None,
            "docs_path": None,
        }
    ]
    assert agent.interpreter.repo_url == "https://github.com/example/repo.git"
    assert agent.interpreter.repo_ref == "main"
    assert agent.interpreter.context_paths == ["docs/spec.md"]
    assert agent.interpreter.volume_name == "tenant-a"


def test_exported_daytona_session_state_can_resume_existing_sandbox() -> None:
    session = _FakeSession()
    runtime = _FakeRuntime(session)
    agent = DaytonaWorkbenchChatAgent(
        runtime=runtime,
        delete_session_on_shutdown=False,
    )
    agent.loaded_document_paths = ["docs/spec.md"]
    agent.history = dspy.History(
        messages=[
            {
                "user_request": "Inspect the repo.",
                "assistant_response": "Done.",
            }
        ]
    )
    agent.interpreter.configure_workspace(
        repo_url="https://github.com/example/repo.git",
        repo_ref="main",
        context_paths=["docs/spec.md"],
        volume_name="tenant-a",
    )
    agent.interpreter._session = session
    agent.interpreter._session_source_key = (
        "https://github.com/example/repo.git",
        "main",
        ("docs/spec.md",),
        "tenant-a",
    )

    exported = agent.export_session_state()

    restored = DaytonaWorkbenchChatAgent(
        runtime=runtime,
        delete_session_on_shutdown=False,
    )
    restored.import_session_state(exported)
    restored.interpreter.start()

    assert restored.loaded_document_paths == ["docs/spec.md"]
    assert runtime.resume_calls == [
        (
            "sbx-root",
            "https://github.com/example/repo.git",
            "main",
            "/workspace/session",
        )
    ]


def test_shutdown_preserves_remote_session_when_configured() -> None:
    session = _FakeSession()
    runtime = _FakeRuntime(session)
    agent = DaytonaWorkbenchChatAgent(
        runtime=runtime,
        delete_session_on_shutdown=False,
    )
    agent.interpreter._session = session

    agent.shutdown()

    assert session.closed == 1
    assert session.deleted == 0
