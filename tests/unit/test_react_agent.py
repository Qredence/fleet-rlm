"""Unit tests for the ReAct chat agent.

These tests mock DSPy ReAct + Modal interpreter behavior to avoid
cloud credentials while validating host-side orchestration logic.

Streaming tests live in test_react_streaming.py.
Tool / filesystem / PDF tests live in test_react_tools.py.
"""

from __future__ import annotations

from types import SimpleNamespace

import dspy
import pytest

from fleet_rlm.react import RLMReActChatAgent, RLMReActChatSignature
from fleet_rlm.react import tools as react_tools
from tests.unit.fixtures_react import FakeInterpreter

pytestmark = pytest.mark.usefixtures("react_records")


def test_react_agent_constructed_with_explicit_signature_and_tools(
    react_records: list[dict[str, object]],
):
    agent = RLMReActChatAgent(interpreter=FakeInterpreter())  # noqa: F841

    assert len(react_records) == 1
    rec = react_records[0]
    assert rec["signature"] is RLMReActChatSignature
    assert isinstance(rec["tools"], list) and len(rec["tools"]) > 0
    assert rec["max_iters"] == 10

    tool_names = [
        getattr(tool, "name", None) or getattr(tool, "__name__", str(tool))
        for tool in rec["tools"]
    ]
    for expected in [
        "load_document",
        "parallel_semantic_map",
        "analyze_long_document",
        "summarize_long_document",
    ]:
        assert expected in tool_names, f"Missing tool: {expected}"


def test_tool_registry_includes_specialized_tools_and_extra_tools(
    react_records: list[dict[str, object]],
):
    def custom_tool(topic: str) -> str:
        """Custom research tool for tests."""
        return f"researched: {topic}"

    agent = RLMReActChatAgent(  # noqa: F841
        interpreter=FakeInterpreter(),
        extra_tools=[custom_tool],
    )

    rec = react_records[0]
    tool_names = [
        getattr(tool, "name", None) or getattr(tool, "__name__", str(tool))
        for tool in rec["tools"]
    ]
    assert "custom_tool" in tool_names


def test_chat_turn_appends_history_and_preserves_session(monkeypatch):
    agent = RLMReActChatAgent(interpreter=FakeInterpreter())
    r1 = agent.chat_turn("hello")
    r2 = agent.chat_turn("how are you?")

    assert r1["assistant_response"] == "echo:hello"
    assert r2["assistant_response"] == "echo:how are you?"
    assert r2["history_turns"] == 2
    assert len(agent.history.messages) == 2


def test_chat_turn_defers_mlflow_metadata_merge_to_callers(monkeypatch):
    monkeypatch.setattr(
        "fleet_rlm.analytics.mlflow_integration.trace_result_metadata",
        lambda response_preview=None: {
            "mlflow_trace_id": "trace-123",
            "mlflow_client_request_id": "req-123",
        },
    )

    agent = RLMReActChatAgent(interpreter=FakeInterpreter())
    result = agent.chat_turn("hello")

    assert "mlflow_trace_id" not in result
    assert "mlflow_client_request_id" not in result


def test_parallel_semantic_map_uses_llm_query_batched(monkeypatch):
    agent = RLMReActChatAgent(interpreter=FakeInterpreter())
    agent.documents["test_doc"] = "line1\nline2"
    agent.active_alias = "test_doc"

    result = agent.parallel_semantic_map(
        query="summarize", chunk_strategy="headers", max_chunks=5
    )

    assert result["status"] == "ok"
    assert "chunk_count" in result
    assert len(agent.interpreter.execute_calls) > 0


def test_context_manager_starts_and_stops_interpreter(monkeypatch):
    fake_interp = FakeInterpreter()
    agent = RLMReActChatAgent(interpreter=fake_interp)

    with agent:
        assert fake_interp.start_calls == 1

    assert fake_interp.shutdown_calls == 1


# -----------------------------------------------------------------------
# Phase 1 Tests — dspy.Module subclass, forward(), dspy.Tool wrappers,
#                 typed Signature generics
# -----------------------------------------------------------------------


def test_agent_is_dspy_module_subclass(monkeypatch):
    """RLMReActChatAgent must subclass dspy.Module."""
    assert issubclass(RLMReActChatAgent, dspy.Module)
    agent = RLMReActChatAgent(interpreter=FakeInterpreter())
    assert isinstance(agent, dspy.Module)


def test_agent_has_react_as_discoverable_submodule(monkeypatch):
    """self.react (dspy.ReAct) must appear in named_sub_modules."""
    agent = RLMReActChatAgent(interpreter=FakeInterpreter())
    assert hasattr(agent, "react")
    # The fake isn't a real dspy.Module so it won't appear in named_sub_modules,
    # but the attribute assignment itself is correct.
    assert agent.react is not None


def test_forward_delegates_to_react_and_starts_interpreter(monkeypatch):
    """forward() should call self.react(...) and start the interpreter."""
    fake_interpreter = FakeInterpreter()
    agent = RLMReActChatAgent(interpreter=fake_interpreter)

    prediction = agent.forward(user_request="test query")
    assert prediction.assistant_response == "echo:test query"
    assert fake_interpreter.start_calls == 1


def test_forward_accepts_custom_history(monkeypatch):
    """forward() should use the provided history, not the agent's own."""
    agent = RLMReActChatAgent(interpreter=FakeInterpreter())
    custom_history = dspy.History(
        messages=[{"user_request": "prior", "assistant_response": "old"}]
    )

    prediction = agent.forward(user_request="new", history=custom_history)
    assert prediction.assistant_response == "echo:new"
    # Agent's own history should be unmodified
    assert len(agent.history.messages) == 0


def test_forward_uses_baseline_iters_for_normal_prompt(monkeypatch):
    captured: dict[str, object] = {}

    class _CaptureReact:
        def __call__(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(assistant_response="ok", trajectory={})

    agent = RLMReActChatAgent(
        interpreter=FakeInterpreter(),
        react_max_iters=15,
        deep_react_max_iters=35,
        enable_adaptive_iters=True,
    )
    agent.react = _CaptureReact()  # type: ignore[assignment]

    prediction = agent.forward(user_request="say hello")
    assert prediction.assistant_response == "ok"
    assert captured["max_iters"] == 15


def test_forward_uses_deep_iters_for_deep_analysis_prompt(monkeypatch):
    captured: dict[str, object] = {}

    class _CaptureReact:
        def __call__(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(assistant_response="ok", trajectory={})

    agent = RLMReActChatAgent(
        interpreter=FakeInterpreter(),
        react_max_iters=15,
        deep_react_max_iters=35,
        enable_adaptive_iters=True,
    )
    agent.react = _CaptureReact()  # type: ignore[assignment]

    prediction = agent.forward(
        user_request="Do a full codebase deep analysis for maintainability hotspots."
    )
    assert prediction.assistant_response == "ok"
    assert captured["max_iters"] == 35


def test_forward_escalates_after_repeated_tool_errors(monkeypatch):
    class _ErrorReact:
        def __call__(self, **kwargs):
            return SimpleNamespace(
                assistant_response="first",
                trajectory={
                    "steps": [
                        {"output": "error: tool one failed"},
                        {"output": "error: tool two failed"},
                    ]
                },
            )

    captured: dict[str, object] = {}

    class _CaptureReact:
        def __call__(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(assistant_response="second", trajectory={})

    agent = RLMReActChatAgent(
        interpreter=FakeInterpreter(),
        react_max_iters=15,
        deep_react_max_iters=35,
        enable_adaptive_iters=True,
    )
    agent.react = _ErrorReact()  # type: ignore[assignment]
    first = agent.forward(user_request="quick check")
    assert first.assistant_response == "first"

    agent.react = _CaptureReact()  # type: ignore[assignment]
    second = agent.forward(user_request="quick follow-up")
    assert second.assistant_response == "second"
    assert captured["max_iters"] == 35


def test_forward_disable_adaptive_iters_keeps_baseline(monkeypatch):
    captured: dict[str, object] = {}

    class _CaptureReact:
        def __call__(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(assistant_response="ok", trajectory={})

    agent = RLMReActChatAgent(
        interpreter=FakeInterpreter(),
        react_max_iters=15,
        deep_react_max_iters=35,
        enable_adaptive_iters=False,
    )
    agent.react = _CaptureReact()  # type: ignore[assignment]

    prediction = agent.forward(
        user_request="Do a full codebase deep analysis for maintainability hotspots."
    )
    assert prediction.assistant_response == "ok"
    assert captured["max_iters"] == 15


def test_chat_turn_uses_module_call_semantics(monkeypatch):
    """chat_turn() should invoke the DSPy module call path (`self(...)`)."""
    called: dict[str, object] = {"used": False, "kwargs": {}}

    def _fake_module_call(self, *args, **kwargs):
        called["used"] = True
        called["kwargs"] = kwargs
        request = str(kwargs.get("user_request", ""))
        return SimpleNamespace(
            assistant_response=f"module-call:{request}",
            trajectory={"tool_name_0": "finish"},
        )

    monkeypatch.setattr(RLMReActChatAgent, "__call__", _fake_module_call)

    agent = RLMReActChatAgent(interpreter=FakeInterpreter())
    result = agent.chat_turn("hello")

    assert called["used"] is True
    assert called["kwargs"] == {"user_request": "hello"}
    assert result["assistant_response"] == "module-call:hello"
    assert result["history_turns"] == 1
    assert agent.history.messages[0]["user_request"] == "hello"


def test_forward_guardrail_strict_rejects_empty_response(monkeypatch):
    class _EmptyReact:
        def __call__(self, **kwargs):
            return SimpleNamespace(assistant_response="   ", trajectory={})

    agent = RLMReActChatAgent(
        interpreter=FakeInterpreter(),
        guardrail_mode="strict",
    )
    agent.react = _EmptyReact()  # type: ignore[assignment]

    with pytest.raises(ValueError, match="empty assistant response"):
        agent.forward(user_request="hello")


def test_chat_turn_warn_mode_includes_guardrail_warnings(monkeypatch):
    class _ShortReact:
        def __call__(self, **kwargs):
            return SimpleNamespace(assistant_response="ok", trajectory={})

    agent = RLMReActChatAgent(
        interpreter=FakeInterpreter(),
        guardrail_mode="warn",
        min_substantive_chars=20,
    )
    agent.react = _ShortReact()  # type: ignore[assignment]

    result = agent.chat_turn("hello")
    assert result["assistant_response"] == "ok"
    assert result["guardrail_warnings"]
    assert any("brief" in warning for warning in result["guardrail_warnings"])


def test_forward_warn_mode_reports_tool_error_trajectory(monkeypatch):
    class _ToolErrorReact:
        def __call__(self, **kwargs):
            return SimpleNamespace(
                assistant_response="This is a sufficiently detailed response.",
                trajectory={"steps": [{"index": 0, "output": "RuntimeError: boom"}]},
            )

    agent = RLMReActChatAgent(
        interpreter=FakeInterpreter(),
        guardrail_mode="warn",
    )
    agent.react = _ToolErrorReact()  # type: ignore[assignment]

    prediction = agent.forward(user_request="hello")
    warnings = list(getattr(prediction, "guardrail_warnings", []) or [])
    assert warnings
    assert any("tool error" in warning for warning in warnings)


@pytest.mark.asyncio
async def test_achat_turn_passes_core_memory_to_react(monkeypatch):
    agent = RLMReActChatAgent(interpreter=FakeInterpreter())
    captured: dict[str, object] = {}

    class _FakeAsyncReact:
        async def acall(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                assistant_response="echo:hello",
                trajectory={"tool_name_0": "finish"},
            )

    agent.react = _FakeAsyncReact()  # type: ignore[assignment]

    result = await agent.achat_turn("hello")
    assert result["assistant_response"] == "echo:hello"
    assert captured["user_request"] == "hello"
    captured_history = captured["history"]
    assert isinstance(captured_history, dspy.History)
    assert len(captured_history.messages) == 0
    assert len(agent.history.messages) == 1
    assert captured["core_memory"] == agent.fmt_core_memory()


def test_all_tools_are_dspy_tool_wrappers(monkeypatch):
    """All tools in react_tools should be dspy.Tool instances."""
    agent = RLMReActChatAgent(interpreter=FakeInterpreter())
    for tool in agent.react_tools:
        assert isinstance(tool, dspy.Tool), (
            f"Tool {tool} is {type(tool).__name__}, expected dspy.Tool"
        )
        assert tool.name, f"Tool {tool} has no name"
        assert tool.desc, f"Tool {tool.name} has no description"


def test_extra_tools_auto_wrapped_in_dspy_tool(monkeypatch):
    """Extra tools passed as raw callables should be auto-wrapped in dspy.Tool."""

    def my_custom_tool(x: str) -> str:
        """A custom helper."""
        return x.upper()

    agent = RLMReActChatAgent(
        interpreter=FakeInterpreter(),
        extra_tools=[my_custom_tool],
    )
    # Find the custom tool in the list (may not be last due to core_memory tools)
    tool_names = [
        getattr(t, "name", getattr(t, "__name__", None)) for t in agent.react_tools
    ]
    assert "my_custom_tool" in tool_names, f"Custom tool not found in {tool_names}"
    custom_tool = next(
        t
        for t in agent.react_tools
        if getattr(t, "name", getattr(t, "__name__", None)) == "my_custom_tool"
    )
    assert isinstance(custom_tool, dspy.Tool)


def test_extra_dspy_tool_not_double_wrapped(monkeypatch):
    """Extra tools that are already dspy.Tool should not be re-wrapped."""

    def raw_fn(x: str) -> str:
        return x

    pre_wrapped = dspy.Tool(raw_fn, name="pre_wrapped", desc="already wrapped")
    agent = RLMReActChatAgent(
        interpreter=FakeInterpreter(),
        extra_tools=[pre_wrapped],
    )
    # Find the pre_wrapped tool in the list (may not be last due to core_memory tools)
    tool_names = [
        getattr(t, "name", getattr(t, "__name__", None)) for t in agent.react_tools
    ]
    assert "pre_wrapped" in tool_names, f"Pre-wrapped tool not found in {tool_names}"
    found_tool = next(
        t
        for t in agent.react_tools
        if getattr(t, "name", getattr(t, "__name__", None)) == "pre_wrapped"
    )
    assert found_tool is pre_wrapped


def test_get_tool_returns_underlying_callable(monkeypatch):
    """_get_tool should return the underlying func from dspy.Tool wrappers."""
    agent = RLMReActChatAgent(interpreter=FakeInterpreter())
    tool_fn = agent._get_tool("load_document")
    assert callable(tool_fn)
    # Should be the unwrapped function, not the dspy.Tool wrapper
    assert not isinstance(tool_fn, dspy.Tool)


def test_get_tool_raises_on_unknown_name(monkeypatch):
    """_get_tool should raise AttributeError for unknown tool names."""
    agent = RLMReActChatAgent(interpreter=FakeInterpreter())
    with pytest.raises(AttributeError, match="nonexistent_tool"):
        agent._get_tool("nonexistent_tool")


def test_get_runtime_module_caches_instances(monkeypatch):
    from fleet_rlm.react import runtime_factory

    created: list[tuple[str, object, int, int, bool]] = []
    fake_module = object()

    def _fake_build_runtime_module(
        name: str,
        *,
        interpreter: object,
        max_iterations: int,
        max_llm_calls: int,
        verbose: bool,
    ) -> object:
        created.append((name, interpreter, max_iterations, max_llm_calls, verbose))
        return fake_module

    monkeypatch.setattr(
        runtime_factory, "build_runtime_module", _fake_build_runtime_module
    )

    agent = RLMReActChatAgent(interpreter=FakeInterpreter(), verbose=True)
    first = agent.get_runtime_module("grounded_answer")
    second = agent.get_runtime_module("grounded_answer")

    assert first is second is fake_module
    assert len(created) == 1
    assert created[0][0] == "grounded_answer"
    assert created[0][2:] == (
        agent.rlm_max_iterations,
        agent.rlm_max_llm_calls,
        True,
    )


def test_get_runtime_module_raises_on_unknown_name(monkeypatch):
    agent = RLMReActChatAgent(interpreter=FakeInterpreter())
    with pytest.raises(ValueError, match="Unknown runtime module: does_not_exist"):
        agent.get_runtime_module("does_not_exist")


def test_list_react_tool_names_handles_dspy_tool(monkeypatch):
    """list_react_tool_names should work with dspy.Tool wrappers."""
    agent = RLMReActChatAgent(interpreter=FakeInterpreter())
    names = react_tools.list_react_tool_names(agent.react_tools)
    assert isinstance(names, list)
    assert "load_document" in names
    assert "parallel_semantic_map" in names
    assert len(names) == len(agent.react_tools)


def test_register_extra_tool_rebuilds_react(monkeypatch):
    """register_extra_tool should rebuild self.react with the new tool."""
    agent = RLMReActChatAgent(interpreter=FakeInterpreter())
    initial_count = len(agent.react_tools)

    def new_tool(x: str) -> str:
        return x

    result = agent.register_extra_tool(new_tool)
    assert result["status"] == "ok"
    assert len(agent.react_tools) == initial_count + 1


def test_reset_clears_history_and_documents(monkeypatch):
    """reset() should clear history AND host-side document state."""
    agent = RLMReActChatAgent(interpreter=FakeInterpreter())
    agent.chat_turn("hello")
    assert len(agent.history.messages) == 1
    # Simulate a loaded document
    agent._document_cache["test.txt"] = "some content"
    agent._document_access_order.append("test.txt")
    agent.active_alias = "test.txt"

    result = agent.reset(clear_sandbox_buffers=False)
    assert result["status"] == "ok"
    assert result["history_turns"] == 0
    assert len(agent.history.messages) == 0
    # Verify documents are also cleared
    assert len(agent._document_cache) == 0
    assert len(agent._document_access_order) == 0
    assert agent.active_alias is None


# -----------------------------------------------------------------------
# Signature typed generics tests
# -----------------------------------------------------------------------


def test_signature_output_types_are_generic():
    """All Signature output fields should use typed generics, not bare list/dict."""
    import typing
    from fleet_rlm.react.signatures import (
        AnalyzeLongDocument,
        CodeChangePlan,
        ClarificationQuestionSignature,
        CoreMemoryUpdateProposal,
        ExtractFromLogs,
        GroundedAnswerWithCitations,
        IncidentTriageFromLogs,
        MemoryActionIntentSignature,
        MemoryStructureAuditSignature,
        MemoryStructureMigrationPlanSignature,
        SummarizeLongDocument,
        VolumeFileTreeSignature,
    )

    checks = [
        (AnalyzeLongDocument, "findings", list[str]),
        (SummarizeLongDocument, "key_points", list[str]),
        (ExtractFromLogs, "matches", list[str]),
        (ExtractFromLogs, "patterns", dict[str, str]),
        (GroundedAnswerWithCitations, "citations", list[dict[str, str]]),
        (IncidentTriageFromLogs, "probable_root_causes", list[str]),
        (IncidentTriageFromLogs, "recommended_actions", list[str]),
        (CodeChangePlan, "plan_steps", list[str]),
        (CodeChangePlan, "files_to_touch", list[str]),
        (CoreMemoryUpdateProposal, "keep", list[str]),
        (CoreMemoryUpdateProposal, "update", list[str]),
        (VolumeFileTreeSignature, "nodes", list[dict[str, str]]),
        (MemoryActionIntentSignature, "target_paths", list[str]),
        (MemoryStructureAuditSignature, "issues", list[str]),
        (MemoryStructureMigrationPlanSignature, "operations", list[dict[str, str]]),
        (ClarificationQuestionSignature, "questions", list[str]),
    ]

    hints = {}
    for sig_cls, field_name, expected_type in checks:
        if sig_cls not in hints:
            hints[sig_cls] = typing.get_type_hints(sig_cls)
        actual = hints[sig_cls].get(field_name)
        assert actual == expected_type, (
            f"{sig_cls.__name__}.{field_name}: expected {expected_type}, got {actual}"
        )
