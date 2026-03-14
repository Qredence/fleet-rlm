from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fleet_rlm.daytona_rlm.chat_agent import DaytonaWorkbenchChatAgent
from fleet_rlm.daytona_rlm.types import RolloutBudget


class _FakeSession:
    def __init__(self) -> None:
        self.context_sources = []
        self.workspace_path = "/workspace/session"
        self.sandbox_id = "sbx-root"
        self.closed = 0
        self.deleted = 0

    def close_driver(self) -> None:
        self.closed += 1

    def delete(self) -> None:
        self.deleted += 1


class _FakeRuntime:
    def __init__(self, session: _FakeSession) -> None:
        self.session = session
        self.calls: list[tuple[str | None, str | None, list[str]]] = []
        self.resume_calls: list[tuple[str, str | None, str | None, str]] = []

    def create_workspace_session(
        self,
        *,
        repo_url: str | None,
        ref: str | None,
        context_paths: list[str] | None = None,
    ):
        self.calls.append((repo_url, ref, list(context_paths or [])))
        return self.session

    def resume_workspace_session(
        self,
        *,
        sandbox_id: str,
        repo_url: str | None,
        ref: str | None,
        workspace_path: str,
        context_sources=None,
    ):
        _ = context_sources
        self.resume_calls.append((sandbox_id, repo_url, ref, workspace_path))
        return self.session


class _FakeRunner:
    init_kwargs: dict[str, object] | None = None
    last_run_kwargs: dict[str, object] | None = None

    def __init__(self, **kwargs) -> None:
        _FakeRunner.init_kwargs = kwargs

    def run(self, **kwargs):
        _FakeRunner.last_run_kwargs = kwargs
        return SimpleNamespace()


def test_daytona_workbench_chat_agent_delegates_to_runner_with_session(
    monkeypatch,
) -> None:
    session = _FakeSession()
    runtime = _FakeRuntime(session)
    planner_lm = object()
    agent = DaytonaWorkbenchChatAgent(
        runtime=runtime,
        budget=RolloutBudget(max_depth=3, batch_concurrency=6),
        planner_lm=planner_lm,
        output_dir=Path("results/daytona-rlm"),
    )

    monkeypatch.setattr(
        "fleet_rlm.daytona_rlm.chat_agent.DaytonaRLMRunner",
        _FakeRunner,
    )

    _ = agent._run_turn_blocking(
        message="inspect recursive reasoning",
        repo_url=None,
        repo_ref=None,
        context_paths=[],
        budget=RolloutBudget(max_depth=3, batch_concurrency=6),
        event_callback=lambda event: None,
        cancel_check=lambda: False,
    )

    assert _FakeRunner.init_kwargs is not None
    assert _FakeRunner.init_kwargs["lm"] is planner_lm
    assert _FakeRunner.last_run_kwargs is not None
    assert _FakeRunner.last_run_kwargs["session"] is session
    assert _FakeRunner.last_run_kwargs["task"] == "inspect recursive reasoning"


def test_exported_daytona_session_state_can_resume_existing_sandbox() -> None:
    session = _FakeSession()
    runtime = _FakeRuntime(session)
    agent = DaytonaWorkbenchChatAgent(
        runtime=runtime,
        delete_session_on_shutdown=False,
    )
    agent.repo_url = "https://github.com/example/repo.git"
    agent.repo_ref = "main"
    agent.context_paths = ["/tmp/context.md"]
    agent._session = session
    agent._session_source_key = (
        "https://github.com/example/repo.git",
        "main",
        ("/tmp/context.md",),
    )

    exported = agent.export_session_state()

    restored = DaytonaWorkbenchChatAgent(
        runtime=runtime,
        delete_session_on_shutdown=False,
    )
    restored.import_session_state(exported)
    resumed = restored._ensure_session(
        repo_url="https://github.com/example/repo.git",
        repo_ref="main",
        context_paths=["/tmp/context.md"],
    )

    assert resumed is session
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
    agent._session = session

    agent.shutdown()

    assert session.closed == 1
    assert session.deleted == 0
