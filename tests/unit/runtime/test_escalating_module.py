"""Unit tests for EscalatingFleetModule and the AgentRuntime use_escalation flag."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import dspy
import pytest

from fleet_rlm.runtime.factory import ESCALATING_RUNTIME_ENV_VAR, build_chat_agent
from fleet_rlm.runtime.modules.escalating import (
    ESCALATION_SENTINEL,
    EscalatingFleetModule,
    _build_rlm_prompt_context,
)


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


class _PreviewPosthocAgent(_PosthocAgent):
    def preview_routing(self, *, user_request: str, execution_mode: str = "auto") -> dict[str, Any]:
        _ = user_request, execution_mode
        return {
            "routing_decision": "url_document_rlm",
            "source_url": "https://dspy.ai",
        }


class TestEscalatingFleetModule:
    def test_url_document_rlm_is_bounded_and_disables_child_tools(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from fleet_rlm.runtime.modules import variable_mode

        calls: list[dict[str, Any]] = []

        def fake_build_variable_mode_rlm(**kwargs: Any) -> MagicMock:
            calls.append(kwargs)
            return MagicMock()

        monkeypatch.setattr(variable_mode, "build_variable_mode_rlm", fake_build_variable_mode_rlm)

        EscalatingFleetModule(
            interpreter=object(),
            tools=[lambda: None],
            max_iterations=20,
            max_llm_calls=50,
        )

        url_call = calls[1]
        assert url_call["max_iterations"] == 4
        assert url_call["max_llm_calls"] == 8
        assert url_call["extra_tools"] == []
        assert url_call["include_sub_tools"] is False
        assert url_call["include_llm_tools"] is False

    def test_url_document_prompt_tells_rlm_semantic_callbacks_are_disabled(self) -> None:
        prompt = _build_rlm_prompt_context(
            user_request="analyze https://dspy.ai docs",
            recent_history="",
            compressed_history="",
            core_memory="",
            url_document_mode=True,
        )

        assert "llm_query and llm_query_batched are disabled" in prompt
        assert "synthesize from Python inspection" in prompt
        assert "llm_query" in prompt

    def test_escalating_module_passes_max_output_chars_to_rlm_wrappers(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from fleet_rlm.runtime.modules import variable_mode

        calls: list[dict[str, Any]] = []

        def fake_build_variable_mode_rlm(**kwargs: Any) -> MagicMock:
            calls.append(kwargs)
            return MagicMock()

        monkeypatch.setattr(variable_mode, "build_variable_mode_rlm", fake_build_variable_mode_rlm)

        EscalatingFleetModule(
            interpreter=object(),
            tools=[],
            max_output_chars=12_345,
        )

        assert calls[0]["max_output_chars"] == 12_345
        assert calls[1]["max_output_chars"] == 12_345

    def test_cot_path_taken_when_no_signal(self) -> None:
        module = _make_module()
        _stub_respond(module, reasoning="Just thinking carefully.", response="Here is the answer.")
        result = module(user_request="Hello", execution_mode="auto")
        module.respond.assert_called_once()
        assert getattr(result, "assistant_response", None) == "Here is the answer."

    def test_cot_path_passes_recency_ordered_history_context(self) -> None:
        module = _make_module()
        _stub_respond(module, reasoning="Just thinking carefully.", response="Here is the answer.")
        history = dspy.History(
            messages=[
                {"user_message": "remember OLD_MARKER", "response": "OLD_MARKER"},
                {"user_message": "remember NEW_MARKER", "response": "NEW_MARKER"},
            ]
        )

        module(user_request="What marker did I just ask you to remember?", execution_mode="auto", history=history)

        call_kwargs = module.respond.call_args.kwargs
        assert call_kwargs["history"] is history
        assert "OLD_MARKER" in call_kwargs["recent_history"]
        assert "NEW_MARKER" in call_kwargs["recent_history"]
        assert call_kwargs["recent_history"].rfind("NEW_MARKER") > call_kwargs["recent_history"].rfind("OLD_MARKER")
        assert "most recent prior turn" in call_kwargs["recent_history"]

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

    def test_url_document_analysis_auto_routes_to_rlm(self) -> None:
        module = _make_module()
        _stub_respond(module)
        rlm_pred = _FakePrediction(answer="doc analysis")
        module._rlm = MagicMock(return_value=rlm_pred)
        _stub_summarize(module)

        result = module(
            user_request="analyze https://dspy.ai and provide an in depth analysis of the documentation",
            execution_mode="auto",
        )

        module.respond.assert_not_called()
        module._rlm.assert_called_once()
        assert getattr(result, "answer", None) == "doc analysis"
        assert result["routing_decision"] == "url_document_rlm"
        assert result["source_url"] == "https://dspy.ai"

    def test_url_document_analysis_passes_fetched_doc_as_rlm_variables(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        module = _make_module(interpreter=object())
        _stub_respond(module)
        rlm_pred = _FakePrediction(answer="doc analysis")
        module._url_document_rlm = MagicMock(return_value=rlm_pred)
        _stub_summarize(module)

        monkeypatch.setattr(
            "fleet_rlm.runtime.tools.document_tools.fetch_document_text",
            lambda url: {
                "status": "ok",
                "text": "# DSPy docs\nRLM details",
                "char_count": 23,
                "metadata": {"source_type": "html"},
            },
        )

        module(
            user_request="analyze https://dspy.ai and provide documentation notes",
            execution_mode="auto",
        )

        module._url_document_rlm.assert_called_once()
        call_kwargs = module._url_document_rlm.call_args.kwargs
        assert call_kwargs["source_url"] == "https://dspy.ai"
        assert call_kwargs["document_text"] == "# DSPy docs\nRLM details"
        assert call_kwargs["source_metadata"] == {
            "status": "ok",
            "char_count": "23",
            "source_type": "html",
        }
        assert "# DSPy docs\nRLM details" not in call_kwargs["prompt"]
        assert call_kwargs["prompt"].startswith(
            "Task:\nanalyze https://dspy.ai and provide documentation notes"
        )
        assert "URL document variables" in call_kwargs["prompt"]
        assert call_kwargs["prompt"].endswith(
            "Repeat task:\nanalyze https://dspy.ai and provide documentation notes"
        )

    def test_tools_only_does_not_auto_route_url_to_rlm(self) -> None:
        module = _make_module()
        _stub_respond(module, response="tool path")
        module._rlm = MagicMock(return_value=_FakePrediction(answer="should not run"))

        result = module(
            user_request="analyze https://dspy.ai and provide documentation notes",
            execution_mode="tools_only",
        )

        module.respond.assert_called_once()
        module._rlm.assert_not_called()
        assert getattr(result, "assistant_response", None) == "tool path"

    def test_preview_routing_surfaces_url_document_route_before_execution(self) -> None:
        module = _make_module()

        preview = module.preview_routing(
            user_request="analyze https://dspy.ai and summarize the docs",
            execution_mode="auto",
        )

        assert preview == {
            "routing_decision": "url_document_rlm",
            "source_url": "https://dspy.ai",
        }

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

    @pytest.mark.asyncio
    async def test_posthoc_stream_surfaces_rlm_code_trajectory(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _disable_runtime_tool_discovery(monkeypatch)
        from fleet_rlm.runtime.agent.runtime import AgentRuntime

        rt = AgentRuntime(use_escalation=True)
        rt.agent = _PosthocAgent(
            _FakePrediction(
                answer="analysis complete",
                selected_skills=["long-context"],
                routing_decision="url_document_rlm",
                source_url="https://dspy.ai",
                trajectory=[
                    {
                        "reasoning": "Fetch and inspect the docs page.",
                        "code": "import urllib.request\nprint('docs')",
                        "output": "docs",
                    }
                ],
            )
        )

        events = [event async for event in rt.aiter_chat_turn_stream("analyze https://dspy.ai")]

        status = next(event for event in events if event.payload.get("selected_skills") == ["long-context"])
        reasoning = next(event for event in events if event.kind == "reasoning")
        repl_call = next(event for event in events if event.kind == "tool_call")
        repl_result = next(event for event in events if event.kind == "tool_result")
        done = events[-1]

        assert "long-context" in status.text
        assert reasoning.text == "Fetch and inspect the docs page."
        assert repl_call.payload["tool_name"] == "repl_execute"
        assert "urllib.request" in repl_call.payload["tool_input"]
        assert repl_result.payload["tool_output"] == "docs"
        assert done.payload["routing_decision"] == "url_document_rlm"
        assert done.payload["source_url"] == "https://dspy.ai"

    @pytest.mark.asyncio
    async def test_posthoc_stream_emits_routing_preview_before_result(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _disable_runtime_tool_discovery(monkeypatch)
        from fleet_rlm.runtime.agent.runtime import AgentRuntime

        rt = AgentRuntime(use_escalation=True)
        rt.agent = _PreviewPosthocAgent(_FakePrediction(answer="analysis complete"))

        events = [event async for event in rt.aiter_chat_turn_stream("analyze https://dspy.ai")]

        assert events[0].text == "Starting turn..."
        assert events[1].payload == {
            "routing_decision": "url_document_rlm",
            "source_url": "https://dspy.ai",
        }
        assert "url_document_rlm" in events[1].text


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

    def test_build_chat_agent_forwards_rlm_limits_to_runtime(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _disable_runtime_tool_discovery(monkeypatch)

        rt = build_chat_agent(
            planner_lm=object(),
            rlm_max_iterations=9,
            rlm_max_llm_calls=11,
            rlm_max_output_chars=12_345,
        )

        assert rt.rlm_max_iterations == 9
        assert rt.rlm_max_llm_calls == 11
        assert rt.rlm_max_output_chars == 12_345
