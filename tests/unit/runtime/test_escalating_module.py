"""Unit tests for EscalatingFleetModule and the AgentRuntime use_escalation flag."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import dspy

from fleet_rlm.runtime.modules.escalating import ESCALATION_SENTINEL, EscalatingFleetModule


class _FakePrediction(dspy.Prediction):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        for k, v in kwargs.items():
            object.__setattr__(self, k, v)


def _make_module(*, interpreter: Any | None = None) -> EscalatingFleetModule:
    return EscalatingFleetModule(interpreter=interpreter, tools=[])


def _stub_respond(module: EscalatingFleetModule, *, reasoning: str = "", response: str = "ok") -> None:
    pred = _FakePrediction(reasoning=reasoning, assistant_response=response)
    module.respond = MagicMock(return_value=pred)


def _stub_summarize(module: EscalatingFleetModule, *, summary: str = "summary") -> None:
    pred = _FakePrediction(summary=summary)
    module.summarize = MagicMock(return_value=pred)


class TestEscalatingFleetModule:
    def test_cot_path_taken_when_no_signal(self) -> None:
        module = _make_module()
        _stub_respond(module, reasoning="Just thinking carefully.", response="Here is the answer.")
        result = module(user_request="Hello", execution_mode="auto")
        module.respond.assert_called_once()
        assert getattr(result, "assistant_response", None) == "Here is the answer."

    def test_rlm_path_triggered_by_sentinel_in_reasoning(self) -> None:
        module = _make_module()
        _stub_respond(module, reasoning=f"I need external data {ESCALATION_SENTINEL}", response="step1")
        rlm_pred = _FakePrediction(answer="deep answer")
        module._rlm = MagicMock(return_value=rlm_pred)
        _stub_summarize(module)

        result = module(user_request="Complex task", execution_mode="auto")
        module.respond.assert_called_once()
        module._rlm.assert_called_once()
        assert getattr(result, "answer", None) == "deep answer"

    def test_force_escalate_skips_cot(self) -> None:
        module = _make_module()
        _stub_respond(module)
        rlm_pred = _FakePrediction(answer="forced")
        module._rlm = MagicMock(return_value=rlm_pred)
        _stub_summarize(module)

        result = module(user_request="do complex thing", force_escalate=True)
        module.respond.assert_not_called()
        module._rlm.assert_called_once()
        assert getattr(result, "answer", None) == "forced"

    def test_execution_mode_rlm_skips_cot(self) -> None:
        module = _make_module()
        _stub_respond(module)
        rlm_pred = _FakePrediction(answer="rlm_mode")
        module._rlm = MagicMock(return_value=rlm_pred)
        _stub_summarize(module)

        result = module(user_request="query", execution_mode="rlm")
        module.respond.assert_not_called()
        assert getattr(result, "answer", None) == "rlm_mode"

    def test_rlm_fallback_to_cot_on_error(self) -> None:
        module = _make_module()
        cot_pred = _FakePrediction(reasoning=ESCALATION_SENTINEL, assistant_response="cot_resp")
        module.respond = MagicMock(side_effect=[cot_pred, _FakePrediction(assistant_response="fallback")])
        module._rlm = MagicMock(side_effect=RuntimeError("RLM failed"))
        _stub_summarize(module)

        result = module(user_request="query", execution_mode="auto")
        assert getattr(result, "assistant_response", None) == "fallback"

    def test_compress_history_returns_summary(self) -> None:
        module = _make_module()
        _stub_summarize(module, summary="compressed context")
        history = dspy.History(messages=[{"user_request": "hi", "assistant_response": "hello"}])
        result = module.compress_history(history)
        assert result == "compressed context"

    def test_compress_history_empty_returns_empty(self) -> None:
        module = _make_module()
        history = dspy.History(messages=[])
        result = module.compress_history(history)
        assert result == ""

    def test_no_sentinel_means_no_escalation(self) -> None:
        module = _make_module()
        _stub_respond(module, reasoning="I can handle this directly.")
        module(user_request="Simple question")
        module.respond.assert_called_once()
        assert module._rlm is None or not getattr(module._rlm, "called", False)


class TestAgentRuntimeEscalationFlag:
    def test_use_escalation_false_keeps_fleet_agent(self) -> None:
        with patch("fleet_rlm.runtime.tools.registry.discover_tools", return_value=[]):
            from fleet_rlm.runtime.agent.agent import FleetAgent
            from fleet_rlm.runtime.agent.runtime import AgentRuntime

            rt = AgentRuntime(use_escalation=False)
            assert isinstance(rt.agent, FleetAgent)

    def test_use_escalation_true_installs_escalating_module(self) -> None:
        with patch("fleet_rlm.runtime.tools.registry.discover_tools", return_value=[]):
            from fleet_rlm.runtime.agent.runtime import AgentRuntime

            rt = AgentRuntime(use_escalation=True)
            assert isinstance(rt.agent, EscalatingFleetModule)

    def test_reset_clears_conversation_summary(self) -> None:
        with patch("fleet_rlm.runtime.tools.registry.discover_tools", return_value=[]):
            from fleet_rlm.runtime.agent.runtime import AgentRuntime

            rt = AgentRuntime(use_escalation=True)
            rt.conversation_summary = "some context"
            rt.reset(clear_sandbox_buffers=False)
            assert rt.conversation_summary == ""
            assert rt._turns_since_summary == 0
