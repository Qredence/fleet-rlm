"""Unit tests for FleetAgent and FleetAgentSignature."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import dspy
import pytest

from fleet_rlm.runtime.agent.agent import FleetAgent, FleetAgentSignature


def _make_fake_predict(records: list[dict[str, Any]]):
    class _FakePredict:
        def __init__(self, signature, **kwargs):
            records.append({"signature": signature})
            self.signature = signature

        def __call__(self, **kwargs):
            return dspy.Prediction(next_thought="fake_thought", next_tool_name="finish", next_tool_args={})

        async def acall(self, **kwargs):
            return self.__call__(**kwargs)

    return _FakePredict


def _make_fake_extract(records: list[dict[str, Any]]):
    class _FakeExtract:
        def __init__(self, signature, **kwargs):
            records.append({"signature": signature})
            self.signature = signature

        def __call__(self, **kwargs):
            return dspy.Prediction(response="fake_response")

        async def acall(self, **kwargs):
            return self.__call__(**kwargs)

    return _FakeExtract


@pytest.fixture()
def react_records(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    monkeypatch.setattr("fleet_rlm.runtime.agent.agent.dspy.Predict", _make_fake_predict(records))
    monkeypatch.setattr("fleet_rlm.runtime.agent.agent.dspy.ChainOfThought", _make_fake_extract(records))
    return records


def test_fleet_agent_initializes_with_planner_and_extract(react_records):
    agent = FleetAgent(tools=[])
    assert hasattr(agent, "planner")
    assert hasattr(agent, "extract")


def test_fleet_agent_uses_fleet_agent_signature(react_records):
    agent = FleetAgent(tools=[])
    sig = agent.signature
    assert sig is FleetAgentSignature


def test_fleet_agent_forward_prediction_has_response_field(react_records):
    agent = FleetAgent(tools=[])
    history = dspy.History(messages=[])
    result = agent.forward(chat_history=history, user_message="hello")
    assert hasattr(result, "response")
    # Finish-only extracts it from thought
    assert result.response == "fake_thought"


def test_build_chat_agent_returns_agent_runtime(monkeypatch: pytest.MonkeyPatch):
    from fleet_rlm.runtime import factory as _factory
    from fleet_rlm.runtime.agent.runtime import AgentRuntime

    monkeypatch.setattr("fleet_rlm.runtime.factory._require_planner_ready", lambda *a, **kw: None)
    agent = _factory.build_chat_agent(planner_lm=object())
    assert isinstance(agent, AgentRuntime)


def test_build_chat_agent_threads_delegate_lm_into_interpreter(monkeypatch: pytest.MonkeyPatch):
    from fleet_rlm.runtime import factory as _factory

    monkeypatch.setattr("fleet_rlm.runtime.factory._require_planner_ready", lambda *a, **kw: None)
    delegate_lm = object()
    interpreter = SimpleNamespace()
    _factory.build_chat_agent(planner_lm=object(), interpreter=interpreter, delegate_lm=delegate_lm)
    assert getattr(interpreter, "sub_lm", None) is delegate_lm


def test_build_chat_agent_supports_sync_context_manager(monkeypatch: pytest.MonkeyPatch):
    from fleet_rlm.runtime import factory as _factory
    from fleet_rlm.runtime.agent.runtime import AgentRuntime

    monkeypatch.setattr("fleet_rlm.runtime.factory._require_planner_ready", lambda *a, **kw: None)
    with _factory.build_chat_agent(planner_lm=object()) as agent:
        assert isinstance(agent, AgentRuntime)
