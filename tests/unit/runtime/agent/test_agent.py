"""Unit tests for FleetAgent and FleetAgentSignature.

Covers VAL-AGENT-001 through VAL-AGENT-006 from the validation contract.
dspy.ReAct is monkeypatched to avoid real LLM calls.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import dspy
import pytest

from fleet_rlm.runtime.agent.agent import FleetAgent, FleetAgentSignature

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fake_react(records: list[dict[str, Any]]):
    """Return a fake dspy.ReAct class that records construction arguments."""

    class _FakeReAct:
        def __init__(self, *, signature, tools, max_iters, **kwargs):
            records.append({"signature": signature, "tools": list(tools), "max_iters": max_iters})
            self.signature = signature

        def __call__(self, **kwargs):
            return dspy.Prediction(response="fake_response")

    return _FakeReAct


def _make_finish_aware_fake_react(
    steps: list[SimpleNamespace],
    *,
    final_response: str,
):
    """Return a fake dspy.ReAct class with the upstream extract phase shape."""

    class _FakeReAct:
        def __init__(self, *, signature, tools, max_iters, **kwargs):
            self.signature = signature
            self._max_iters = max_iters
            self.react = object()
            self.extract = object()
            self.extract_calls = 0
            self._steps = list(steps)
            self.tools = {getattr(tool, "__name__", str(tool)): tool for tool in tools}
            self.tools["finish"] = lambda: "Completed."

        def _call_with_potential_trajectory_truncation(self, module, trajectory, **kwargs):
            if module is self.react:
                if not self._steps:
                    raise AssertionError("No scripted ReAct step remaining")
                return self._steps.pop(0)
            if module is self.extract:
                self.extract_calls += 1
                return {"response": final_response}
            raise AssertionError(f"Unexpected module call: {module!r}")

        def __call__(self, **kwargs):
            return self.forward(**kwargs)

        def forward(self, **input_args):
            trajectory = {}
            max_iters = input_args.pop("max_iters", self._max_iters)
            for idx in range(max_iters):
                pred = self._call_with_potential_trajectory_truncation(
                    self.react,
                    trajectory,
                    **input_args,
                )
                trajectory[f"thought_{idx}"] = pred.next_thought
                trajectory[f"tool_name_{idx}"] = pred.next_tool_name
                trajectory[f"tool_args_{idx}"] = pred.next_tool_args
                trajectory[f"observation_{idx}"] = self.tools[pred.next_tool_name](**pred.next_tool_args)
                if pred.next_tool_name == "finish":
                    break

            extract = self._call_with_potential_trajectory_truncation(
                self.extract,
                trajectory,
                **input_args,
            )
            return dspy.Prediction(trajectory=trajectory, **extract)

    return _FakeReAct


@pytest.fixture()
def react_records(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Monkeypatch dspy.ReAct inside agent.py and capture construction args."""
    records: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "fleet_rlm.runtime.agent.agent.dspy.ReAct",
        _make_fake_react(records),
    )
    return records


# ---------------------------------------------------------------------------
# VAL-AGENT-001: FleetAgent subclasses dspy.Module
# ---------------------------------------------------------------------------


def test_fleet_agent_wraps_react_internally(react_records):
    """VAL-AGENT-001: FleetAgent must wrap a dspy.ReAct instance."""
    agent = FleetAgent(tools=[])
    assert hasattr(agent, "react")
    assert agent.react is not None
    assert len(react_records) == 1


# ---------------------------------------------------------------------------
# VAL-AGENT-002: Signature has chat_history, user_message, response
# ---------------------------------------------------------------------------


def test_fleet_agent_react_uses_fleet_agent_signature(react_records):
    """VAL-AGENT-002: FleetAgent passes FleetAgentSignature to dspy.ReAct."""
    agent = FleetAgent(tools=[])
    sig = agent.react.signature
    assert sig is FleetAgentSignature
    assert "chat_history" in sig.input_fields
    assert "user_message" in sig.input_fields
    assert "response" in sig.output_fields


# ---------------------------------------------------------------------------
# VAL-AGENT-003: forward() returns dspy.Prediction with response field
# ---------------------------------------------------------------------------


def test_fleet_agent_forward_prediction_has_response_field(react_records):
    """VAL-AGENT-003: The returned Prediction must contain a response field."""
    agent = FleetAgent(tools=[])
    history = dspy.History(messages=[])
    result = agent.forward(chat_history=history, user_message="hello")
    assert hasattr(result, "response")
    assert result.response == "fake_response"


def test_fleet_agent_finish_only_trajectory_skips_extract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FleetAgent reuses the initial thought when the first ReAct step is finish."""
    monkeypatch.setattr(
        "fleet_rlm.runtime.agent.agent.dspy.ReAct",
        _make_finish_aware_fake_react(
            [
                SimpleNamespace(
                    next_thought="Hello from the first thought",
                    next_tool_name="finish",
                    next_tool_args={},
                )
            ],
            final_response="should not be used",
        ),
    )

    agent = FleetAgent(tools=[])
    history = dspy.History(messages=[])

    result = agent.forward(chat_history=history, user_message="hello")

    assert result.response == "Hello from the first thought"
    assert result.trajectory["tool_name_0"] == "finish"
    assert agent.react.extract_calls == 0


def test_fleet_agent_tool_trajectory_still_uses_extract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FleetAgent still uses the extract stage once a real tool was called."""

    def lookup(x: str) -> str:
        return f"observed:{x}"

    monkeypatch.setattr(
        "fleet_rlm.runtime.agent.agent.dspy.ReAct",
        _make_finish_aware_fake_react(
            [
                SimpleNamespace(
                    next_thought="Need the lookup tool",
                    next_tool_name="lookup",
                    next_tool_args={"x": "value"},
                ),
                SimpleNamespace(
                    next_thought="Now I can finish",
                    next_tool_name="finish",
                    next_tool_args={},
                ),
            ],
            final_response="extracted answer",
        ),
    )

    agent = FleetAgent(tools=[lookup])
    history = dspy.History(messages=[])

    result = agent.forward(chat_history=history, user_message="hello")

    assert result.response == "extracted answer"
    assert result.trajectory["tool_name_0"] == "lookup"
    assert result.trajectory["observation_0"] == "observed:value"
    assert agent.react.extract_calls == 1


def test_fleet_agent_patches_finish_only_prompt_instruction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FleetAgent replaces the default ReAct prompt with a compact tool catalog."""

    def lookup(query: str, limit: int = 3) -> str:
        return f"{query}:{limit}"

    class _FakeReAct:
        def __init__(self, *, signature, tools, max_iters, **kwargs):
            self.signature = signature
            self.tools = {
                "lookup": SimpleNamespace(
                    name="lookup",
                    desc="Look up a value for the user.",
                    args={
                        "query": {"type": "string"},
                        "limit": {"type": "integer", "default": 3},
                    },
                ),
                "finish": SimpleNamespace(name="finish", desc="Verbose finish text.", args={}),
            }
            self.react = SimpleNamespace(signature=SimpleNamespace(instructions="Base instructions"))
            self.extract = object()

        def __call__(self, **kwargs):
            return dspy.Prediction(response="ok")

    monkeypatch.setattr(
        "fleet_rlm.runtime.agent.agent.dspy.ReAct",
        _FakeReAct,
    )

    agent = FleetAgent(tools=[lookup])
    instructions = agent.react.react.signature.instructions

    assert (
        "If you choose finish on the first step without using any tool, write "
        "next_thought as the exact final response to send to the user." in instructions
    )
    assert "- lookup(query:string, limit:integer=3): Look up a value for the user." in instructions
    assert "- finish(): Stop and return the final response." in instructions
    assert "It takes arguments" not in instructions


# ---------------------------------------------------------------------------
# VAL-AGENT-004: Optimizer-compatible
# ---------------------------------------------------------------------------


def test_fleet_agent_save_load_compatible(react_records, tmp_path):
    """VAL-AGENT-004: FleetAgent supports save/load without error."""
    agent = FleetAgent(tools=[])
    save_path = str(tmp_path / "agent.json")
    # save() and load() are DSPy optimizer contract methods
    agent.save(save_path)
    agent.load(save_path)


# ---------------------------------------------------------------------------
# VAL-AGENT-005: Constructor accepts tools list
# ---------------------------------------------------------------------------


def test_fleet_agent_passes_tools_to_react(react_records):
    """VAL-AGENT-005: Tools are forwarded to the internal dspy.ReAct."""

    def tool_a(x: str) -> str:
        """Tool A for testing."""
        return x

    def tool_b(x: str) -> str:
        """Tool B for testing."""
        return x

    FleetAgent(tools=[tool_a, tool_b])
    assert len(react_records) == 1
    assert react_records[0]["tools"] == [tool_a, tool_b]


def test_fleet_agent_passes_max_iters_to_react(react_records):
    """VAL-AGENT-005: max_iters is forwarded to the internal dspy.ReAct."""
    FleetAgent(tools=[], max_iters=20)
    assert len(react_records) == 1
    assert react_records[0]["max_iters"] == 20


# ---------------------------------------------------------------------------
# VAL-BACKEND-RUNTIME-002: RLMReActAgent.forward does not pass max_iters
# ---------------------------------------------------------------------------


def test_rlm_react_agent_forward_omits_max_iters(monkeypatch: pytest.MonkeyPatch):
    """RLMReActAgent.forward must not pass max_iters to dspy.ReAct.forward.

    max_iters is a constructor argument; passing it to forward() is incorrect.
    """
    from fleet_rlm.runtime.agent.agent import RLMReActAgent
    from fleet_rlm.runtime.agent.signatures import RLMReActChatSignature

    call_kwargs: dict[str, Any] = {}

    class _FakeReAct:
        def __init__(self, *, signature, tools, max_iters, **kwargs):
            self.signature = signature
            self._max_iters = max_iters

        def __call__(self, **kwargs):
            call_kwargs.update(kwargs)
            return dspy.Prediction(answer="fake")

    monkeypatch.setattr(
        "fleet_rlm.runtime.agent.agent.dspy.ReAct",
        _FakeReAct,
    )

    agent = RLMReActAgent(
        signature=RLMReActChatSignature,
        tools=[],
        max_iters=7,
    )
    history = dspy.History(messages=[])
    agent.forward(
        user_request="test",
        history=history,
        core_memory="",
        max_iters=7,
    )

    assert "max_iters" not in call_kwargs
    assert "user_request" in call_kwargs
    assert "history" in call_kwargs
    assert "core_memory" in call_kwargs


# ---------------------------------------------------------------------------
# Factory: build_chat_agent returns AgentRuntime
# ---------------------------------------------------------------------------


def test_build_chat_agent_returns_agent_runtime(monkeypatch: pytest.MonkeyPatch):
    """build_chat_agent should return an AgentRuntime instance (VAL-FACTORY-002)."""
    # Ensure factory has no RLMReActChatAgent reference
    import inspect

    from fleet_rlm.runtime import factory as _factory
    from fleet_rlm.runtime.agent.runtime import AgentRuntime

    factory_src = inspect.getsource(_factory)
    assert "RLMReActChatAgent" not in factory_src

    # Suppress LM configuration requirement
    monkeypatch.setattr(
        "fleet_rlm.runtime.factory._require_planner_ready",
        lambda *a, **kw: None,
    )

    agent = _factory.build_chat_agent(planner_lm=object())
    assert isinstance(agent, AgentRuntime)


def test_build_chat_agent_threads_delegate_lm_into_interpreter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fleet_rlm.runtime import factory as _factory

    monkeypatch.setattr(
        "fleet_rlm.runtime.factory._require_planner_ready",
        lambda *a, **kw: None,
    )

    delegate_lm = object()
    interpreter = SimpleNamespace()
    _factory.build_chat_agent(
        planner_lm=object(),
        interpreter=interpreter,
        delegate_lm=delegate_lm,
    )

    assert interpreter.sub_lm is delegate_lm


def test_build_chat_agent_supports_sync_context_manager(
    monkeypatch: pytest.MonkeyPatch,
):
    """Existing CLI/MCP call sites use build_chat_agent() in a sync with block."""
    from fleet_rlm.runtime import factory as _factory
    from fleet_rlm.runtime.agent.runtime import AgentRuntime

    monkeypatch.setattr(
        "fleet_rlm.runtime.factory._require_planner_ready",
        lambda *a, **kw: None,
    )

    with _factory.build_chat_agent(planner_lm=object()) as agent:
        assert isinstance(agent, AgentRuntime)


@pytest.mark.asyncio
async def test_build_chat_agent_supports_async_context_manager(
    monkeypatch: pytest.MonkeyPatch,
):
    """Websocket runtime call sites can still use async context management."""
    from fleet_rlm.runtime import factory as _factory
    from fleet_rlm.runtime.agent.runtime import AgentRuntime

    monkeypatch.setattr(
        "fleet_rlm.runtime.factory._require_planner_ready",
        lambda *a, **kw: None,
    )

    async with _factory.build_chat_agent(planner_lm=object()) as agent:
        assert isinstance(agent, AgentRuntime)
