from __future__ import annotations

import asyncio
import threading
import dspy
import pytest
from typing import Any, cast

from fleet_rlm.cli import runtime_factory
from fleet_rlm.runtime.agent.chat_agent import RLMReActChatAgent
from fleet_rlm.runtime.models import StreamEvent
from fleet_rlm.integrations.providers.daytona.agent import (
    DaytonaWorkbenchChatAgent,
)


class _FakeSession:
    def __init__(self) -> None:
        self.context_sources = []
        self.workspace_path = "/workspace/session"
        self.sandbox_id = "sbx-root"
        self.context_id = "ctx-root"
        self.closed = 0
        self.deleted = 0
        self.driver_started = 0
        self.owner_thread_id: int | None = None
        self.owner_loop_id: int | None = None

    def bind_current_async_owner(self) -> None:
        self.owner_thread_id = threading.get_ident()
        self.owner_loop_id = id(asyncio.get_running_loop())

    def matches_current_async_owner(self) -> bool:
        if self.owner_thread_id is None or self.owner_loop_id is None:
            return False
        try:
            return (
                self.owner_thread_id,
                self.owner_loop_id,
            ) == (threading.get_ident(), id(asyncio.get_running_loop()))
        except RuntimeError:
            return False

    async def astart_driver(self, *, timeout: float) -> None:
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
        self.create_specs: list[object | None] = []
        self.resume_calls: list[tuple[str, str | None, str | None, str]] = []

    async def acreate_workspace_session(
        self,
        *,
        repo_url: str | None,
        ref: str | None,
        context_paths: list[str] | None = None,
        volume_name: str | None = None,
        spec: object | None = None,
    ) -> _FakeSession:
        self.create_calls.append(
            (repo_url, ref, list(context_paths or []), volume_name)
        )
        self.create_specs.append(spec)
        self.session.bind_current_async_owner()
        return self.session

    async def aresume_workspace_session(
        self,
        *,
        sandbox_id: str,
        repo_url: str | None,
        ref: str | None,
        workspace_path: str,
        context_sources=None,
        context_id: str | None = None,
    ) -> _FakeSession:
        _ = context_sources, context_id
        self.resume_calls.append((sandbox_id, repo_url, ref, workspace_path))
        self.session.bind_current_async_owner()
        return self.session

    async def aclose(self) -> None:
        return None


def _interpreter(agent: DaytonaWorkbenchChatAgent) -> Any:
    return cast(Any, agent.interpreter)


@pytest.mark.asyncio
async def test_daytona_workbench_chat_agent_uses_shared_react_stream_with_daytona_runtime(
    monkeypatch,
) -> None:
    runtime = _FakeRuntime(_FakeSession())
    agent = DaytonaWorkbenchChatAgent(
        runtime=cast(Any, runtime),
    )
    assert isinstance(agent, RLMReActChatAgent)
    interpreter = _interpreter(agent)
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
    assert interpreter.repo_url == "https://github.com/example/repo.git"
    assert interpreter.repo_ref == "main"
    assert interpreter.context_paths == ["docs/spec.md"]
    assert interpreter.volume_name == "tenant-a"
    assert agent.daytona_batch_concurrency == 6


def test_daytona_workbench_chat_agent_registers_recursive_batch_tool() -> None:
    agent = DaytonaWorkbenchChatAgent(
        runtime=cast(Any, _FakeRuntime(_FakeSession())),
    )

    tool_names = [
        getattr(tool, "name", None) or getattr(tool, "__name__", str(tool))
        for tool in agent.react_tools
    ]

    assert "rlm_query_batched" in tool_names
    assert "parallel_semantic_map" not in tool_names


def test_daytona_named_heavy_tool_uses_cached_runtime_module_path(
    monkeypatch,
) -> None:
    agent = DaytonaWorkbenchChatAgent(
        runtime=cast(Any, _FakeRuntime(_FakeSession())),
    )
    agent._set_document("active", "# Header\nOne\nTwo")
    agent.active_alias = "active"

    captured: dict[str, Any] = {}

    def _fake_run(agent_obj: Any, module_name: str, **kwargs: Any):
        captured["agent"] = agent_obj
        captured["module_name"] = module_name
        captured["kwargs"] = kwargs
        return (
            {
                "findings": ["f1"],
                "answer": "ok",
                "sections_examined": 2,
                "depth": 1,
                "sub_agent_history": 0,
                "trajectory": [],
            },
            None,
            False,
        )

    monkeypatch.setattr(
        "fleet_rlm.runtime.tools.sandbox_delegate_tools._run_runtime_module",
        _fake_run,
    )

    payload = agent._get_tool("analyze_long_document")("inspect this")

    assert payload["status"] == "ok"
    assert payload["answer"] == "ok"
    assert captured["agent"] is agent
    assert captured["module_name"] == "analyze_long_document"
    assert captured["kwargs"]["query"] == "inspect this"


@pytest.mark.asyncio
async def test_daytona_parallel_semantic_map_command_is_unavailable() -> None:
    agent = DaytonaWorkbenchChatAgent(
        runtime=cast(Any, _FakeRuntime(_FakeSession())),
    )

    with pytest.raises(ValueError, match="not available in the current runtime"):
        await agent.execute_command("parallel_semantic_map", {"query": "summarize"})


@pytest.mark.asyncio
async def test_daytona_recursive_batch_tool_preserves_order_and_concurrency(
    monkeypatch,
) -> None:
    agent = DaytonaWorkbenchChatAgent(
        runtime=cast(Any, _FakeRuntime(_FakeSession())),
        delegate_max_calls_per_turn=8,
    )
    agent.daytona_batch_concurrency = 2

    active_calls = 0
    max_active_calls = 0

    async def _fake_spawn(*_args: Any, prompt: str, context: str = "", **_kwargs: Any):
        nonlocal active_calls, max_active_calls
        active_calls += 1
        max_active_calls = max(max_active_calls, active_calls)
        await asyncio.sleep(0.01)
        active_calls -= 1
        if prompt == "fail":
            return {"status": "error", "error": "child failed"}
        return {
            "status": "ok",
            "answer": f"{prompt}|{context}",
            "sub_agent_history": 1,
            "depth": 1,
            "trajectory": {"trajectory": [{"thought": prompt}]},
        }

    monkeypatch.setattr(
        "fleet_rlm.runtime.tools.sandbox_delegate_tools.spawn_delegate_sub_agent_async",
        _fake_spawn,
    )

    result = agent._get_tool("rlm_query_batched")(
        [
            {"query": "alpha", "context": "ctx-a"},
            {"query": "fail", "context": "ctx-b"},
            {"query": "omega", "context": "ctx-c"},
        ]
    )

    payload = await result

    assert payload["status"] == "ok"
    assert payload["batch_concurrency"] == 2
    assert payload["task_count"] == 3
    assert payload["success_count"] == 2
    assert payload["error_count"] == 1
    assert max_active_calls == 2
    assert [item["query"] for item in payload["results"]] == [
        "alpha",
        "fail",
        "omega",
    ]
    assert payload["results"][0]["answer"] == "alpha|ctx-a"
    assert payload["results"][1]["status"] == "error"
    assert payload["results"][1]["error"] == "child failed"
    assert payload["results"][2]["answer"] == "omega|ctx-c"
    assert all(
        item["callback_name"] == "rlm_query_batched" for item in payload["results"]
    )


def test_exported_daytona_session_state_can_resume_existing_sandbox() -> None:
    session = _FakeSession()
    runtime = _FakeRuntime(session)
    agent = DaytonaWorkbenchChatAgent(
        runtime=cast(Any, runtime),
        delete_session_on_shutdown=False,
    )
    interpreter = _interpreter(agent)
    agent.loaded_document_paths = ["docs/spec.md"]
    agent.history = dspy.History(
        messages=[
            {
                "user_request": "Inspect the repo.",
                "assistant_response": "Done.",
            }
        ]
    )
    interpreter.configure_workspace(
        repo_url="https://github.com/example/repo.git",
        repo_ref="main",
        context_paths=["docs/spec.md"],
        volume_name="tenant-a",
    )
    interpreter._session = session
    interpreter._session_source_key = (
        "https://github.com/example/repo.git",
        "main",
        ("docs/spec.md",),
        "tenant-a",
    )

    exported = agent.export_session_state()

    restored = DaytonaWorkbenchChatAgent(
        runtime=cast(Any, runtime),
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


@pytest.mark.asyncio
async def test_async_imported_daytona_session_state_can_resume_existing_sandbox() -> (
    None
):
    session = _FakeSession()
    runtime = _FakeRuntime(session)
    agent = DaytonaWorkbenchChatAgent(
        runtime=cast(Any, runtime),
        delete_session_on_shutdown=False,
    )
    interpreter = _interpreter(agent)
    agent.loaded_document_paths = ["docs/spec.md"]
    agent.history = dspy.History(
        messages=[
            {
                "user_request": "Inspect the repo.",
                "assistant_response": "Done.",
            }
        ]
    )
    interpreter.configure_workspace(
        repo_url="https://github.com/example/repo.git",
        repo_ref="main",
        context_paths=["docs/spec.md"],
        volume_name="tenant-a",
    )
    interpreter._session = session
    interpreter._session_source_key = (
        "https://github.com/example/repo.git",
        "main",
        ("docs/spec.md",),
        "tenant-a",
    )

    exported = agent.export_session_state()

    restored = DaytonaWorkbenchChatAgent(
        runtime=cast(Any, runtime),
        delete_session_on_shutdown=False,
    )
    await restored.aimport_session_state(exported)
    await restored.interpreter.astart()

    assert restored.loaded_document_paths == ["docs/spec.md"]
    assert runtime.resume_calls == [
        (
            "sbx-root",
            "https://github.com/example/repo.git",
            "main",
            "/workspace/session",
        )
    ]


@pytest.mark.asyncio
async def test_daytona_workbench_chat_agent_async_stream_reconfigures_workspace_and_releases_old_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _FakeRuntime(_FakeSession())
    agent = DaytonaWorkbenchChatAgent(runtime=cast(Any, runtime))
    interpreter = _interpreter(agent)
    runtime.session.bind_current_async_owner()
    interpreter._session = runtime.session
    interpreter._session_source_key = (
        "https://github.com/example/old.git",
        "main",
        tuple(),
        None,
    )

    async def _fake_stream(self, *args, **kwargs):
        yield StreamEvent(kind="final", text="done", payload={})

    monkeypatch.setattr(RLMReActChatAgent, "aiter_chat_turn_stream", _fake_stream)

    events = [
        event
        async for event in agent.aiter_chat_turn_stream(
            "inspect recursive reasoning",
            repo_url="https://github.com/example/new.git",
            repo_ref="main",
        )
    ]

    assert [event.kind for event in events] == ["status", "final"]
    assert runtime.session.deleted == 1
    assert runtime.session.closed == 0


@pytest.mark.asyncio
async def test_daytona_workbench_chat_agent_preserves_existing_workspace_when_stream_args_omitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _FakeRuntime(_FakeSession())
    agent = DaytonaWorkbenchChatAgent(runtime=cast(Any, runtime))
    interpreter = _interpreter(agent)
    interpreter.configure_workspace(
        repo_url="https://github.com/example/repo.git",
        repo_ref="main",
        context_paths=["docs/spec.md"],
        volume_name="tenant-a",
    )
    forwarded_calls: list[dict[str, object]] = []

    async def _fake_stream(self, *args, **kwargs):
        forwarded_calls.append(kwargs)
        yield StreamEvent(kind="final", text="done", payload={})

    monkeypatch.setattr(RLMReActChatAgent, "aiter_chat_turn_stream", _fake_stream)

    events = [
        event
        async for event in agent.aiter_chat_turn_stream("inspect recursive reasoning")
    ]

    assert [event.kind for event in events] == ["status", "final"]
    assert events[0].payload["repo_url"] == "https://github.com/example/repo.git"
    assert events[0].payload["repo_ref"] == "main"
    assert events[0].payload["context_paths"] == ["docs/spec.md"]
    assert events[0].payload["runtime"]["volume_name"] == "tenant-a"
    assert forwarded_calls == [
        {
            "message": "inspect recursive reasoning",
            "trace": True,
            "cancel_check": None,
            "docs_path": None,
        }
    ]
    assert interpreter.repo_url == "https://github.com/example/repo.git"
    assert interpreter.repo_ref == "main"
    assert interpreter.context_paths == ["docs/spec.md"]
    assert interpreter.volume_name == "tenant-a"


def test_shutdown_preserves_remote_session_when_configured() -> None:
    session = _FakeSession()
    runtime = _FakeRuntime(session)
    agent = DaytonaWorkbenchChatAgent(
        runtime=cast(Any, runtime),
        delete_session_on_shutdown=False,
    )
    _interpreter(agent)._session = session

    agent.shutdown()

    assert session.closed == 1
    assert session.deleted == 0


def test_daytona_workbench_chat_agent_threads_interpreter_async_execute() -> None:
    agent = DaytonaWorkbenchChatAgent(
        runtime=cast(Any, _FakeRuntime(_FakeSession())),
        interpreter_async_execute=False,
    )

    assert _interpreter(agent).async_execute is False


def test_build_daytona_workbench_chat_agent_threads_interpreter_async_execute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class _FakeDaytonaWorkbenchChatAgent:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(
        "fleet_rlm.integrations.providers.daytona.agent.DaytonaWorkbenchChatAgent",
        _FakeDaytonaWorkbenchChatAgent,
    )

    agent = runtime_factory.build_daytona_workbench_chat_agent(
        timeout=123,
        max_depth=4,
        interpreter_async_execute=False,
    )

    assert isinstance(agent, _FakeDaytonaWorkbenchChatAgent)
    assert captured["timeout"] == 123
    assert captured["max_depth"] == 4
    assert captured["interpreter_async_execute"] is False


@pytest.mark.asyncio
async def test_sandbox_spec_flows_from_agent_to_runtime() -> None:
    """Verify SandboxSpec is threaded from agent → interpreter → runtime."""
    from fleet_rlm.integrations.providers.daytona.types import SandboxSpec

    spec = SandboxSpec(
        language="python",
        labels={"env": "test"},
        env_vars={"MY_VAR": "hello"},
    )
    session = _FakeSession()
    runtime = _FakeRuntime(session)

    agent = DaytonaWorkbenchChatAgent(
        runtime=runtime,
        sandbox_spec=spec,
    )

    # The interpreter should carry the spec
    interpreter = cast("Any", agent.interpreter)
    assert interpreter.sandbox_spec is spec

    # Trigger session creation via start()
    await agent.astart()

    # The spec should have been forwarded to the runtime
    assert len(runtime.create_specs) == 1
    assert runtime.create_specs[0] is spec


@pytest.mark.asyncio
async def test_sandbox_spec_with_declarative_image_flows_through() -> None:
    """Verify a daytona.Image declarative builder reaches the runtime."""
    from daytona import Image
    from fleet_rlm.integrations.providers.daytona.types import SandboxSpec

    img = Image.debian_slim("3.12").pip_install(["dspy"])
    spec = SandboxSpec(image=img, labels={"managed-by": "fleet-rlm"})

    session = _FakeSession()
    runtime = _FakeRuntime(session)

    agent = DaytonaWorkbenchChatAgent(
        runtime=runtime,
        sandbox_spec=spec,
    )

    await agent.astart()

    forwarded_spec = runtime.create_specs[0]
    assert forwarded_spec is spec
    assert forwarded_spec.uses_declarative_image is True
    assert forwarded_spec.image is img
    assert "dspy" in img.dockerfile()
