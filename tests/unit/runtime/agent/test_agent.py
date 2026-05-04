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
            records.append(
                {"signature": signature, "tools": list(tools), "max_iters": max_iters}
            )
            self.signature = signature

        def __call__(self, **kwargs):
            return dspy.Prediction(response="fake_response")

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


def test_fleet_agent_is_dspy_module_subclass(react_records):
    """VAL-AGENT-001: FleetAgent must subclass dspy.Module."""
    assert issubclass(FleetAgent, dspy.Module)
    agent = FleetAgent(tools=[])
    assert isinstance(agent, dspy.Module)


def test_fleet_agent_wraps_react_internally(react_records):
    """VAL-AGENT-001: FleetAgent must wrap a dspy.ReAct instance."""
    agent = FleetAgent(tools=[])
    assert hasattr(agent, "react")
    assert agent.react is not None
    assert len(react_records) == 1


# ---------------------------------------------------------------------------
# VAL-AGENT-002: Signature has chat_history, user_message, response
# ---------------------------------------------------------------------------


def test_fleet_agent_signature_has_required_input_fields():
    """VAL-AGENT-002: FleetAgentSignature defines chat_history and user_message inputs."""
    assert "chat_history" in FleetAgentSignature.input_fields
    assert "user_message" in FleetAgentSignature.input_fields


def test_fleet_agent_signature_has_response_output_field():
    """VAL-AGENT-002: FleetAgentSignature defines response as output field."""
    assert "response" in FleetAgentSignature.output_fields


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


def test_fleet_agent_forward_returns_prediction(react_records):
    """VAL-AGENT-003: forward() returns a dspy.Prediction instance."""
    agent = FleetAgent(tools=[])
    history = dspy.History(messages=[])
    result = agent.forward(chat_history=history, user_message="hello")
    assert isinstance(result, dspy.Prediction)


def test_fleet_agent_forward_prediction_has_response_field(react_records):
    """VAL-AGENT-003: The returned Prediction must contain a response field."""
    agent = FleetAgent(tools=[])
    history = dspy.History(messages=[])
    result = agent.forward(chat_history=history, user_message="hello")
    assert hasattr(result, "response")
    assert result.response == "fake_response"


# ---------------------------------------------------------------------------
# VAL-AGENT-004: Optimizer-compatible
# ---------------------------------------------------------------------------


def test_fleet_agent_named_parameters_callable(react_records):
    """VAL-AGENT-004: named_parameters() must be callable (DSPy optimizer API)."""
    agent = FleetAgent(tools=[])
    params = list(agent.named_parameters())
    assert isinstance(params, list)


def test_fleet_agent_predictors_callable(react_records):
    """VAL-AGENT-004: predictors() must be callable (DSPy optimizer API)."""
    agent = FleetAgent(tools=[])
    predictors = list(agent.predictors())
    assert isinstance(predictors, list)


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


def test_fleet_agent_accepts_empty_tools_list(react_records):
    """VAL-AGENT-005: Constructor accepts an empty tools list."""
    FleetAgent(tools=[])
    assert len(react_records) == 1
    assert react_records[0]["tools"] == []


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


def test_fleet_agent_default_max_iters_is_ten(react_records):
    """VAL-AGENT-005: Default max_iters is 10."""
    FleetAgent(tools=[])
    assert react_records[0]["max_iters"] == 10


# ---------------------------------------------------------------------------
# VAL-AGENT-006: No import-time side effects
# ---------------------------------------------------------------------------


def test_no_import_time_side_effects():
    """VAL-AGENT-006: Importing agent.py must not trigger network calls."""
    # If we can import without error or network access, the constraint is met.
    import fleet_rlm.runtime.agent.agent as agent_module

    assert hasattr(agent_module, "FleetAgent")
    assert hasattr(agent_module, "FleetAgentSignature")


def test_fleet_agent_signature_importable_without_network():
    """VAL-AGENT-006: FleetAgentSignature is available at import time with no side effects."""
    from fleet_rlm.runtime.agent.agent import FleetAgentSignature as Sig

    assert issubclass(Sig, dspy.Signature)


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
