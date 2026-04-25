"""Unit tests for simplified AgentRuntime.

Covers VAL-AGENT-007 through VAL-AGENT-010 from the validation contract:

- VAL-AGENT-007: Runtime manages interpreter, history, tools, memory
- VAL-AGENT-008: Runtime initialises agent with discovered tools
- VAL-AGENT-009: Runtime maintains chat history across turns
- VAL-AGENT-010: Runtime exposes core memory to tools
"""

from __future__ import annotations

from typing import Any

import dspy
import pytest

from fleet_rlm.runtime.agent.runtime import AgentRuntime


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_fake_react():
    """Return a fake dspy.ReAct class that records construction arguments."""

    class _FakeReAct:
        def __init__(self, *, signature, tools, max_iters, **kwargs):
            self.signature = signature
            self._tools = list(tools)
            self._max_iters = max_iters

        def __call__(self, **kwargs):
            return dspy.Prediction(response="fake_response")

    return _FakeReAct


def _tools_by_name(tools: list[Any]) -> dict[str, Any]:
    """Index raw callables or dspy.Tool wrappers by their exposed tool name."""
    return {
        getattr(tool, "name", None) or getattr(tool, "__name__", ""): tool
        for tool in tools
    }


class _FakeInterpreter:
    """Small Daytona-interpreter stand-in for runtime tool binding tests."""

    verbose = False
    _started = True
    rlm_max_iterations = 20
    sub_lm = None

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.shutdown_called = False
        self.child_build_calls: list[int] = []
        self.child_isolation_metadata = {"mode": "test", "strategy": "fake"}

    def execute(
        self,
        code: str,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = dict(variables or {})
        self.calls.append((code, payload))
        return {"code": code, "variables": payload}

    def shutdown(self) -> None:
        self.shutdown_called = True

    def _remaining_llm_budget(self) -> int:
        return 50

    def build_delegate_child(self, *, remaining_llm_budget: int) -> "_FakeInterpreter":
        self.child_build_calls.append(remaining_llm_budget)
        return self

    def start(self) -> None:
        self._started = True


@pytest.fixture()
def mock_react(monkeypatch: pytest.MonkeyPatch):
    """Monkeypatch dspy.ReAct inside agent.py to avoid real LLM calls."""
    FakeReAct = _make_fake_react()
    monkeypatch.setattr(
        "fleet_rlm.runtime.agent.agent.dspy.ReAct",
        FakeReAct,
    )
    return FakeReAct


@pytest.fixture()
def fake_tools():
    """Return two simple callable tools for testing."""

    def tool_alpha(query: str) -> str:
        """Tool alpha for testing."""
        return f"alpha:{query}"

    def tool_beta(query: str) -> str:
        """Tool beta for testing."""
        return f"beta:{query}"

    return [tool_alpha, tool_beta]


@pytest.fixture()
def runtime(mock_react, monkeypatch: pytest.MonkeyPatch, fake_tools):
    """Construct an AgentRuntime with mocked discover_tools and LLM."""
    monkeypatch.setattr(
        "fleet_rlm.runtime.agent.runtime.discover_tools",
        lambda: list(fake_tools),
    )
    return AgentRuntime()


# ---------------------------------------------------------------------------
# VAL-AGENT-007: Runtime manages interpreter, history, tools, memory
# ---------------------------------------------------------------------------


class TestRuntimeHoldsState:
    """VAL-AGENT-007: AgentRuntime owns agent, interpreter, history, tools, memory."""

    def test_has_agent_attribute(self, runtime: AgentRuntime) -> None:
        assert hasattr(runtime, "agent")
        assert runtime.agent is not None

    def test_has_interpreter_attribute(self, runtime: AgentRuntime) -> None:
        assert hasattr(runtime, "interpreter")

    def test_interpreter_defaults_to_none(self, runtime: AgentRuntime) -> None:
        assert runtime.interpreter is None

    def test_interpreter_can_be_set(
        self, mock_react, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "fleet_rlm.runtime.agent.runtime.discover_tools",
            lambda: [],
        )
        fake_interp = object()
        rt = AgentRuntime(interpreter=fake_interp)
        assert rt.interpreter is fake_interp

    def test_has_history_attribute(self, runtime: AgentRuntime) -> None:
        assert hasattr(runtime, "history")

    def test_history_is_dspy_history(self, runtime: AgentRuntime) -> None:
        assert isinstance(runtime.history, dspy.History)

    def test_history_starts_empty(self, runtime: AgentRuntime) -> None:
        messages = list(getattr(runtime.history, "messages", []) or [])
        assert messages == []

    def test_has_tools_attribute(self, runtime: AgentRuntime) -> None:
        assert hasattr(runtime, "tools")
        assert isinstance(runtime.tools, list)

    def test_has_core_memory_attribute(self, runtime: AgentRuntime) -> None:
        assert hasattr(runtime, "core_memory")
        assert isinstance(runtime.core_memory, dict)

    def test_core_memory_is_non_empty_by_default(self, runtime: AgentRuntime) -> None:
        assert len(runtime.core_memory) > 0


# ---------------------------------------------------------------------------
# VAL-AGENT-008: Runtime initialises agent with discovered tools
# ---------------------------------------------------------------------------


class TestAgentInitWithDiscoveredTools:
    """VAL-AGENT-008: AgentRuntime discovers tools and passes them to FleetAgent."""

    def test_discover_tools_called_on_init(
        self, mock_react, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[int] = []

        def _fake_discover() -> list[Any]:
            calls.append(1)
            return []

        monkeypatch.setattr(
            "fleet_rlm.runtime.agent.runtime.discover_tools", _fake_discover
        )
        AgentRuntime()
        assert calls == [1], "discover_tools() must be called exactly once during init"

    def test_tools_list_contains_discovered_tools(
        self, mock_react, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def sentinel_tool(x: str) -> str:
            """Sentinel tool."""
            return x

        monkeypatch.setattr(
            "fleet_rlm.runtime.agent.runtime.discover_tools",
            lambda: [sentinel_tool],
        )
        rt = AgentRuntime()
        assert sentinel_tool in rt.tools

    def test_extra_tools_appended_to_discovered(
        self, mock_react, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def base_tool(x: str) -> str:
            """Base tool."""
            return x

        def extra_tool(x: str) -> str:
            """Extra tool."""
            return x

        monkeypatch.setattr(
            "fleet_rlm.runtime.agent.runtime.discover_tools",
            lambda: [base_tool],
        )
        rt = AgentRuntime(extra_tools=[extra_tool])
        assert base_tool in rt.tools
        assert extra_tool in rt.tools

    def test_agent_constructed_with_tools(
        self, mock_react, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def my_tool(x: str) -> str:
            """My tool."""
            return x

        monkeypatch.setattr(
            "fleet_rlm.runtime.agent.runtime.discover_tools",
            lambda: [my_tool],
        )
        rt = AgentRuntime()
        # Agent exists and was built with the tool
        assert rt.agent is not None
        assert len(rt.tools) >= 1

    def test_interpreter_tools_omitted_without_interpreter(
        self, mock_react, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fleet_rlm.runtime.tools.buffer_tools import read_buffer
        from fleet_rlm.runtime.tools.memory_tools import (
            read_core_memory,
            write_core_memory,
        )
        from fleet_rlm.runtime.tools.rlm_delegate import (
            delegate_to_rlm,
            delegate_to_rlm_batched,
        )
        from fleet_rlm.runtime.tools.sandbox_tools import execute_code

        monkeypatch.setattr(
            "fleet_rlm.runtime.agent.runtime.discover_tools",
            lambda: [
                delegate_to_rlm,
                delegate_to_rlm_batched,
                execute_code,
                read_buffer,
                read_core_memory,
                write_core_memory,
            ],
        )

        rt = AgentRuntime()
        tools = _tools_by_name(rt.tools)

        assert "delegate_to_rlm" not in tools
        assert "delegate_to_rlm_batched" not in tools
        assert "execute_code" not in tools
        assert "read_buffer" not in tools
        assert "read_core_memory" in tools
        assert "write_core_memory" in tools
        assert tools["write_core_memory"](key="topic", value="runtime") == {
            "status": "ok",
            "key": "topic",
            "value": "runtime",
        }
        assert tools["read_core_memory"](key="topic") == {
            "status": "ok",
            "key": "topic",
            "value": "runtime",
        }

    def test_interpreter_tools_are_bound_to_runtime_backends(
        self, mock_react, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fleet_rlm.runtime.tools.buffer_tools import (
            clear_buffer,
            read_buffer,
            write_buffer,
        )
        from fleet_rlm.runtime.tools.memory_tools import (
            read_core_memory,
            write_core_memory,
        )
        from fleet_rlm.runtime.tools.rlm_delegate import (
            delegate_to_rlm,
            delegate_to_rlm_batched,
        )
        from fleet_rlm.runtime.tools.sandbox_tools import execute_code

        monkeypatch.setattr(
            "fleet_rlm.runtime.agent.runtime.discover_tools",
            lambda: [
                clear_buffer,
                delegate_to_rlm,
                delegate_to_rlm_batched,
                execute_code,
                read_buffer,
                read_core_memory,
                write_buffer,
                write_core_memory,
            ],
        )
        monkeypatch.setattr(
            "fleet_rlm.runtime.tools.rlm_delegate.build_recursive_subquery_rlm",
            lambda **kwargs: (
                lambda prompt, context: dspy.Prediction(answer=f"{prompt}:{context}")
            ),
        )
        interpreter = _FakeInterpreter()

        rt = AgentRuntime(interpreter=interpreter)
        tools = _tools_by_name(rt.tools)

        assert set(tools) == {
            "clear_buffer",
            "delegate_to_rlm",
            "delegate_to_rlm_batched",
            "execute_code",
            "read_buffer",
            "read_core_memory",
            "write_buffer",
            "write_core_memory",
        }
        assert rt.react_tools is rt.tools
        assert tools["execute_code"](code="SUBMIT(value=1)") == {
            "status": "ok",
            "code": "SUBMIT(value=1)",
            "variables": {},
        }
        assert tools["write_buffer"](name="notes", content="hello")["status"] == "ok"
        assert tools["read_buffer"](name="notes")["variables"] == {
            "buffer_name": "notes"
        }
        assert tools["clear_buffer"](name="notes")["status"] == "ok"
        assert tools["write_core_memory"](key="phase", value="bound")["status"] == "ok"
        assert tools["read_core_memory"](key="phase")["value"] == "bound"
        assert tools["delegate_to_rlm"](query="q", context="c") == {
            "status": "ok",
            "answer": "q:c",
        }
        assert tools["delegate_to_rlm_batched"](queries=["q1", "q2"], context="c") == {
            "status": "ok",
            "results": [
                {"query": "q1", "answer": "q1:c"},
                {"query": "q2", "answer": "q2:c"},
            ],
        }

    def test_max_iters_forwarded_to_agent(
        self, mock_react, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "fleet_rlm.runtime.agent.runtime.discover_tools",
            lambda: [],
        )
        rt = AgentRuntime(max_iters=25)
        # FleetAgent stores max_iters on its react submodule
        assert rt.agent.react._max_iters == 25


# ---------------------------------------------------------------------------
# VAL-AGENT-009: Runtime maintains chat history across turns
# ---------------------------------------------------------------------------


class TestHistoryAccumulationAcrossTurns:
    """VAL-AGENT-009: Multiple chat calls accumulate history; agent sees prior turns."""

    def test_single_turn_stored_in_history(self, runtime: AgentRuntime) -> None:
        runtime.agent.forward = lambda *, chat_history, user_message: dspy.Prediction(
            response="reply1"
        )
        runtime.chat_turn("message1")
        messages = list(getattr(runtime.history, "messages", []) or [])
        assert len(messages) == 1
        assert messages[0]["user_message"] == "message1"
        assert messages[0]["response"] == "reply1"

    def test_second_turn_sees_first_turn_in_history(
        self, runtime: AgentRuntime
    ) -> None:
        seen_histories: list[list[Any]] = []

        def _fake_forward(
            *, chat_history: dspy.History, user_message: str
        ) -> dspy.Prediction:
            seen_histories.append(list(getattr(chat_history, "messages", []) or []))
            return dspy.Prediction(response=f"response_to_{user_message}")

        runtime.agent.forward = _fake_forward

        runtime.chat_turn("first")
        runtime.chat_turn("second")

        assert len(seen_histories) == 2
        # First call: empty history
        assert len(seen_histories[0]) == 0
        # Second call: one prior turn
        assert len(seen_histories[1]) == 1
        assert seen_histories[1][0]["user_message"] == "first"

    def test_history_accumulates_across_three_turns(
        self, runtime: AgentRuntime
    ) -> None:
        runtime.agent.forward = lambda *, chat_history, user_message: dspy.Prediction(
            response=f"r:{user_message}"
        )

        runtime.chat_turn("turn1")
        runtime.chat_turn("turn2")
        runtime.chat_turn("turn3")

        messages = list(getattr(runtime.history, "messages", []) or [])
        assert len(messages) == 3
        assert [m["user_message"] for m in messages] == ["turn1", "turn2", "turn3"]

    def test_history_max_turns_enforced(
        self, mock_react, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "fleet_rlm.runtime.agent.runtime.discover_tools",
            lambda: [],
        )
        rt = AgentRuntime(history_max_turns=2)
        rt.agent.forward = lambda *, chat_history, user_message: dspy.Prediction(
            response="r"
        )

        rt.chat_turn("a")
        rt.chat_turn("b")
        rt.chat_turn("c")

        messages = list(getattr(rt.history, "messages", []) or [])
        # Only last 2 turns are kept
        assert len(messages) == 2
        assert messages[-1]["user_message"] == "c"

    def test_chat_turn_returns_prediction(self, runtime: AgentRuntime) -> None:
        runtime.agent.forward = lambda *, chat_history, user_message: dspy.Prediction(
            response="ok"
        )
        result = runtime.chat_turn("test")
        assert isinstance(result, dspy.Prediction)
        assert result.response == "ok"

    @pytest.mark.asyncio
    async def test_stream_turn_suppresses_success_after_cancellation(
        self, runtime: AgentRuntime
    ) -> None:
        cancelled = False

        def _fake_forward(
            *, chat_history: dspy.History, user_message: str
        ) -> dspy.Prediction:
            _ = chat_history, user_message
            nonlocal cancelled
            cancelled = True
            return dspy.Prediction(response="late response")

        runtime.agent.forward = _fake_forward

        events = [
            event
            async for event in runtime.aiter_chat_turn_stream(
                "cancel me",
                cancel_check=lambda: cancelled,
            )
        ]

        assert [event.kind for event in events] == ["status", "done"]
        assert events[-1].text == "[cancelled]"
        assert events[-1].payload == {"cancelled": True, "history_turns": 0}
        assert runtime.history_turns() == 0


# ---------------------------------------------------------------------------
# VAL-AGENT-010: Core memory accessible by tools
# ---------------------------------------------------------------------------


class TestCoreMemoryAccessibility:
    """VAL-AGENT-010: Core memory is accessible by tool functions; tools can read/write."""

    def test_core_memory_is_dict(self, runtime: AgentRuntime) -> None:
        assert isinstance(runtime.core_memory, dict)

    def test_get_core_memory_returns_dict(self, runtime: AgentRuntime) -> None:
        memory = runtime.get_core_memory()
        assert isinstance(memory, dict)

    def test_get_core_memory_returns_same_object(self, runtime: AgentRuntime) -> None:
        memory = runtime.get_core_memory()
        assert memory is runtime.core_memory

    def test_set_core_memory_key_stores_value(self, runtime: AgentRuntime) -> None:
        runtime.set_core_memory_key("task", "write unit tests")
        assert runtime.core_memory["task"] == "write unit tests"

    def test_get_core_memory_key_reads_value(self, runtime: AgentRuntime) -> None:
        runtime.core_memory["context"] = "python project"
        assert runtime.get_core_memory_key("context") == "python project"

    def test_get_core_memory_key_returns_none_for_missing(
        self, runtime: AgentRuntime
    ) -> None:
        assert runtime.get_core_memory_key("nonexistent_key_xyz") is None

    def test_tool_can_write_to_core_memory(self, runtime: AgentRuntime) -> None:
        """Simulate a tool writing to core memory via the runtime."""

        def _tool_write(rt: AgentRuntime, key: str, value: str) -> str:
            rt.set_core_memory_key(key, value)
            return f"wrote {key}={value}"

        _tool_write(runtime, "note", "important info")
        assert runtime.core_memory["note"] == "important info"

    def test_tool_can_read_from_core_memory(self, runtime: AgentRuntime) -> None:
        """Simulate a tool reading from core memory via the runtime."""
        runtime.core_memory["agent_name"] = "FleetBot"

        def _tool_read(rt: AgentRuntime, key: str) -> str | None:
            return rt.get_core_memory_key(key)

        result = _tool_read(runtime, "agent_name")
        assert result == "FleetBot"

    def test_core_memory_persists_across_chat_turns(
        self, runtime: AgentRuntime
    ) -> None:
        runtime.agent.forward = lambda *, chat_history, user_message: dspy.Prediction(
            response="ok"
        )
        runtime.set_core_memory_key("persistent", "value")
        runtime.chat_turn("hello")
        # Core memory should survive chat turns
        assert runtime.get_core_memory_key("persistent") == "value"

    def test_core_memory_defaults_have_expected_keys(
        self, runtime: AgentRuntime
    ) -> None:
        """Default core memory blocks are present."""
        assert "persona" in runtime.core_memory
        assert "human" in runtime.core_memory
        assert "scratchpad" in runtime.core_memory


# ---------------------------------------------------------------------------
# VAL-BACKEND-RUNTIME-003: reset() respects clear_sandbox_buffers parameter
# ---------------------------------------------------------------------------


class TestResetClearsSandboxBuffers:
    """VAL-BACKEND-RUNTIME-003: reset(clear_sandbox_buffers=...) actually clears buffers."""

    def test_reset_clear_sandbox_buffers_true_clears_buffers(
        self, mock_react, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "fleet_rlm.runtime.agent.runtime.discover_tools",
            lambda: [],
        )
        interpreter = _FakeInterpreter()
        rt = AgentRuntime(interpreter=interpreter)

        result = rt.reset(clear_sandbox_buffers=True)

        assert result["status"] == "ok"
        assert result["buffers_cleared"] is True
        # The interpreter should have received a clear_buffer call
        assert any("clear_buffer" in code for code, _vars in interpreter.calls)

    def test_reset_clear_sandbox_buffers_false_preserves_buffers(
        self, mock_react, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "fleet_rlm.runtime.agent.runtime.discover_tools",
            lambda: [],
        )
        interpreter = _FakeInterpreter()
        rt = AgentRuntime(interpreter=interpreter)

        result = rt.reset(clear_sandbox_buffers=False)

        assert result["status"] == "ok"
        assert result["buffers_cleared"] is False
        # The interpreter should NOT have received a clear_buffer call
        assert not any("clear_buffer" in code for code, _vars in interpreter.calls)

    def test_reset_without_interpreter_ignores_clear_flag(
        self, mock_react, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "fleet_rlm.runtime.agent.runtime.discover_tools",
            lambda: [],
        )
        rt = AgentRuntime(interpreter=None)

        result = rt.reset(clear_sandbox_buffers=True)

        assert result["status"] == "ok"
        assert result["buffers_cleared"] is True
        # No crash despite missing interpreter

    def test_reset_clears_history(
        self, mock_react, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "fleet_rlm.runtime.agent.runtime.discover_tools",
            lambda: [],
        )
        rt = AgentRuntime()
        rt.history = dspy.History(
            messages=[{"user_message": "hi", "response": "hello"}]
        )

        rt.reset(clear_sandbox_buffers=False)

        messages = list(getattr(rt.history, "messages", []) or [])
        assert messages == []
