from __future__ import annotations

import asyncio
import dspy
import pytest
from typing import Any, cast

from fleet_rlm.runtime.agent.chat_agent import RLMReActChatAgent
from fleet_rlm.runtime import factory as runtime_factory
from fleet_rlm.runtime.models import StreamEvent
from tests.unit.fixtures_daytona import FakeDaytonaRuntime, FakeDaytonaSession


def _interpreter(agent: RLMReActChatAgent) -> Any:
    return cast(Any, agent.interpreter)


@pytest.mark.asyncio
async def test_chat_agent_uses_shared_react_stream_with_daytona_runtime(
    monkeypatch,
) -> None:
    runtime = FakeDaytonaRuntime(FakeDaytonaSession())
    agent = RLMReActChatAgent(
        runtime=cast(Any, runtime),
    )
    assert isinstance(agent, RLMReActChatAgent)
    interpreter = _interpreter(agent)
    forwarded_calls: list[dict[str, object]] = []

    async def _fake_stream(self, message, trace, cancel_check):
        forwarded_calls.append(
            {
                "message": message,
                "trace": trace,
                "cancel_check": cancel_check,
            }
        )
        yield StreamEvent(
            kind="final",
            text="Daytona done",
            payload={"history_turns": 1},
        )

    monkeypatch.setattr(
        "fleet_rlm.runtime.agent.chat_agent._aiter_stream", _fake_stream
    )

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
        }
    ]
    assert interpreter.repo_url == "https://github.com/example/repo.git"
    assert interpreter.repo_ref == "main"
    assert interpreter.context_paths == ["docs/spec.md"]
    assert interpreter.volume_name == "tenant-a"
    assert agent.batch_concurrency == 6


def test_chat_agent_registers_recursive_batch_tool_for_daytona_runtime() -> None:
    agent = RLMReActChatAgent(
        runtime=cast(Any, FakeDaytonaRuntime(FakeDaytonaSession())),
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
    agent = RLMReActChatAgent(
        runtime=cast(Any, FakeDaytonaRuntime(FakeDaytonaSession())),
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
                "key_points": ["p1"],
                "summary": "ok",
                "depth": 1,
                "sub_agent_history": 0,
                "trajectory": [],
            },
            None,
            False,
        )

    monkeypatch.setattr(
        "fleet_rlm.runtime.tools.sandbox.delegate._run_runtime_module",
        _fake_run,
    )

    payload = agent._get_tool("summarize_long_document")("inspect this")

    assert payload["status"] == "ok"
    assert payload["summary"] or payload.get("answer")
    assert captured["module_name"] == "summarize_long_document"


@pytest.mark.asyncio
async def test_daytona_parallel_semantic_map_command_is_unavailable() -> None:
    agent = RLMReActChatAgent(
        runtime=cast(Any, FakeDaytonaRuntime(FakeDaytonaSession())),
    )

    with pytest.raises(ValueError, match="not available in the current runtime"):
        await agent.execute_command("parallel_semantic_map", {"query": "summarize"})


@pytest.mark.asyncio
async def test_daytona_recursive_batch_tool_preserves_order_and_concurrency(
    monkeypatch,
) -> None:
    agent = RLMReActChatAgent(
        runtime=cast(Any, FakeDaytonaRuntime(FakeDaytonaSession())),
        delegate_max_calls_per_turn=8,
    )
    agent.batch_concurrency = 2

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
        "fleet_rlm.runtime.tools.batch_tools.spawn_delegate_sub_agent_async",
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
    session = FakeDaytonaSession()
    runtime = FakeDaytonaRuntime(session)
    agent = RLMReActChatAgent(
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

    restored = RLMReActChatAgent(
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
    session = FakeDaytonaSession()
    runtime = FakeDaytonaRuntime(session)
    agent = RLMReActChatAgent(
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

    restored = RLMReActChatAgent(
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
async def test_chat_agent_async_stream_reconfigures_workspace_and_releases_old_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = FakeDaytonaRuntime(FakeDaytonaSession())
    agent = RLMReActChatAgent(runtime=cast(Any, runtime))
    interpreter = _interpreter(agent)
    runtime.session.bind_current_async_owner()
    interpreter._session = runtime.session
    interpreter._session_source_key = (
        "https://github.com/example/old.git",
        "main",
        tuple(),
        None,
    )

    async def _fake_stream(self, message, trace, cancel_check):
        yield StreamEvent(kind="final", text="done", payload={})

    monkeypatch.setattr(
        "fleet_rlm.runtime.agent.chat_agent._aiter_stream", _fake_stream
    )

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
async def test_chat_agent_preserves_existing_workspace_when_stream_args_omitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = FakeDaytonaRuntime(FakeDaytonaSession())
    agent = RLMReActChatAgent(runtime=cast(Any, runtime))
    interpreter = _interpreter(agent)
    interpreter.configure_workspace(
        repo_url="https://github.com/example/repo.git",
        repo_ref="main",
        context_paths=["docs/spec.md"],
        volume_name="tenant-a",
    )
    forwarded_calls: list[dict[str, object]] = []

    async def _fake_stream(self, message, trace, cancel_check):
        forwarded_calls.append(
            {
                "message": message,
                "trace": trace,
                "cancel_check": cancel_check,
            }
        )
        yield StreamEvent(kind="final", text="done", payload={})

    monkeypatch.setattr(
        "fleet_rlm.runtime.agent.chat_agent._aiter_stream", _fake_stream
    )

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
        }
    ]
    assert interpreter.repo_url == "https://github.com/example/repo.git"
    assert interpreter.repo_ref == "main"
    assert interpreter.context_paths == ["docs/spec.md"]
    assert interpreter.volume_name == "tenant-a"


def test_shutdown_preserves_remote_session_when_configured() -> None:
    session = FakeDaytonaSession()
    runtime = FakeDaytonaRuntime(session)
    agent = RLMReActChatAgent(
        runtime=cast(Any, runtime),
        delete_session_on_shutdown=False,
    )
    _interpreter(agent)._session = session

    agent.shutdown()

    assert session.closed == 1
    assert session.deleted == 0


def test_chat_agent_threads_interpreter_async_execute() -> None:
    agent = RLMReActChatAgent(
        runtime=cast(Any, FakeDaytonaRuntime(FakeDaytonaSession())),
        interpreter_async_execute=False,
    )

    assert _interpreter(agent).async_execute is False


def test_build_chat_agent_threads_interpreter_async_execute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def _extra_tool() -> None:
        return None

    class _FakeRLMReActChatAgent:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(
        "fleet_rlm.runtime.factory.RLMReActChatAgent",
        _FakeRLMReActChatAgent,
    )

    agent = runtime_factory.build_chat_agent(
        timeout=123,
        max_depth=4,
        secret_name="SECRET",
        volume_name="tenant-a",
        extra_tools=[_extra_tool],
        interpreter_async_execute=False,
        planner_lm=object(),
    )

    assert isinstance(agent, _FakeRLMReActChatAgent)
    assert captured["timeout"] == 123
    assert captured["max_depth"] == 4
    assert captured["secret_name"] == "SECRET"
    assert captured["volume_name"] == "tenant-a"
    assert captured["extra_tools"] == [_extra_tool]
    assert captured["interpreter_async_execute"] is False


def test_build_chat_agent_threads_recursive_verification_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class _FakeRLMReActChatAgent:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(
        "fleet_rlm.runtime.factory.RLMReActChatAgent",
        _FakeRLMReActChatAgent,
    )

    agent = runtime_factory.build_chat_agent(
        recursive_verification_enabled=True,
        planner_lm=object(),
    )

    assert isinstance(agent, _FakeRLMReActChatAgent)
    assert captured["recursive_verification_enabled"] is True


@pytest.mark.asyncio
async def test_sandbox_spec_flows_from_agent_to_runtime() -> None:
    """Verify SandboxSpec is threaded from agent → interpreter → runtime."""
    from fleet_rlm.integrations.daytona.types import SandboxSpec

    spec = SandboxSpec(
        language="python",
        labels={"env": "test"},
        env_vars={"MY_VAR": "hello"},
    )
    session = FakeDaytonaSession()
    runtime = FakeDaytonaRuntime(session)

    agent = RLMReActChatAgent(
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
    from fleet_rlm.integrations.daytona.types import SandboxSpec

    img = Image.debian_slim("3.12").pip_install(["dspy"])
    spec = SandboxSpec(image=img, labels={"managed-by": "fleet-rlm"})

    session = FakeDaytonaSession()
    runtime = FakeDaytonaRuntime(session)

    agent = RLMReActChatAgent(
        runtime=runtime,
        sandbox_spec=spec,
    )

    await agent.astart()

    forwarded_spec = runtime.create_specs[0]
    assert forwarded_spec is spec
    assert forwarded_spec.uses_declarative_image is True
    assert forwarded_spec.image is img
    assert "dspy" in img.dockerfile()
