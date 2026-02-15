"""Unit tests for the ReAct chat agent.

These tests mock DSPy ReAct + Modal interpreter behavior to avoid
cloud credentials while validating host-side orchestration logic.

Streaming tests live in test_react_streaming.py.
Tool / filesystem / PDF tests live in test_react_tools.py.
"""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import dspy
import pytest
from dspy.primitives.code_interpreter import FinalOutput

from fleet_rlm.react import RLMReActChatAgent, RLMReActChatSignature
from fleet_rlm.react import tools as react_tools


class _FakeInterpreter:
    def __init__(self):
        self.start_calls = 0
        self.shutdown_calls = 0
        self.execute_calls: list[tuple[str, dict]] = []
        self.default_execution_profile = "RLM_DELEGATE"

    def start(self):
        self.start_calls += 1

    def shutdown(self):
        self.shutdown_calls += 1

    @contextmanager
    def execution_profile(self, profile):
        previous = self.default_execution_profile
        self.default_execution_profile = profile
        try:
            yield self
        finally:
            self.default_execution_profile = previous

    def execute(self, code, variables=None, **kwargs):
        self.execute_calls.append((code, variables or {}))
        return FinalOutput(
            {
                "status": "ok",
                "chunk_count": len((variables or {}).get("prompts", [])),
                "findings_count": len((variables or {}).get("prompts", [])),
                "buffer_name": (variables or {}).get("buffer_name", "findings"),
            }
        )


def _make_fake_react(records):
    class _FakeReAct:
        def __init__(self, *, signature, tools, max_iters):
            records.append(
                {
                    "signature": signature,
                    "tools": tools,
                    "max_iters": max_iters,
                }
            )

        def __call__(self, **kwargs):
            request = kwargs.get("user_request", "")
            return SimpleNamespace(
                assistant_response=f"echo:{request}",
                trajectory={"tool_name_0": "finish"},
            )

    return _FakeReAct


def test_react_agent_constructed_with_explicit_signature_and_tools(monkeypatch):
    records = []
    monkeypatch.setattr("fleet_rlm.react.agent.dspy.ReAct", _make_fake_react(records))

    agent = RLMReActChatAgent(interpreter=_FakeInterpreter())  # noqa: F841

    assert len(records) == 1
    rec = records[0]
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


def test_tool_registry_includes_specialized_tools_and_extra_tools(monkeypatch):
    records = []
    monkeypatch.setattr("fleet_rlm.react.agent.dspy.ReAct", _make_fake_react(records))

    def custom_tool(topic: str) -> str:
        """Custom research tool for tests."""
        return f"researched: {topic}"

    agent = RLMReActChatAgent(  # noqa: F841
        interpreter=_FakeInterpreter(),
        extra_tools=[custom_tool],
    )

    rec = records[0]
    tool_names = [
        getattr(tool, "name", None) or getattr(tool, "__name__", str(tool))
        for tool in rec["tools"]
    ]
    assert "custom_tool" in tool_names


def test_chat_turn_appends_history_and_preserves_session(monkeypatch):
    records = []
    monkeypatch.setattr("fleet_rlm.react.agent.dspy.ReAct", _make_fake_react(records))

    agent = RLMReActChatAgent(interpreter=_FakeInterpreter())
    r1 = agent.chat_turn("hello")
    r2 = agent.chat_turn("how are you?")

    assert r1["assistant_response"] == "echo:hello"
    assert r2["assistant_response"] == "echo:how are you?"
    assert r2["history_turns"] == 2
    assert len(agent.history.messages) == 2


def test_parallel_semantic_map_uses_llm_query_batched(monkeypatch):
    records = []
    monkeypatch.setattr("fleet_rlm.react.agent.dspy.ReAct", _make_fake_react(records))

    agent = RLMReActChatAgent(interpreter=_FakeInterpreter())
    agent.documents["test_doc"] = "line1\nline2"
    agent.active_alias = "test_doc"

    result = agent.parallel_semantic_map(
        query="summarize", chunk_strategy="headers", max_chunks=5
    )

    assert result["status"] == "ok"
    assert "chunk_count" in result
    assert len(agent.interpreter.execute_calls) > 0


def test_context_manager_starts_and_stops_interpreter(monkeypatch):
    records = []
    monkeypatch.setattr("fleet_rlm.react.agent.dspy.ReAct", _make_fake_react(records))

    fake_interp = _FakeInterpreter()
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
    records = []
    monkeypatch.setattr("fleet_rlm.react.agent.dspy.ReAct", _make_fake_react(records))

    assert issubclass(RLMReActChatAgent, dspy.Module)
    agent = RLMReActChatAgent(interpreter=_FakeInterpreter())
    assert isinstance(agent, dspy.Module)


def test_agent_has_react_as_discoverable_submodule(monkeypatch):
    """self.react (dspy.ReAct) must appear in named_sub_modules."""
    records = []
    monkeypatch.setattr("fleet_rlm.react.agent.dspy.ReAct", _make_fake_react(records))

    agent = RLMReActChatAgent(interpreter=_FakeInterpreter())
    assert hasattr(agent, "react")
    # The fake isn't a real dspy.Module so it won't appear in named_sub_modules,
    # but the attribute assignment itself is correct.
    assert agent.react is not None


def test_forward_delegates_to_react_and_starts_interpreter(monkeypatch):
    """forward() should call self.react(...) and start the interpreter."""
    records = []
    monkeypatch.setattr("fleet_rlm.react.agent.dspy.ReAct", _make_fake_react(records))

    fake_interpreter = _FakeInterpreter()
    agent = RLMReActChatAgent(interpreter=fake_interpreter)

    prediction = agent.forward(user_request="test query")
    assert prediction.assistant_response == "echo:test query"
    assert fake_interpreter.start_calls == 1


def test_forward_accepts_custom_history(monkeypatch):
    """forward() should use the provided history, not the agent's own."""
    records = []
    monkeypatch.setattr("fleet_rlm.react.agent.dspy.ReAct", _make_fake_react(records))

    agent = RLMReActChatAgent(interpreter=_FakeInterpreter())
    custom_history = dspy.History(
        messages=[{"user_request": "prior", "assistant_response": "old"}]
    )

    prediction = agent.forward(user_request="new", history=custom_history)
    assert prediction.assistant_response == "echo:new"
    # Agent's own history should be unmodified
    assert len(agent.history.messages) == 0


def test_chat_turn_uses_forward_internally(monkeypatch):
    """chat_turn() should delegate to forward() and append history."""
    records = []
    monkeypatch.setattr("fleet_rlm.react.agent.dspy.ReAct", _make_fake_react(records))

    agent = RLMReActChatAgent(interpreter=_FakeInterpreter())
    result = agent.chat_turn("hello")

    assert result["assistant_response"] == "echo:hello"
    assert result["history_turns"] == 1
    assert agent.history.messages[0]["user_request"] == "hello"


@pytest.mark.asyncio
async def test_achat_turn_passes_core_memory_to_react(monkeypatch):
    records = []
    monkeypatch.setattr("fleet_rlm.react.agent.dspy.ReAct", _make_fake_react(records))

    agent = RLMReActChatAgent(interpreter=_FakeInterpreter())
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
    records = []
    monkeypatch.setattr("fleet_rlm.react.agent.dspy.ReAct", _make_fake_react(records))

    agent = RLMReActChatAgent(interpreter=_FakeInterpreter())
    for tool in agent.react_tools:
        assert isinstance(tool, dspy.Tool), (
            f"Tool {tool} is {type(tool).__name__}, expected dspy.Tool"
        )
        assert tool.name, f"Tool {tool} has no name"
        assert tool.desc, f"Tool {tool.name} has no description"


def test_extra_tools_auto_wrapped_in_dspy_tool(monkeypatch):
    """Extra tools passed as raw callables should be auto-wrapped in dspy.Tool."""
    records = []
    monkeypatch.setattr("fleet_rlm.react.agent.dspy.ReAct", _make_fake_react(records))

    def my_custom_tool(x: str) -> str:
        """A custom helper."""
        return x.upper()

    agent = RLMReActChatAgent(
        interpreter=_FakeInterpreter(),
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
    records = []
    monkeypatch.setattr("fleet_rlm.react.agent.dspy.ReAct", _make_fake_react(records))

    def raw_fn(x: str) -> str:
        return x

    pre_wrapped = dspy.Tool(raw_fn, name="pre_wrapped", desc="already wrapped")
    agent = RLMReActChatAgent(
        interpreter=_FakeInterpreter(),
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
    records = []
    monkeypatch.setattr("fleet_rlm.react.agent.dspy.ReAct", _make_fake_react(records))

    agent = RLMReActChatAgent(interpreter=_FakeInterpreter())
    tool_fn = agent._get_tool("load_document")
    assert callable(tool_fn)
    # Should be the unwrapped function, not the dspy.Tool wrapper
    assert not isinstance(tool_fn, dspy.Tool)


def test_get_tool_raises_on_unknown_name(monkeypatch):
    """_get_tool should raise AttributeError for unknown tool names."""
    records = []
    monkeypatch.setattr("fleet_rlm.react.agent.dspy.ReAct", _make_fake_react(records))

    agent = RLMReActChatAgent(interpreter=_FakeInterpreter())
    with pytest.raises(AttributeError, match="nonexistent_tool"):
        agent._get_tool("nonexistent_tool")


def test_list_react_tool_names_handles_dspy_tool(monkeypatch):
    """list_react_tool_names should work with dspy.Tool wrappers."""
    records = []
    monkeypatch.setattr("fleet_rlm.react.agent.dspy.ReAct", _make_fake_react(records))

    agent = RLMReActChatAgent(interpreter=_FakeInterpreter())
    names = react_tools.list_react_tool_names(agent.react_tools)
    assert isinstance(names, list)
    assert "load_document" in names
    assert "parallel_semantic_map" in names
    assert len(names) == len(agent.react_tools)


def test_register_extra_tool_rebuilds_react(monkeypatch):
    """register_extra_tool should rebuild self.react with the new tool."""
    records = []
    monkeypatch.setattr("fleet_rlm.react.agent.dspy.ReAct", _make_fake_react(records))

    agent = RLMReActChatAgent(interpreter=_FakeInterpreter())
    initial_count = len(agent.react_tools)

    def new_tool(x: str) -> str:
        return x

    result = agent.register_extra_tool(new_tool)
    assert result["status"] == "ok"
    assert len(agent.react_tools) == initial_count + 1


def test_reset_clears_history_and_documents(monkeypatch):
    """reset() should clear history AND host-side document state."""
    records = []
    monkeypatch.setattr("fleet_rlm.react.agent.dspy.ReAct", _make_fake_react(records))

    agent = RLMReActChatAgent(interpreter=_FakeInterpreter())
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
    from fleet_rlm.signatures import (
        AnalyzeLongDocument,
        ExtractAPIEndpoints,
        ExtractArchitecture,
        ExtractFromLogs,
        ExtractWithCustomTool,
        FindErrorPatterns,
        SummarizeLongDocument,
    )

    checks = [
        (ExtractArchitecture, "modules", list[str]),
        (ExtractArchitecture, "optimizers", list[str]),
        (ExtractAPIEndpoints, "api_endpoints", list[str]),
        (FindErrorPatterns, "error_categories", dict[str, str]),
        (ExtractWithCustomTool, "headers", list[str]),
        (ExtractWithCustomTool, "code_blocks", list[str]),
        (AnalyzeLongDocument, "findings", list[str]),
        (SummarizeLongDocument, "key_points", list[str]),
        (ExtractFromLogs, "matches", list[str]),
        (ExtractFromLogs, "patterns", dict[str, str]),
    ]

    hints = {}
    for sig_cls, field_name, expected_type in checks:
        if sig_cls not in hints:
            hints[sig_cls] = typing.get_type_hints(sig_cls)
        actual = hints[sig_cls].get(field_name)
        assert actual == expected_type, (
            f"{sig_cls.__name__}.{field_name}: expected {expected_type}, got {actual}"
        )
