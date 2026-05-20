"""Unit tests for simplified AgentRuntime.

Covers VAL-AGENT-007 through VAL-AGENT-010 from the validation contract:

- VAL-AGENT-007: Runtime manages interpreter, history, tools, memory
- VAL-AGENT-008: Runtime initialises agent with discovered tools
- VAL-AGENT-009: Runtime maintains chat history across turns
- VAL-AGENT-010: Runtime exposes core memory to tools
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import dspy
import pytest
from dspy.streaming import StreamResponse

from fleet_rlm.runtime.agent.runtime import AgentRuntime
from fleet_rlm.runtime.tools.binding import INTERPRETER_TOOL_NAMES

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
    return {getattr(tool, "name", None) or getattr(tool, "__name__", ""): tool for tool in tools}


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


class _FakeAsyncTool:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    async def acall(self, **kwargs: Any) -> Any:
        self.calls.append(dict(kwargs))
        return self.result


class _FakeStreamableReactProgram:
    def __init__(
        self,
        *,
        planner_predictions: list[dspy.Prediction],
        tools: dict[str, Any] | None = None,
    ) -> None:
        self.react = object()
        self.extract = SimpleNamespace(predict=object())
        self.max_iters = len(planner_predictions)
        self.tools = dict(tools or {})
        self._planner_predictions = list(planner_predictions)
        self.formatted_trajectories: list[dict[str, Any]] = []

    def _format_trajectory(self, trajectory: dict[str, Any]) -> str:
        self.formatted_trajectories.append(dict(trajectory))
        return str(trajectory)

    async def _async_call_with_potential_trajectory_truncation(
        self,
        module: Any,
        trajectory: dict[str, Any],
        **input_args: Any,
    ) -> dspy.Prediction:
        _ = trajectory, input_args
        assert module is self.react
        return self._planner_predictions.pop(0)


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

    def test_initial_state(self, runtime: AgentRuntime) -> None:
        assert hasattr(runtime, "agent")
        assert runtime.agent is not None
        assert hasattr(runtime, "interpreter")
        assert runtime.interpreter is None
        assert hasattr(runtime, "history")
        assert isinstance(runtime.history, dspy.History)
        messages = list(getattr(runtime.history, "messages", []) or [])
        assert messages == []
        assert hasattr(runtime, "tools")
        assert isinstance(runtime.tools, list)
        assert hasattr(runtime, "core_memory")
        assert isinstance(runtime.core_memory, dict)
        assert len(runtime.core_memory) > 0

    def test_interpreter_can_be_set(self, mock_react, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "fleet_rlm.runtime.agent.runtime.discover_tools",
            lambda: [],
        )
        fake_interp = object()
        rt = AgentRuntime(interpreter=fake_interp)
        assert rt.interpreter is fake_interp

    def test_shutdown_runs_async_interpreter_cleanup(
        self,
        mock_react,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "fleet_rlm.runtime.agent.runtime.discover_tools",
            lambda: [],
        )

        class _AsyncOnlyInterpreter:
            def __init__(self) -> None:
                self.shutdown_called = False

            async def ashutdown(self) -> None:
                self.shutdown_called = True

        interpreter = _AsyncOnlyInterpreter()
        runtime = AgentRuntime(interpreter=interpreter)

        runtime.shutdown()

        assert interpreter.shutdown_called is True


# ---------------------------------------------------------------------------
# VAL-AGENT-008: Runtime initialises agent with discovered tools
# ---------------------------------------------------------------------------


class TestAgentInitWithDiscoveredTools:
    """VAL-AGENT-008: AgentRuntime discovers tools and passes them to FleetAgent."""

    def test_discover_tools_called_on_init(self, mock_react, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[int] = []

        def _fake_discover() -> list[Any]:
            calls.append(1)
            return []

        monkeypatch.setattr("fleet_rlm.runtime.agent.runtime.discover_tools", _fake_discover)
        AgentRuntime()
        assert calls == [1], "discover_tools() must be called exactly once during init"

    def test_tools_list_contains_discovered_tools(self, mock_react, monkeypatch: pytest.MonkeyPatch) -> None:
        def sentinel_tool(x: str) -> str:
            """Sentinel tool."""
            return x

        monkeypatch.setattr(
            "fleet_rlm.runtime.agent.runtime.discover_tools",
            lambda: [sentinel_tool],
        )
        rt = AgentRuntime()
        assert sentinel_tool in rt.tools

    def test_extra_tools_appended_to_discovered(self, mock_react, monkeypatch: pytest.MonkeyPatch) -> None:
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

    def test_agent_constructed_with_tools(self, mock_react, monkeypatch: pytest.MonkeyPatch) -> None:
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

    def test_interpreter_tools_omitted_without_interpreter(self, mock_react, monkeypatch: pytest.MonkeyPatch) -> None:
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

    def test_runtime_binding_declares_all_interpreter_only_tools(self) -> None:
        assert INTERPRETER_TOOL_NAMES == {
            "clear_buffer",
            "delegate_to_rlm",
            "delegate_to_rlm_batched",
            "execute_code",
            "read_buffer",
            "recursive_workspace",
            "sandbox_create_directory",
            "sandbox_delete_file",
            "sandbox_find_in_files",
            "sandbox_get_file_info",
            "sandbox_list_files",
            "sandbox_move_file",
            "sandbox_read_file",
            "sandbox_replace_in_files",
            "sandbox_search_files",
            "sandbox_write_file",
            "write_buffer",
        }

    def test_interpreter_tools_are_bound_to_runtime_backends(self, mock_react, monkeypatch: pytest.MonkeyPatch) -> None:
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
            lambda **kwargs: lambda prompt, context: dspy.Prediction(answer=f"{prompt}:{context}"),
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
        assert tools["read_buffer"](name="notes")["variables"] == {"buffer_name": "notes"}
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

    def test_max_iters_forwarded_to_agent(self, mock_react, monkeypatch: pytest.MonkeyPatch) -> None:
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
        runtime.agent.forward = lambda *, chat_history, user_message: dspy.Prediction(response="reply1")
        runtime.chat_turn("message1")
        messages = list(getattr(runtime.history, "messages", []) or [])
        assert len(messages) == 1
        assert messages[0]["user_message"] == "message1"
        assert messages[0]["response"] == "reply1"

    def test_second_turn_sees_first_turn_in_history(self, runtime: AgentRuntime) -> None:
        seen_histories: list[list[Any]] = []

        def _fake_forward(*, chat_history: dspy.History, user_message: str) -> dspy.Prediction:
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

    def test_history_accumulates_across_three_turns(self, runtime: AgentRuntime) -> None:
        runtime.agent.forward = lambda *, chat_history, user_message: dspy.Prediction(response=f"r:{user_message}")

        runtime.chat_turn("turn1")
        runtime.chat_turn("turn2")
        runtime.chat_turn("turn3")

        messages = list(getattr(runtime.history, "messages", []) or [])
        assert len(messages) == 3
        assert [m["user_message"] for m in messages] == ["turn1", "turn2", "turn3"]

    def test_history_max_turns_enforced(self, mock_react, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "fleet_rlm.runtime.agent.runtime.discover_tools",
            lambda: [],
        )
        rt = AgentRuntime(history_max_turns=2)
        rt.agent.forward = lambda *, chat_history, user_message: dspy.Prediction(response="r")

        rt.chat_turn("a")
        rt.chat_turn("b")
        rt.chat_turn("c")

        messages = list(getattr(rt.history, "messages", []) or [])
        # Only last 2 turns are kept
        assert len(messages) == 2
        assert messages[-1]["user_message"] == "c"

    def test_chat_turn_returns_prediction(self, runtime: AgentRuntime) -> None:
        runtime.agent.forward = lambda *, chat_history, user_message: dspy.Prediction(response="ok")
        result = runtime.chat_turn("test")
        assert isinstance(result, dspy.Prediction)
        assert result.response == "ok"

    @pytest.mark.asyncio
    async def test_stream_turn_suppresses_success_after_cancellation(self, runtime: AgentRuntime) -> None:
        cancelled = False

        def _fake_forward(*, chat_history: dspy.History, user_message: str) -> dspy.Prediction:
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

    @pytest.mark.asyncio
    async def test_stream_turn_emits_streamed_response_tokens(
        self,
        runtime: AgentRuntime,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fake_react_program = _FakeStreamableReactProgram(
            planner_predictions=[
                dspy.Prediction(
                    next_thought="I can answer directly.",
                    next_tool_name="finish",
                    next_tool_args={},
                )
            ]
        )
        monkeypatch.setattr(
            "fleet_rlm.runtime.agent.runtime._get_streamable_react_program",
            lambda _program: fake_react_program,
        )

        # For finish-only trajectories the fast path skips the extract LLM
        # call and emits the planner thought as the response directly.
        events = [event async for event in runtime.aiter_chat_turn_stream("hello")]

        assert [event.kind for event in events] == ["status", "reasoning", "text", "done"]
        assert events[1].text == "I can answer directly."
        # Response is the thought itself (no extract call)
        assert events[2].text == "I can answer directly."
        assert events[-1].text == "I can answer directly."
        assert runtime.history_turns() == 1

    @pytest.mark.asyncio
    async def test_stream_turn_emits_live_tool_events_from_status_messages(
        self,
        runtime: AgentRuntime,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        tool = _FakeAsyncTool({"status": "ok", "answer": "Delegated answer"})
        fake_react_program = _FakeStreamableReactProgram(
            planner_predictions=[
                dspy.Prediction(
                    next_thought="I should delegate this task.",
                    next_tool_name="delegate_to_rlm",
                    next_tool_args={"query": "test"},
                ),
                dspy.Prediction(
                    next_thought="I have the delegated result.",
                    next_tool_name="finish",
                    next_tool_args={},
                ),
            ],
            tools={"delegate_to_rlm": tool},
        )
        monkeypatch.setattr(
            "fleet_rlm.runtime.agent.runtime._get_streamable_react_program",
            lambda _program: fake_react_program,
        )

        def _fake_streamify(*args: Any, **kwargs: Any):
            _ = args, kwargs

            async def _runner(**input_args: Any):
                _ = input_args
                yield StreamResponse("predict_response", "response", "Done", True)
                yield dspy.Prediction(response="Done", reasoning="")

            return _runner

        monkeypatch.setattr("fleet_rlm.runtime.agent.runtime.dspy.streamify", _fake_streamify)

        events = [event async for event in runtime.aiter_chat_turn_stream("delegate this")]

        assert [event.kind for event in events] == [
            "status",
            "reasoning",
            "tool_call",
            "tool_result",
            "reasoning",
            "text",
            "done",
        ]
        assert events[2].payload == {
            "tool_name": "delegate_to_rlm",
            "tool_input": "{'query': 'test'}",
            "tool_args": {"query": "test"},
            "step_index": 0,
        }
        assert events[3].payload == {
            "tool_name": "delegate_to_rlm",
            "tool_output": "{'status': 'ok', 'answer': 'Delegated answer'}",
            "step_index": 0,
        }
        assert tool.calls == [{"query": "test"}]

    @pytest.mark.asyncio
    async def test_stream_turn_marks_degraded_delegate_result_for_human_review(
        self,
        runtime: AgentRuntime,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        tool = _FakeAsyncTool(
            {
                "status": "needs_human_review",
                "answer": "Usable but degraded answer",
                "degraded": True,
                "reason": "broker_unavailable",
                "error": "Daytona broker unavailable during child RLM execution.",
                "degradation_reason": "broker_unavailable",
                "degradation_error": "Daytona broker unavailable during child RLM execution.",
                "duration_ms": 123,
            }
        )
        fake_react_program = _FakeStreamableReactProgram(
            planner_predictions=[
                dspy.Prediction(
                    next_thought="I should delegate this task.",
                    next_tool_name="delegate_to_rlm",
                    next_tool_args={"query": "test"},
                ),
                dspy.Prediction(
                    next_thought="I have a degraded delegated result.",
                    next_tool_name="finish",
                    next_tool_args={},
                ),
            ],
            tools={"delegate_to_rlm": tool},
        )
        monkeypatch.setattr(
            "fleet_rlm.runtime.agent.runtime._get_streamable_react_program",
            lambda _program: fake_react_program,
        )

        def _fake_streamify(*args: Any, **kwargs: Any):
            _ = args, kwargs

            async def _runner(**input_args: Any):
                _ = input_args
                yield StreamResponse("predict_response", "response", "Done", True)
                yield dspy.Prediction(response="Done", reasoning="")

            return _runner

        monkeypatch.setattr("fleet_rlm.runtime.agent.runtime.dspy.streamify", _fake_streamify)

        events = [event async for event in runtime.aiter_chat_turn_stream("delegate this")]

        done_payload = events[-1].payload
        assert done_payload["human_review"]["required"] is True
        assert done_payload["human_review"]["repair_mode"] == "needs_human_review"
        assert done_payload["runtime_degraded"] is True
        assert done_payload["runtime_failure_category"] == "recursive_child_degraded"
        assert done_payload["runtime_failure_phase"] == "delegate_to_rlm"

    @pytest.mark.asyncio
    async def test_stream_turn_preserves_clarification_events_from_final_trajectory(
        self,
        runtime: AgentRuntime,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        tool = _FakeAsyncTool(
            {
                "status": "clarification_needed",
                "question": "Which repository?",
                "options": ["frontend", "backend"],
            }
        )
        fake_react_program = _FakeStreamableReactProgram(
            planner_predictions=[
                dspy.Prediction(
                    next_thought="I need clarification.",
                    next_tool_name="clarification_questions",
                    next_tool_args={},
                )
            ],
            tools={"clarification_questions": tool},
        )
        monkeypatch.setattr(
            "fleet_rlm.runtime.agent.runtime._get_streamable_react_program",
            lambda _program: fake_react_program,
        )

        def _fake_streamify(*args: Any, **kwargs: Any):
            _ = args, kwargs

            async def _runner(**input_args: Any):
                _ = input_args
                yield dspy.Prediction(response="", reasoning="")

            return _runner

        monkeypatch.setattr("fleet_rlm.runtime.agent.runtime.dspy.streamify", _fake_streamify)

        events = [event async for event in runtime.aiter_chat_turn_stream("need clarification")]

        assert [event.kind for event in events] == [
            "status",
            "reasoning",
            "tool_call",
            "tool_result",
            "clarification",
            "done",
        ]
        assert events[4].text == "Which repository?"
        assert events[4].payload["options"] == ["frontend", "backend"]


# ---------------------------------------------------------------------------
# VAL-AGENT-010: Core memory accessible by tools
# ---------------------------------------------------------------------------


class TestCoreMemoryAccessibility:
    """VAL-AGENT-010: Core memory is accessible by tool functions; tools can read/write."""

    def test_core_memory_accessors(self, runtime: AgentRuntime) -> None:
        assert isinstance(runtime.core_memory, dict)
        memory = runtime.get_core_memory()
        assert isinstance(memory, dict)
        assert memory is runtime.core_memory
        runtime.set_core_memory_key("task", "write unit tests")
        assert runtime.core_memory["task"] == "write unit tests"
        runtime.core_memory["context"] = "python project"
        assert runtime.get_core_memory_key("context") == "python project"
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

    def test_core_memory_persists_across_chat_turns(self, runtime: AgentRuntime) -> None:
        runtime.agent.forward = lambda *, chat_history, user_message: dspy.Prediction(response="ok")
        runtime.set_core_memory_key("persistent", "value")
        runtime.chat_turn("hello")
        # Core memory should survive chat turns
        assert runtime.get_core_memory_key("persistent") == "value"

    def test_core_memory_defaults_have_expected_keys(self, runtime: AgentRuntime) -> None:
        """Default core memory blocks are present."""
        assert "persona" in runtime.core_memory
        assert "human" in runtime.core_memory
        assert "scratchpad" in runtime.core_memory


# ---------------------------------------------------------------------------
# VAL-BACKEND-RUNTIME-003: reset() respects clear_sandbox_buffers parameter
# ---------------------------------------------------------------------------


class TestResetClearsSandboxBuffers:
    """VAL-BACKEND-RUNTIME-003: reset(clear_sandbox_buffers=...) actually clears buffers."""

    def test_reset_behavior(self, mock_react, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "fleet_rlm.runtime.agent.runtime.discover_tools",
            lambda: [],
        )

        # clear_sandbox_buffers=True clears buffers
        interpreter = _FakeInterpreter()
        rt = AgentRuntime(interpreter=interpreter)
        result = rt.reset(clear_sandbox_buffers=True)
        assert result["status"] == "ok"
        assert result["buffers_cleared"] is True
        assert any("clear_buffer" in code for code, _vars in interpreter.calls)

        # clear_sandbox_buffers=False preserves buffers
        interpreter2 = _FakeInterpreter()
        rt2 = AgentRuntime(interpreter=interpreter2)
        result2 = rt2.reset(clear_sandbox_buffers=False)
        assert result2["status"] == "ok"
        assert result2["buffers_cleared"] is False
        assert not any("clear_buffer" in code for code, _vars in interpreter2.calls)

        # Without interpreter, ignores clear flag (no crash)
        rt3 = AgentRuntime(interpreter=None)
        result3 = rt3.reset(clear_sandbox_buffers=True)
        assert result3["status"] == "ok"
        assert result3["buffers_cleared"] is True

        # Reset clears history
        rt4 = AgentRuntime()
        rt4.history = dspy.History(messages=[{"user_message": "hi", "response": "hello"}])
        rt4.reset(clear_sandbox_buffers=False)
        messages = list(getattr(rt4.history, "messages", []) or [])
        assert messages == []
