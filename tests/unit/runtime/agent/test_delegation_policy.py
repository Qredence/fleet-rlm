"""Unit tests for the shared ReAct delegation policy helpers."""

from __future__ import annotations

import pytest

from fleet_rlm.runtime.agent import RLMReActChatAgent
from fleet_rlm.runtime.agent.delegation_policy import (
    claim_delegate_slot_or_error,
    invoke_runtime_module,
    normalize_delegate_result,
    RuntimeModuleExecutionRequest,
)
from tests.unit.fixtures_react import FakeInterpreter

pytestmark = pytest.mark.usefixtures("react_records")


def test_claim_delegate_slot_or_error_respects_depth_guard() -> None:
    agent = RLMReActChatAgent(
        interpreter=FakeInterpreter(),
        max_depth=1,
        current_depth=1,
    )

    error = claim_delegate_slot_or_error(
        agent,
        depth_error_suffix="Cannot spawn delegate sub-agent.",
    )

    assert error is not None
    assert error["status"] == "error"
    assert "Max recursion depth (1) reached" in error["error"]


def test_invoke_runtime_module_records_budget_and_fallback() -> None:
    agent = RLMReActChatAgent(
        interpreter=FakeInterpreter(),
        delegate_max_calls_per_turn=2,
    )
    agent._prepare_turn("inspect runtime module")
    agent.get_runtime_module = lambda _name: (  # type: ignore[method-assign]
        lambda **_kwargs: {"answer": "ok"}
    )

    result = invoke_runtime_module(
        RuntimeModuleExecutionRequest(
            agent=agent,
            module_name="summarize_long_document",
            module_kwargs={"document": "doc", "query": "q"},
        )
    )

    assert result.error is None
    assert result.prediction == {"answer": "ok"}
    assert result.fallback_used is True
    assert agent._turn_delegation_state.delegate_calls_turn == 1
    assert agent._turn_delegation_state.runtime_module_calls_turn == 1
    assert agent._turn_delegation_state.recursive_delegate_calls_turn == 0
    assert agent._turn_delegation_state.delegate_fallback_count_turn == 1


def test_invoke_runtime_module_uses_context_builder_for_module(monkeypatch) -> None:
    agent = RLMReActChatAgent(interpreter=FakeInterpreter())
    agent._prepare_turn("structured runtime module")
    agent.delegate_lm = object()
    captured: dict[str, object] = {}

    class _FakeContext:
        def __enter__(self):
            captured["entered"] = True
            return self

        def __exit__(self, exc_type, exc, tb):
            captured["exited"] = True
            return False

    monkeypatch.setattr(
        "fleet_rlm.runtime.agent.delegation_policy.build_dspy_context",
        lambda *, lm=None, module_name=None: (
            captured.update({"lm": lm, "module_name": module_name}) or _FakeContext()
        ),
    )
    agent.get_runtime_module = lambda _name: (  # type: ignore[method-assign]
        lambda **_kwargs: {"answer": "ok"}
    )

    result = invoke_runtime_module(
        RuntimeModuleExecutionRequest(
            agent=agent,
            module_name="grounded_answer",
            module_kwargs={"document": "doc", "query": "q"},
        )
    )

    assert result.error is None
    assert captured["module_name"] == "grounded_answer"
    assert captured["entered"] is True
    assert captured["exited"] is True


def test_normalize_delegate_result_truncates_and_tracks_counter() -> None:
    agent = RLMReActChatAgent(
        interpreter=FakeInterpreter(),
        delegate_result_truncation_chars=256,
    )
    agent._prepare_turn("truncate child output")
    raw_answer = "x" * 300

    payload = normalize_delegate_result(
        agent=agent,
        raw_result={"answer": raw_answer},
        fallback_used=False,
    )

    assert payload["delegate_output_truncated"] is True
    assert payload["answer"].endswith("[truncated delegate output]")
    assert payload["assistant_response"] == payload["answer"]
    assert agent._turn_delegation_state.delegate_result_truncated_count_turn == 1
