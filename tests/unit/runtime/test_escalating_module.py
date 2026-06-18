"""Unit tests for EscalatingFleetModule and the AgentRuntime use_escalation flag."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import dspy
import pytest

from fleet_rlm.runtime.factory import ESCALATING_RUNTIME_ENV_VAR, build_chat_agent
from fleet_rlm.runtime.modules.escalating import EscalatingFleetModule
from fleet_rlm.runtime.modules.rlm_prompts import build_rlm_core_context
from fleet_rlm.runtime.task_intent import implies_quote_retrieval, quote_retrieval_repl_guidance


class _FakePrediction(dspy.Prediction):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        for k, v in kwargs.items():
            object.__setattr__(self, k, v)


def _make_module(*, interpreter: Any | None = None) -> EscalatingFleetModule:
    return EscalatingFleetModule(interpreter=interpreter, tools=[])


def _stub_respond(module: EscalatingFleetModule, *, reasoning: str = "", response: str = "ok") -> None:
    pred = _FakePrediction(reasoning=reasoning, response=response)
    module.respond = MagicMock(return_value=pred)


def _stub_route(module: EscalatingFleetModule, *, route: str = "direct") -> None:
    module.route = MagicMock(return_value=_FakePrediction(route=route))


def _stub_summarize(module: EscalatingFleetModule, *, summary: str = "summary") -> None:
    pred = _FakePrediction(summary=summary)
    module.summarize = MagicMock(return_value=pred)


def test_enrich_with_skills_uses_scaffold_when_volume_unmounted() -> None:
    module = _make_module(interpreter=None)

    enriched, selected, active_skills = module._enrich_with_skills(
        "Analyze the whole documentation of https://dspy.ai",
        "",
    )

    assert "long-context" in selected
    assert "[Active Skills]" in enriched
    assert "[Skill:" not in enriched
    assert "long-context" in active_skills.selected
    assert active_skills.instructions["long-context"].startswith("---")


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
    def preview_routing(
        self,
        *,
        user_request: str,
        execution_mode: str = "auto",
        turn_context: Any | None = None,
    ) -> dict[str, Any]:
        _ = user_request, execution_mode, turn_context
        return {
            "routing_decision": "url_document_rlm",
            "source_url": "https://dspy.ai",
        }


class _AsyncReactAgent:
    def __init__(self, prediction: dspy.Prediction) -> None:
        self.prediction = prediction
        self.acall_kwargs: dict[str, Any] | None = None

    def __call__(self, **_: Any) -> dspy.Prediction:
        raise AssertionError("sync ReAct path should not be used by aforward")

    async def acall(self, **kwargs: Any) -> dspy.Prediction:
        self.acall_kwargs = kwargs
        return self.prediction


class TestEscalatingFleetModule:
    def test_url_document_rlm_is_bounded_and_disables_child_tools(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from fleet_rlm.runtime.modules import escalating

        calls: list[dict[str, Any]] = []

        def fake_create_runtime_rlm(**kwargs: Any) -> MagicMock:
            calls.append(kwargs)
            return MagicMock()

        monkeypatch.setattr(escalating, "create_runtime_rlm", fake_create_runtime_rlm)

        tool = lambda: None  # noqa: E731

        EscalatingFleetModule(
            interpreter=object(),
            tools=[tool],
            max_iterations=20,
            max_llm_calls=50,
        )

        assert len(calls) == 3
        url_call = calls[2]
        assert url_call["max_iterations"] == 4
        assert url_call["max_llm_calls"] == 8
        assert "tools" not in url_call
        assert url_call["include_llm_tools"] is True
        # The main and workspace RLMs share user tools (plus delegation tools).
        assert calls[0]["tools"] == [tool]
        assert calls[1]["tools"] == [tool]

    def test_main_rlm_receives_interpreter_delegation_tools(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from types import SimpleNamespace

        from fleet_rlm.runtime.modules import escalating

        calls: list[dict[str, Any]] = []

        def fake_create_runtime_rlm(**kwargs: Any) -> MagicMock:
            calls.append(kwargs)
            return MagicMock()

        monkeypatch.setattr(escalating, "create_runtime_rlm", fake_create_runtime_rlm)
        interpreter = SimpleNamespace(sub_rlm=lambda prompt: prompt, sub_rlm_batched=lambda prompts: prompts)

        EscalatingFleetModule(interpreter=interpreter, tools=[])

        assert calls[0]["tools"] == [interpreter.sub_rlm, interpreter.sub_rlm_batched]

    def test_url_document_signature_keeps_llm_query_guidance(self) -> None:
        from fleet_rlm.runtime.agent.signatures import RLMDocumentTurnSignature

        assert "llm_query" in str(RLMDocumentTurnSignature.__doc__)

    def test_needle_guidance_requires_single_verbatim_quote(self) -> None:
        guidance = quote_retrieval_repl_guidance()
        assert guidance
        assert "exactly ONE quote block" in guidance
        assert "no paraphrase" in guidance
        assert implies_quote_retrieval("Return the quote verbatim from Akiyuki Ui, Operating Officer, Mizuho Bank.")

    def test_needle_guidance_skipped_for_non_quote_tasks(self) -> None:
        assert not implies_quote_retrieval("Summarize the foreword themes")

    def test_large_context_prompt_adds_needle_guidance_for_exact_quotes(self) -> None:
        prompt = build_rlm_core_context(
            user_request="What is the exact quote from Chad Gates, Managing Director?",
            compressed_history="",
            core_memory="",
            url_document_mode=False,
            large_context_mode=True,
        )

        assert "Exact quote retrieval (mandatory):" in prompt
        assert "exactly ONE quote block" in prompt
        assert "document_text.find" in prompt
        assert "character-for-character" in prompt
        assert "Do not open host context_paths" in prompt

    def test_review_draft_prompt_adds_early_submit_guidance(self) -> None:
        prompt = build_rlm_core_context(
            user_request="Review my uncommitted changes and draft findings.",
            compressed_history="",
            core_memory="",
        )

        assert "Early stop for review/draft tasks" in prompt
        assert "call SUBMIT(response=...) immediately" in prompt

    def test_url_document_prompt_disables_llm_query_when_repl_only_env(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("FLEET_RLM_URL_REPL_ONLY", "true")
        prompt = build_rlm_core_context(
            user_request="analyze https://dspy.ai docs",
            compressed_history="",
            core_memory="",
            url_document_mode=True,
        )

        assert "llm_query and llm_query_batched are disabled" in prompt
        assert "synthesize from Python inspection" in prompt

    def test_escalating_module_passes_max_output_chars_to_rlm_wrappers(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from fleet_rlm.runtime.modules import escalating

        calls: list[dict[str, Any]] = []

        def fake_create_runtime_rlm(**kwargs: Any) -> MagicMock:
            calls.append(kwargs)
            return MagicMock()

        monkeypatch.setattr(escalating, "create_runtime_rlm", fake_create_runtime_rlm)

        EscalatingFleetModule(
            interpreter=object(),
            tools=[],
            max_output_chars=12_345,
        )

        assert calls[0]["max_output_chars"] == 12_345
        assert calls[1]["max_output_chars"] == 12_345
        assert calls[2]["max_output_chars"] == 12_345

    def test_escalating_module_passes_action_max_tokens_to_rlm_wrappers(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from fleet_rlm.runtime.modules import escalating

        calls: list[dict[str, Any]] = []

        def fake_create_runtime_rlm(**kwargs: Any) -> MagicMock:
            calls.append(kwargs)
            return MagicMock()

        monkeypatch.setattr(escalating, "create_runtime_rlm", fake_create_runtime_rlm)

        EscalatingFleetModule(
            interpreter=object(),
            tools=[],
            action_max_tokens=4096,
        )

        assert calls[0]["action_max_tokens"] == 4096
        assert calls[1]["action_max_tokens"] == 4096
        assert calls[2]["action_max_tokens"] == 4096

    def test_direct_route_uses_chain_of_thought(self) -> None:
        module = _make_module()
        _stub_route(module, route="direct")
        _stub_respond(module, reasoning="Just thinking carefully.", response="Here is the answer.")
        result = module(user_request="Hello", execution_mode="auto")
        module.route.assert_called_once()
        module.respond.assert_called_once()
        assert getattr(result, "response", None) == "Here is the answer."

    def test_direct_route_passes_history_to_respond(self) -> None:
        module = _make_module()
        _stub_route(module, route="direct")
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
        assert "recent_history" not in call_kwargs

    def test_router_failure_degrades_to_direct(self) -> None:
        module = _make_module()
        module.route = MagicMock(side_effect=RuntimeError("router LM unavailable"))
        _stub_respond(module, response="direct fallback")
        result = module(user_request="Hello", execution_mode="auto")
        module.respond.assert_called_once()
        assert getattr(result, "response", None) == "direct fallback"

    def test_tools_route_without_tools_degrades_to_direct(self) -> None:
        module = _make_module()
        _stub_route(module, route="tools")
        _stub_respond(module, response="no tools available")
        react = MagicMock()
        module._react = react

        result = module(user_request="Use a tool", execution_mode="auto")

        react.assert_not_called()
        module.respond.assert_called_once()
        assert getattr(result, "response", None) == "no tools available"

    def test_react_tool_branch_triggered_by_router(self) -> None:
        module = _make_module()
        module._tools = [object()]
        _stub_route(module, route="tools")
        _stub_respond(module, response="unused")
        react_pred = _FakePrediction(response="tool answer")
        module._react = MagicMock(return_value=react_pred)
        module._rlm = MagicMock()
        _stub_summarize(module)

        result = module(user_request="Use a tool", execution_mode="auto")
        module.respond.assert_not_called()
        module._react.assert_called_once()
        module._rlm.assert_not_called()
        assert getattr(result, "response", None) == "tool answer"
        assert result["routing_decision"] == "tools_react"

    def test_rlm_route_chosen_by_router(self) -> None:
        module = _make_module()
        _stub_route(module, route="rlm")
        _stub_respond(module, response="unused")
        rlm_pred = _FakePrediction(response="rlm answer")
        module._rlm = MagicMock(return_value=rlm_pred)
        _stub_summarize(module)

        result = module(user_request="Analyze this repository", execution_mode="auto")
        module._rlm.assert_called_once()
        assert getattr(result, "response", None) == "rlm answer"
        assert result["routing_decision"] == "router_rlm"

    @pytest.mark.asyncio
    async def test_async_react_tool_branch_uses_acall(self) -> None:
        module = _make_module()
        module._tools = [object()]
        _stub_route(module, route="tools")
        _stub_respond(module, response="unused")
        react_agent = _AsyncReactAgent(_FakePrediction(response="async tool answer"))
        module._react = react_agent  # type: ignore[assignment]
        module._rlm = MagicMock()
        _stub_summarize(module)

        result = await module.aforward(user_request="Use an async tool", execution_mode="auto")

        module.respond.assert_not_called()
        module._rlm.assert_not_called()
        assert react_agent.acall_kwargs is not None
        assert react_agent.acall_kwargs["user_message"] == "Use an async tool"
        assert isinstance(react_agent.acall_kwargs["chat_history"], dspy.History)
        assert getattr(result, "response", None) == "async tool answer"
        assert result["routing_decision"] == "tools_react"

    def test_force_escalate_skips_cot(self) -> None:
        module = _make_module()
        _stub_respond(module)
        rlm_pred = _FakePrediction(response="forced")
        module._rlm = MagicMock(return_value=rlm_pred)
        _stub_summarize(module)

        result = module(user_request="do complex thing", force_escalate=True)
        module.respond.assert_not_called()
        module._rlm.assert_called_once()
        assert getattr(result, "response", None) == "forced"

    def test_execution_mode_rlm_skips_cot(self) -> None:
        module = _make_module()
        _stub_respond(module)
        rlm_pred = _FakePrediction(response="rlm_mode")
        module._rlm = MagicMock(return_value=rlm_pred)
        _stub_summarize(module)

        result = module(user_request="query", execution_mode="rlm")
        module.respond.assert_not_called()
        assert getattr(result, "response", None) == "rlm_mode"

    def test_execution_mode_rlm_only_skips_cot(self) -> None:
        module = _make_module()
        _stub_respond(module)
        rlm_pred = _FakePrediction(response="rlm_only_mode")
        module._rlm = MagicMock(return_value=rlm_pred)
        _stub_summarize(module)

        result = module(user_request="query", execution_mode="rlm_only")
        module.respond.assert_not_called()
        assert getattr(result, "response", None) == "rlm_only_mode"

    def test_url_document_analysis_auto_routes_to_rlm(self) -> None:
        module = _make_module()
        _stub_respond(module)
        rlm_pred = _FakePrediction(response="doc analysis")
        module._rlm = MagicMock(return_value=rlm_pred)
        _stub_summarize(module)

        result = module(
            user_request="analyze https://dspy.ai and provide an in depth analysis of the documentation",
            execution_mode="auto",
        )

        module.respond.assert_not_called()
        module._rlm.assert_called_once()
        assert getattr(result, "response", None) == "doc analysis"
        assert result["routing_decision"] == "url_document_rlm"
        assert result["source_url"] == "https://dspy.ai"

    def test_url_document_analysis_passes_fetched_doc_as_rlm_variables(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        module = _make_module(interpreter=object())
        _stub_respond(module)
        rlm_pred = _FakePrediction(response="doc analysis")
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
        document = call_kwargs["document"]
        assert document.source_url == "https://dspy.ai"
        assert document.text == "# DSPy docs\nRLM details"
        assert document.metadata == {
            "status": "ok",
            "char_count": "23",
            "source_type": "html",
        }
        assert call_kwargs["user_request"] == "analyze https://dspy.ai and provide documentation notes"
        assert "# DSPy docs\nRLM details" not in call_kwargs["core_memory"]
        assert isinstance(call_kwargs["history"], dspy.History)

    def test_tools_only_does_not_auto_route_url_to_rlm(self) -> None:
        module = _make_module()
        _stub_route(module, route="direct")
        _stub_respond(module, response="tool path")
        module._rlm = MagicMock(return_value=_FakePrediction(response="should not run"))

        result = module(
            user_request="analyze https://dspy.ai and provide documentation notes",
            execution_mode="tools_only",
        )

        module.respond.assert_called_once()
        module._rlm.assert_not_called()
        assert getattr(result, "response", None) == "tool path"

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

    def test_react_fallback_to_cot_on_error(self) -> None:
        module = _make_module()
        module._tools = [object()]
        _stub_route(module, route="tools")
        module.respond = MagicMock(return_value=_FakePrediction(response="fallback"))
        module._react = MagicMock(side_effect=RuntimeError("ReAct failed"))
        _stub_summarize(module)

        result = module(user_request="query", execution_mode="auto")
        assert getattr(result, "response", None) == "fallback"
        assert result["runtime_degraded"] is True
        assert result["runtime_failure_category"] == "react_fallback"
        assert result["runtime_failure_phase"] == "escalating_react"
        assert result["runtime_fallback_used"] is True
        assert result["runtime_warning"]
        assert result["routing_decision"] == "tools_react"

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

    def test_direct_route_does_not_touch_rlm(self) -> None:
        module = _make_module()
        _stub_route(module, route="direct")
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
                response="fallback answer",
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
                response="analysis complete",
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
    async def test_posthoc_stream_emits_chain_of_thought_reasoning(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _disable_runtime_tool_discovery(monkeypatch)
        from fleet_rlm.runtime.agent.runtime import AgentRuntime

        rt = AgentRuntime(use_escalation=True)
        rt.agent = _PosthocAgent(
            _FakePrediction(
                reasoning="The user wants a concise definition of RLM.",
                response="RLM is a recursive long-chain-of-thought framework.",
            )
        )

        events = [event async for event in rt.aiter_chat_turn_stream("Explain what is RLM")]

        reasoning = next(event for event in events if event.kind == "reasoning")
        text = next(event for event in events if event.kind == "text")
        done = events[-1]

        assert reasoning.text == "The user wants a concise definition of RLM."
        assert text.text == "RLM is a recursive long-chain-of-thought framework."
        assert done.payload["final_reasoning"] == "The user wants a concise definition of RLM."

    @pytest.mark.asyncio
    async def test_posthoc_stream_emits_routing_preview_before_result(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _disable_runtime_tool_discovery(monkeypatch)
        from fleet_rlm.runtime.agent.runtime import AgentRuntime

        rt = AgentRuntime(use_escalation=True)
        rt.agent = _PreviewPosthocAgent(_FakePrediction(response="analysis complete"))

        events = [event async for event in rt.aiter_chat_turn_stream("analyze https://dspy.ai")]

        assert events[0].text == "Starting turn..."
        assert events[1].payload == {
            "routing_decision": "url_document_rlm",
            "source_url": "https://dspy.ai",
        }
        assert "url_document_rlm" in events[1].text


class TestLargeContextRouting:
    def test_preview_routing_large_context_when_turn_context_exceeds_threshold(self, tmp_path) -> None:
        from fleet_rlm.runtime.agent.turn_context import TurnContext
        from fleet_rlm.runtime.modules.factory import VARIABLE_MODE_THRESHOLD

        module = _make_module()
        turn_context = TurnContext(
            docs_path=str(tmp_path / "large.txt"),
            estimated_chars=VARIABLE_MODE_THRESHOLD + 500,
            threshold_chars=VARIABLE_MODE_THRESHOLD,
            context_sources=[f"docs_path:{tmp_path}:large"],
        )
        preview = module.preview_routing(
            user_request="Summarize the attached documentation",
            execution_mode="auto",
            turn_context=turn_context,
        )
        assert preview["routing_decision"] == "large_context_rlm"
        assert preview["estimated_chars"] >= VARIABLE_MODE_THRESHOLD


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
            rlm_action_max_tokens=4096,
        )

        assert rt.rlm_max_iterations == 9
        assert rt.rlm_max_llm_calls == 11
        assert rt.rlm_max_output_chars == 12_345
        assert rt.rlm_action_max_tokens == 4096
