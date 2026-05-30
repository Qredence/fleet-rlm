"""Unit tests for EscalatingFleetModule and the AgentRuntime use_escalation flag."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import dspy
import pytest

from fleet_rlm.runtime.factory import ESCALATING_RUNTIME_ENV_VAR, build_chat_agent
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


def _disable_runtime_tool_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    from fleet_rlm.runtime.agent import runtime as runtime_mod

    monkeypatch.setattr(runtime_mod, "discover_tools", lambda: [])


class _PosthocAgent:
    def __init__(self, prediction: dspy.Prediction) -> None:
        self.prediction = prediction
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> dspy.Prediction:
        self.calls.append(kwargs)
        return self.prediction


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

    def test_execution_mode_rlm_only_skips_cot(self) -> None:
        module = _make_module()
        _stub_respond(module)
        rlm_pred = _FakePrediction(answer="rlm_only_mode")
        module._rlm = MagicMock(return_value=rlm_pred)
        _stub_summarize(module)

        result = module(user_request="query", execution_mode="rlm_only")
        module.respond.assert_not_called()
        assert getattr(result, "answer", None) == "rlm_only_mode"

    def test_rlm_fallback_to_cot_on_error(self) -> None:
        module = _make_module()
        cot_pred = _FakePrediction(reasoning=ESCALATION_SENTINEL, assistant_response="cot_resp")
        module.respond = MagicMock(side_effect=[cot_pred, _FakePrediction(assistant_response="fallback")])
        module._rlm = MagicMock(side_effect=RuntimeError("RLM failed"))
        _stub_summarize(module)

        result = module(user_request="query", execution_mode="auto")
        assert getattr(result, "assistant_response", None) == "fallback"
        assert result["runtime_degraded"] is True
        assert result["runtime_failure_category"] == "rlm_fallback"
        assert result["runtime_failure_phase"] == "escalating_rlm"
        assert result["runtime_fallback_used"] is True
        assert result["runtime_warning"]

    def test_compress_history_returns_summary(self) -> None:
        module = _make_module()
        _stub_summarize(module, summary="compressed context")
        history = dspy.History(messages=[{"user_message": "hi", "response": "hello"}])
        result = module.compress_history(history)
        assert result == "compressed context"
        module.summarize.assert_called_once_with(conversation_history="User: hi\nAssistant: hello")

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
    def test_default_installs_escalating_module(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _disable_runtime_tool_discovery(monkeypatch)
        from fleet_rlm.runtime.agent.runtime import AgentRuntime

        rt = AgentRuntime()
        assert isinstance(rt.agent, EscalatingFleetModule)

    def test_use_escalation_false_keeps_fleet_agent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _disable_runtime_tool_discovery(monkeypatch)
        from fleet_rlm.runtime.agent.agent import FleetAgent
        from fleet_rlm.runtime.agent.runtime import AgentRuntime

        rt = AgentRuntime(use_escalation=False)
        assert isinstance(rt.agent, FleetAgent)

    def test_use_escalation_true_installs_escalating_module(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _disable_runtime_tool_discovery(monkeypatch)
        from fleet_rlm.runtime.agent.runtime import AgentRuntime

        rt = AgentRuntime(use_escalation=True)
        assert isinstance(rt.agent, EscalatingFleetModule)

    def test_reset_clears_conversation_summary(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _disable_runtime_tool_discovery(monkeypatch)
        from fleet_rlm.runtime.agent.runtime import AgentRuntime

        rt = AgentRuntime(use_escalation=True)
        rt.conversation_summary = "some context"
        rt.reset(clear_sandbox_buffers=False)
        assert rt.conversation_summary == ""
        assert rt._turns_since_summary == 0

    @pytest.mark.asyncio
    async def test_posthoc_stream_surfaces_rlm_fallback_warning(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _disable_runtime_tool_discovery(monkeypatch)
        from fleet_rlm.runtime.agent.runtime import AgentRuntime

        rt = AgentRuntime(use_escalation=True)
        rt.agent = _PosthocAgent(
            _FakePrediction(
                assistant_response="fallback answer",
                runtime_degraded=True,
                runtime_failure_category="rlm_fallback",
                runtime_failure_phase="escalating_rlm",
                runtime_fallback_used=True,
                runtime_warning="RLM fallback used.",
            )
        )

        events = [event async for event in rt.aiter_chat_turn_stream("hello")]

        warning = next(event for event in events if event.kind == "warning")
        done = events[-1]
        assert warning.text == "RLM fallback used."
        assert warning.payload["runtime_degraded"] is True
        assert done.kind == "done"
        assert done.payload["runtime_degraded"] is True
        assert done.payload["runtime_fallback_used"] is True
        assert done.payload["runtime_module"] == "_PosthocAgent"
        assert done.payload["escalation_enabled"] is True
        assert done.payload["execution_mode"] == "auto"
        assert done.text == "fallback answer"


class TestBuildChatAgentRuntimeDefault:
    def test_build_chat_agent_defaults_to_escalating_module(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(ESCALATING_RUNTIME_ENV_VAR, raising=False)
        _disable_runtime_tool_discovery(monkeypatch)

        rt = build_chat_agent(planner_lm=object())

        assert isinstance(rt.agent, EscalatingFleetModule)

    def test_build_chat_agent_env_toggle_rolls_back_to_fleet_agent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(ESCALATING_RUNTIME_ENV_VAR, "false")
        _disable_runtime_tool_discovery(monkeypatch)
        from fleet_rlm.runtime.agent.agent import FleetAgent

        rt = build_chat_agent(planner_lm=object())

        assert isinstance(rt.agent, FleetAgent)

    def test_build_chat_agent_explicit_toggle_overrides_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(ESCALATING_RUNTIME_ENV_VAR, "false")
        _disable_runtime_tool_discovery(monkeypatch)

        rt = build_chat_agent(planner_lm=object(), use_escalation=True)

        assert isinstance(rt.agent, EscalatingFleetModule)
