from __future__ import annotations

from types import MethodType, SimpleNamespace
from typing import Any

import dspy
import pytest

from fleet_rlm.runtime.agent.chat_agent import RLMReActChatAgent
from fleet_rlm.runtime.agent.recursive_reflection import (
    ReflectAndReviseWorkspaceStepModule,
    build_workspace_reflection_inputs,
)
from fleet_rlm.runtime.agent.recursive_runtime import spawn_delegate_sub_agent_async
from tests.unit.fixtures_react import FakeInterpreter

pytestmark = pytest.mark.usefixtures("react_records")


def _raise_reflection_should_stay_off() -> None:
    raise AssertionError("reflection should stay off")


def _bind_reflection_module_factory(agent: Any, factory: Any) -> None:
    agent.get_recursive_reflection_module = MethodType(  # type: ignore[method-assign]
        lambda self: factory(),
        agent,
    )


def _bind_decomposition_module_factory(agent: Any, factory: Any) -> None:
    agent.get_recursive_decomposition_module = MethodType(  # type: ignore[method-assign]
        lambda self: factory(),
        agent,
    )


def _bind_context_selection_module_factory(agent: Any, factory: Any) -> None:
    agent.get_recursive_context_selection_module = MethodType(  # type: ignore[method-assign]
        lambda self: factory(),
        agent,
    )


def test_reflection_module_coerces_typed_outputs() -> None:
    class _FakePredictor:
        def __call__(self, **_kwargs: Any) -> Any:
            return SimpleNamespace(
                next_action="repair_and_retry",
                revised_plan="Retry after repairing the failing sandbox step.",
                rationale="The latest tool result shows a recoverable failure.",
                confidence="1.4",
            )

    module = ReflectAndReviseWorkspaceStepModule(predictor=_FakePredictor())

    prediction = module(
        user_request="fix it",
        working_memory_summary="volume_name=ws-1",
        current_plan="repair and rerun",
        latest_sandbox_evidence="traceback snippet",
        latest_tool_or_code_result="pytest failed",
        loop_state="depth=1",
    )

    assert prediction.next_action == "repair_and_retry"
    assert prediction.revised_plan.startswith("Retry after repairing")
    assert prediction.rationale.startswith("The latest tool result")
    assert prediction.confidence == 1.0


def test_build_workspace_reflection_inputs_stays_summary_only() -> None:
    inputs = build_workspace_reflection_inputs(
        user_request="Investigate the workspace",
        current_plan="Inspect the failing command and repair it.",
        latest_result={
            "answer": "x" * 1200,
            "trajectory": {"trajectory": [{"thought": "inspect"}]},
            "final_reasoning": "Found a likely syntax error in the generated code.",
        },
        runtime_metadata={
            "volume_name": "tenant-a",
            "workspace_path": "/workspace/repo",
            "sandbox_id": "sbx-123",
            "memory_handle": "meta/workspaces/tenant-a/users/u/react-session.json",
        },
        recursion_depth=1,
        max_depth=3,
        reflection_passes=0,
        fallback_used=False,
    )

    assert "volume_name=tenant-a" in inputs.working_memory_summary
    assert "memory_handle=meta/workspaces/tenant-a" in inputs.working_memory_summary
    assert inputs.latest_tool_or_code_result.endswith("...")
    assert "recursion_depth=1" in inputs.loop_state


@pytest.mark.asyncio
async def test_spawn_delegate_sub_agent_async_skips_reflection_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = RLMReActChatAgent(
        interpreter=FakeInterpreter(),
        recursive_reflection_enabled=False,
    )
    agent.prepare_routed_turn()

    class _FakeChildModule:
        async def acall(self, *, prompt: str, context: str) -> dspy.Prediction:
            assert prompt == "inspect"
            assert context == "ctx"
            return dspy.Prediction(answer="done", trajectory={})

    monkeypatch.setattr(
        "fleet_rlm.runtime.models.builders.build_recursive_subquery_rlm",
        lambda **_kwargs: _FakeChildModule(),
    )
    _bind_reflection_module_factory(agent, _raise_reflection_should_stay_off)

    result = await spawn_delegate_sub_agent_async(
        agent, prompt="inspect", context="ctx"
    )

    assert result["status"] == "ok"
    assert result["answer"] == "done"


@pytest.mark.asyncio
async def test_spawn_delegate_sub_agent_async_uses_recursive_decomposition_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = RLMReActChatAgent(
        interpreter=FakeInterpreter(),
        recursive_decomposition_enabled=True,
    )
    agent.interpreter.max_llm_calls = 10
    agent.batch_concurrency = 2
    agent.prepare_routed_turn()
    setattr(
        agent.interpreter,
        "current_runtime_metadata",
        lambda: {
            "workspace_path": "/workspace/repo",
            "memory_handle": "meta/workspaces/tenant-a/users/u/react-session.json",
            "memory_blob": "SECRET" * 200,
        },
    )

    calls: list[tuple[str, str]] = []
    decomposition_kwargs: dict[str, Any] = {}

    class _FakeChildModule:
        async def acall(self, *, prompt: str, context: str) -> dspy.Prediction:
            calls.append((prompt, context))
            return dspy.Prediction(
                answer=f"{prompt} done",
                trajectory={"trajectory": [{"thought": prompt}]},
                final_reasoning=f"{prompt} finished",
            )

    class _FakeDecompositionModule:
        async def acall(self, **kwargs: Any) -> dict[str, Any]:
            decomposition_kwargs.update(kwargs)
            return {
                "decomposition_mode": "fan_out",
                "subqueries": [
                    "Inspect the traceback",
                    "Repair the failing import path",
                    "Summarize the bounded fix",
                ],
                "batching_strategy": "batched",
                "aggregation_plan": "Combine the bounded findings in Python.",
                "decomposition_rationale": "Split the repair into bounded semantic work.",
            }

    monkeypatch.setattr(
        "fleet_rlm.runtime.models.builders.build_recursive_subquery_rlm",
        lambda **_kwargs: _FakeChildModule(),
    )
    _bind_decomposition_module_factory(agent, _FakeDecompositionModule)

    result = await spawn_delegate_sub_agent_async(
        agent,
        prompt="repair the recursive failure",
        context="Use only the staged traceback summary.",
    )

    assert result["status"] == "ok"
    assert len(calls) == 2
    assert calls[0][0] == "Inspect the traceback"
    assert calls[1][0] == "Repair the failing import path"
    assert all("Recursive decomposition plan:" in item[1] for item in calls)
    assert "Summarize the bounded fix" not in result["answer"]
    assert (
        "memory_handle=meta/workspaces/tenant-a/users/u/react-session.json"
        in (decomposition_kwargs["latest_sandbox_evidence"])
    )
    assert "SECRET" not in decomposition_kwargs["assembled_recursive_context"]
    assert "SECRET" not in decomposition_kwargs["latest_sandbox_evidence"]
    assert result["trajectory_truncated"] is False
    assert "Recursive decomposition chose fan_out" in result["final_reasoning"]
    assert agent._turn_delegation_state.recursive_delegate_calls_turn == 2


@pytest.mark.asyncio
async def test_spawn_delegate_sub_agent_async_fan_out_consumes_delegate_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = RLMReActChatAgent(
        interpreter=FakeInterpreter(),
        recursive_decomposition_enabled=True,
        delegate_max_calls_per_turn=2,
    )
    agent.interpreter.max_llm_calls = 10
    agent.batch_concurrency = 2
    agent.prepare_routed_turn()

    class _FakeChildModule:
        async def acall(self, *, prompt: str, context: str) -> dspy.Prediction:
            return dspy.Prediction(answer=f"{prompt} done", trajectory={})

    class _FakeDecompositionModule:
        async def acall(self, **_kwargs: Any) -> dict[str, Any]:
            return {
                "decomposition_mode": "fan_out",
                "subqueries": [
                    "Inspect the traceback",
                    "Repair the failing import path",
                ],
                "batching_strategy": "batched",
                "aggregation_plan": "Combine the bounded findings in Python.",
                "decomposition_rationale": "Split the repair into bounded semantic work.",
            }

    monkeypatch.setattr(
        "fleet_rlm.runtime.models.builders.build_recursive_subquery_rlm",
        lambda **_kwargs: _FakeChildModule(),
    )
    _bind_decomposition_module_factory(agent, _FakeDecompositionModule)

    first_result = await spawn_delegate_sub_agent_async(
        agent,
        prompt="repair the recursive failure",
        context="Use only the staged traceback summary.",
    )
    assert first_result["status"] == "ok"
    assert agent._turn_delegation_state.recursive_delegate_calls_turn == 2

    second_result = await spawn_delegate_sub_agent_async(
        agent,
        prompt="one more delegate call",
        context="ctx",
    )

    assert second_result["status"] == "error"
    assert second_result["delegate_max_calls_per_turn"] == 2


@pytest.mark.asyncio
async def test_spawn_delegate_sub_agent_async_falls_back_to_single_pass_when_decomposition_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = RLMReActChatAgent(
        interpreter=FakeInterpreter(),
        recursive_decomposition_enabled=True,
    )
    agent.interpreter.max_llm_calls = 10
    agent.prepare_routed_turn()

    calls: list[tuple[str, str]] = []

    class _FakeChildModule:
        async def acall(self, *, prompt: str, context: str) -> dspy.Prediction:
            calls.append((prompt, context))
            return dspy.Prediction(answer="done", trajectory={})

    class _FailingDecompositionModule:
        async def acall(self, **_kwargs: Any) -> dict[str, Any]:
            raise RuntimeError("planner unavailable")

    monkeypatch.setattr(
        "fleet_rlm.runtime.models.builders.build_recursive_subquery_rlm",
        lambda **_kwargs: _FakeChildModule(),
    )
    _bind_decomposition_module_factory(agent, _FailingDecompositionModule)

    result = await spawn_delegate_sub_agent_async(
        agent, prompt="inspect", context="ctx"
    )

    assert result["status"] == "ok"
    assert calls == [("inspect", "ctx")]


@pytest.mark.asyncio
async def test_spawn_delegate_sub_agent_async_uses_single_pass_when_decomposition_returns_no_subqueries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = RLMReActChatAgent(
        interpreter=FakeInterpreter(),
        recursive_decomposition_enabled=True,
    )
    agent.interpreter.max_llm_calls = 10
    agent.prepare_routed_turn()

    calls: list[tuple[str, str]] = []

    class _FakeChildModule:
        async def acall(self, *, prompt: str, context: str) -> dspy.Prediction:
            calls.append((prompt, context))
            return dspy.Prediction(answer="done", trajectory={})

    class _EmptyDecompositionModule:
        async def acall(self, **_kwargs: Any) -> dict[str, Any]:
            return {
                "decomposition_mode": "fan_out",
                "subqueries": [],
                "batching_strategy": "batched",
                "aggregation_plan": "Combine findings in Python.",
                "decomposition_rationale": "No safe split is available.",
            }

    monkeypatch.setattr(
        "fleet_rlm.runtime.models.builders.build_recursive_subquery_rlm",
        lambda **_kwargs: _FakeChildModule(),
    )
    _bind_decomposition_module_factory(agent, _EmptyDecompositionModule)

    result = await spawn_delegate_sub_agent_async(
        agent, prompt="inspect", context="ctx"
    )

    assert result["status"] == "ok"
    assert calls == [("inspect", "ctx")]


@pytest.mark.asyncio
async def test_spawn_delegate_sub_agent_async_can_retry_once_from_reflection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = RLMReActChatAgent(
        interpreter=FakeInterpreter(),
        max_depth=4,
        delegate_max_calls_per_turn=4,
        recursive_reflection_enabled=True,
        recursive_context_selection_enabled=True,
    )
    agent.prepare_routed_turn()
    setattr(
        agent.interpreter,
        "current_runtime_metadata",
        lambda: {
            "volume_name": "tenant-a",
            "workspace_path": "/workspace/repo",
            "memory_handle": "meta/workspaces/tenant-a/users/u/react-session.json",
        },
    )

    attempts: list[tuple[str, str]] = []
    answers = iter(["first attempt", "recovered answer"])

    class _FakeChildModule:
        async def acall(self, *, prompt: str, context: str) -> dspy.Prediction:
            attempts.append((prompt, context))
            return dspy.Prediction(
                answer=next(answers),
                trajectory={"trajectory": [{"thought": f"run {len(attempts)}"}]},
                final_reasoning="delegate finished",
            )

    class _FakeReflectionModule:
        async def acall(self, **kwargs: Any) -> dict[str, Any]:
            assert kwargs["user_request"] == "inspect"
            assert (
                "memory_handle=meta/workspaces/tenant-a/users/u/react-session.json"
                in kwargs["working_memory_summary"]
            )
            assert (
                "Selected traceback summary only." in kwargs["latest_sandbox_evidence"]
            )
            assert "answer | first attempt" not in kwargs["latest_sandbox_evidence"]
            return {
                "next_action": "repair_and_retry",
                "revised_plan": "Fix the last failing step and rerun only the repair path.",
                "rationale": "The first answer is incomplete and should be repaired.",
                "confidence": 0.92,
            }

    class _FakeContextSelectionModule:
        async def acall(self, **kwargs: Any) -> dict[str, Any]:
            assert kwargs["user_request"] == "inspect"
            assert any(
                entry.startswith(
                    "memory_handle=meta/workspaces/tenant-a/users/u/react-session.json"
                )
                for entry in kwargs["working_memory_catalog"]
            )
            return {
                "selected_memory_handles": [
                    "memory_handle=meta/workspaces/tenant-a/users/u/react-session.json"
                ],
                "selected_evidence_ids": ["final_reasoning"],
                "assembled_context_summary": "Selected traceback summary only.",
                "omission_rationale": "Skip the broader answer text to stay within budget.",
            }

    monkeypatch.setattr(
        "fleet_rlm.runtime.models.builders.build_recursive_subquery_rlm",
        lambda **_kwargs: _FakeChildModule(),
    )
    _bind_reflection_module_factory(agent, _FakeReflectionModule)
    _bind_context_selection_module_factory(agent, _FakeContextSelectionModule)

    result = await spawn_delegate_sub_agent_async(
        agent, prompt="inspect", context="ctx"
    )

    assert result["status"] == "ok"
    assert result["answer"] == "recovered answer"
    assert len(attempts) == 2
    assert attempts[1][0] == "inspect"
    assert "Recursive context assembly:" in attempts[1][1]
    assert (
        "memory_handle=meta/workspaces/tenant-a/users/u/react-session.json"
        in attempts[1][1]
    )
    assert "answer | first attempt" not in attempts[1][1]
    assert "Recursive reflection guidance:" in attempts[1][1]
    assert "repair_and_retry" in result["final_reasoning"]
    assert agent._turn_delegation_state.recursive_delegate_calls_turn == 2


@pytest.mark.asyncio
async def test_spawn_delegate_sub_agent_async_runs_reflection_with_delegate_lm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = RLMReActChatAgent(
        interpreter=FakeInterpreter(),
        recursive_reflection_enabled=True,
    )
    agent.prepare_routed_turn()
    setattr(agent.interpreter, "current_runtime_metadata", lambda: {})

    parent_lm_marker = object()
    delegate_lm_marker = object()
    agent.delegate_lm = delegate_lm_marker

    class _FakeChildModule:
        async def acall(self, *, prompt: str, context: str) -> dspy.Prediction:
            assert dspy.settings.lm is delegate_lm_marker
            assert prompt == "inspect"
            assert context == "ctx"
            return dspy.Prediction(answer="done", trajectory={})

    class _FakeReflectionModule:
        async def acall(self, **_kwargs: Any) -> dict[str, Any]:
            assert dspy.settings.lm is delegate_lm_marker
            return {
                "next_action": "finalize",
                "revised_plan": "done",
                "rationale": "Delegate LM context should stay active for reflection.",
                "confidence": 0.8,
            }

    monkeypatch.setattr(
        "fleet_rlm.runtime.models.builders.build_recursive_subquery_rlm",
        lambda **_kwargs: _FakeChildModule(),
    )
    monkeypatch.setattr(
        "fleet_rlm.runtime.agent.recursive_runtime.build_dspy_context",
        lambda *, lm: dspy.context(lm=lm),
    )
    _bind_reflection_module_factory(agent, _FakeReflectionModule)

    with dspy.context(lm=parent_lm_marker):
        result = await spawn_delegate_sub_agent_async(
            agent, prompt="inspect", context="ctx"
        )

    assert result["status"] == "ok"
    assert result["answer"] == "done"


@pytest.mark.asyncio
async def test_spawn_delegate_sub_agent_async_runs_reflection_with_parent_lm_after_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = RLMReActChatAgent(
        interpreter=FakeInterpreter(),
        recursive_reflection_enabled=True,
    )
    agent.prepare_routed_turn()
    setattr(agent.interpreter, "current_runtime_metadata", lambda: {})

    parent_lm_marker = object()
    delegate_lm_marker = object()
    agent.delegate_lm = delegate_lm_marker
    child_calls = 0

    class _FakeChildModule:
        async def acall(self, *, prompt: str, context: str) -> dspy.Prediction:
            nonlocal child_calls
            child_calls += 1
            if child_calls == 1:
                assert dspy.settings.lm is delegate_lm_marker
                raise RuntimeError("delegate lm failed")
            assert dspy.settings.lm is parent_lm_marker
            assert prompt == "inspect"
            assert context == "ctx"
            return dspy.Prediction(answer="done", trajectory={})

    class _FakeReflectionModule:
        async def acall(self, **_kwargs: Any) -> dict[str, Any]:
            assert dspy.settings.lm is parent_lm_marker
            return {
                "next_action": "finalize",
                "revised_plan": "done",
                "rationale": "Fallback should reuse the parent LM for reflection.",
                "confidence": 0.8,
            }

    monkeypatch.setattr(
        "fleet_rlm.runtime.models.builders.build_recursive_subquery_rlm",
        lambda **_kwargs: _FakeChildModule(),
    )
    monkeypatch.setattr(
        "fleet_rlm.runtime.agent.recursive_runtime.build_dspy_context",
        lambda *, lm: dspy.context(lm=lm),
    )
    _bind_reflection_module_factory(agent, _FakeReflectionModule)

    with dspy.context(lm=parent_lm_marker):
        result = await spawn_delegate_sub_agent_async(
            agent, prompt="inspect", context="ctx"
        )

    assert result["status"] == "ok"
    assert result["answer"] == "done"
    assert result["delegate_lm_fallback"] is True
