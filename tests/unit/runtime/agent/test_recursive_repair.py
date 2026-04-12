from __future__ import annotations

from types import MethodType
from typing import Any

import dspy
import pytest

from fleet_rlm.runtime.agent.chat_agent import RLMReActChatAgent
from fleet_rlm.runtime.agent.recursive_repair import (
    PlanRecursiveRepairModule,
    build_recursive_repair_inputs,
)
from fleet_rlm.runtime.agent.recursive_runtime import spawn_delegate_sub_agent_async
from tests.unit.fixtures_react import FakeInterpreter

pytestmark = pytest.mark.usefixtures("react_records")

_TEST_LARGE_BLOB_MULTIPLIER = 200


def _raise_repair_should_stay_off() -> None:
    raise AssertionError("recursive repair should stay off")


def _bind_repair_module_factory(agent: Any, factory: Any) -> None:
    agent.get_recursive_repair_module = MethodType(  # type: ignore[method-assign]
        lambda self: factory(),
        agent,
    )


def _bind_reflection_module_factory(agent: Any, factory: Any) -> None:
    agent.get_recursive_reflection_module = MethodType(  # type: ignore[method-assign]
        lambda self: factory(),
        agent,
    )


def test_recursive_repair_module_coerces_bounded_outputs() -> None:
    class _FakePredictor:
        def __call__(self, **_kwargs: Any) -> Any:
            return {
                "repair_mode": "bounded-repair-loop",
                "repair_target": "Repair the failing import path before broader retries.",
                "repair_steps": [
                    "Inspect the failing import path.",
                    "Patch only the broken import.",
                    "Run one bounded verification rerun.",
                    "Ignore this overflow step.",
                ],
                "repair_subqueries": [
                    "Inspect the failing import path",
                    "Run one bounded verification rerun",
                    "Ignore this overflow query",
                ],
                "repair_rationale": "",
            }

    module = PlanRecursiveRepairModule(predictor=_FakePredictor())

    prediction = module(
        user_request="repair the recursive failure",
        assembled_recursive_context="Use only the staged traceback summary.",
        verification_summary="Verification says the result still needs repair.",
        latest_sandbox_evidence="workspace_path=/workspace/repo",
        latest_failure_signals="missing_evidence=Run one bounded verification step.",
        repair_budget=2,
    )

    assert prediction.repair_mode == "bounded_repair_loop"
    assert prediction.repair_steps == [
        "Inspect the failing import path.",
        "Patch only the broken import.",
        "Run one bounded verification rerun.",
    ]
    assert prediction.repair_subqueries == [
        "Inspect the failing import path",
        "Run one bounded verification rerun",
    ]
    assert prediction.repair_target.startswith("Repair the failing import path")
    assert prediction.repair_rationale


def test_build_recursive_repair_inputs_stays_summary_only() -> None:
    inputs = build_recursive_repair_inputs(
        user_request="Repair the workspace",
        assembled_recursive_context="Use only the staged traceback and memory handle.",
        latest_result={
            "answer": "Updated the import path and reran one bounded check.",
            "final_reasoning": "The repair is plausible but not fully confirmed.",
            "recursive_verification": {
                "verification_status": "needs_repair",
                "missing_evidence": ["Run one bounded verification step."],
                "contradictions": [
                    "The repair landed but the rerun still fails elsewhere."
                ],
                "verified_summary": "One narrow repair exists but needs one more bounded check.",
                "verification_rationale": "The current result is coherent but still incomplete.",
            },
        },
        runtime_metadata={
            "volume_name": "tenant-a",
            "workspace_path": "/workspace/repo",
            "sandbox_id": "sbx-123",
            "memory_handle": "meta/workspaces/tenant-a/users/u/react-session.json",
            "memory_blob": "SECRET" * _TEST_LARGE_BLOB_MULTIPLIER,
        },
        reflection_decision=type(
            "_Decision",
            (),
            {
                "next_action": "repair_and_retry",
                "revised_plan": "Repair the import path and rerun one bounded check.",
                "rationale": "The failure is narrow enough for focused repair.",
            },
        )(),
        repair_budget=2,
        recursion_depth=1,
        max_depth=4,
    )

    assert "One narrow repair exists" in inputs.verification_summary
    assert "workspace_path=/workspace/repo" in inputs.latest_sandbox_evidence
    assert "missing_evidence=Run one bounded verification step." in (
        inputs.latest_failure_signals
    )
    assert "SECRET" not in inputs.latest_sandbox_evidence
    assert "SECRET" not in inputs.verification_summary
    assert inputs.repair_budget == 2


@pytest.mark.asyncio
async def test_spawn_delegate_sub_agent_async_preserves_generic_retry_when_repair_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = RLMReActChatAgent(
        interpreter=FakeInterpreter(),
        recursive_reflection_enabled=True,
        recursive_repair_enabled=False,
    )
    agent.prepare_routed_turn()

    calls: list[tuple[str, str]] = []

    class _FakeChildModule:
        async def acall(self, *, prompt: str, context: str) -> dspy.Prediction:
            calls.append((prompt, context))
            return dspy.Prediction(answer=f"{prompt} done", trajectory={})

    class _FakeReflectionModule:
        async def acall(self, **_kwargs: Any) -> dict[str, Any]:
            return {
                "next_action": "repair_and_retry",
                "revised_plan": "Retry after a narrow repair.",
                "rationale": "The failure is recoverable.",
                "confidence": 0.9,
            }

    monkeypatch.setattr(
        "fleet_rlm.runtime.models.builders.build_recursive_subquery_rlm",
        lambda **_kwargs: _FakeChildModule(),
    )
    _bind_repair_module_factory(agent, _raise_repair_should_stay_off)
    _bind_reflection_module_factory(agent, _FakeReflectionModule)

    result = await spawn_delegate_sub_agent_async(
        agent, prompt="inspect", context="ctx"
    )

    assert result["status"] == "ok"
    assert len(calls) == 2
    assert calls[1][0] == "inspect"
    assert "Recursive reflection guidance:" in calls[1][1]
    assert "Recursive repair plan:" not in calls[1][1]


@pytest.mark.asyncio
async def test_spawn_delegate_sub_agent_async_executes_bounded_recursive_repair_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = RLMReActChatAgent(
        interpreter=FakeInterpreter(),
        recursive_reflection_enabled=True,
        recursive_repair_enabled=True,
    )
    agent.prepare_routed_turn()
    setattr(
        agent.interpreter,
        "current_runtime_metadata",
        lambda: {
            "workspace_path": "/workspace/repo",
            "memory_handle": "meta/workspaces/tenant-a/users/u/react-session.json",
            "memory_blob": "SECRET" * _TEST_LARGE_BLOB_MULTIPLIER,
        },
    )

    calls: list[tuple[str, str]] = []

    class _FakeChildModule:
        async def acall(self, *, prompt: str, context: str) -> dspy.Prediction:
            calls.append((prompt, context))
            return dspy.Prediction(
                answer=f"{prompt} done",
                trajectory={"trajectory": [{"thought": prompt}]},
                final_reasoning=f"{prompt} finished",
            )

    class _FakeReflectionModule:
        async def acall(self, **_kwargs: Any) -> dict[str, Any]:
            return {
                "next_action": "repair_and_retry",
                "revised_plan": "Repair the import path and run one bounded rerun.",
                "rationale": "The failure is narrow enough for bounded repair.",
                "confidence": 0.9,
            }

    class _FakeRepairModule:
        async def acall(self, **kwargs: Any) -> dict[str, Any]:
            assert "SECRET" not in kwargs["latest_sandbox_evidence"]
            assert "SECRET" not in kwargs["verification_summary"]
            return {
                "repair_mode": "bounded_repair_loop",
                "repair_target": "Repair the failing import path.",
                "repair_steps": [
                    "Inspect the failing import path.",
                    "Patch only the broken import.",
                    "Run one bounded verification rerun.",
                ],
                "repair_subqueries": [
                    "Inspect the failing import path",
                    "Run one bounded verification rerun",
                ],
                "repair_rationale": "Use the narrow traceback evidence before broader recursion.",
            }

    monkeypatch.setattr(
        "fleet_rlm.runtime.models.builders.build_recursive_subquery_rlm",
        lambda **_kwargs: _FakeChildModule(),
    )
    _bind_reflection_module_factory(agent, _FakeReflectionModule)
    _bind_repair_module_factory(agent, _FakeRepairModule)

    result = await spawn_delegate_sub_agent_async(
        agent,
        prompt="repair the recursive failure",
        context="Use only the staged traceback summary.",
    )

    assert result["status"] == "ok"
    assert [prompt for prompt, _ in calls] == [
        "repair the recursive failure",
        "Inspect the failing import path",
        "Run one bounded verification rerun",
    ]
    assert all("Recursive repair plan:" in context for _, context in calls[1:])
    assert result["recursive_repair"]["repair_mode"] == "bounded_repair_loop"
    assert "Recursive repair executed bounded_repair_loop" in result["final_reasoning"]
    assert "Recursive reflection chose repair_and_retry" in result["final_reasoning"]


@pytest.mark.asyncio
async def test_spawn_delegate_sub_agent_async_does_not_run_repair_when_reflection_recurses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = RLMReActChatAgent(
        interpreter=FakeInterpreter(),
        recursive_reflection_enabled=True,
        recursive_repair_enabled=True,
    )
    agent.prepare_routed_turn()

    calls: list[tuple[str, str]] = []

    class _FakeChildModule:
        async def acall(self, *, prompt: str, context: str) -> dspy.Prediction:
            calls.append((prompt, context))
            return dspy.Prediction(answer=f"{prompt} done", trajectory={})

    class _FakeReflectionModule:
        async def acall(self, **_kwargs: Any) -> dict[str, Any]:
            return {
                "next_action": "recurse",
                "revised_plan": "Refine the recursive context and recurse once more.",
                "rationale": "A broader recursive pass is needed before repair.",
                "confidence": 0.8,
            }

    monkeypatch.setattr(
        "fleet_rlm.runtime.models.builders.build_recursive_subquery_rlm",
        lambda **_kwargs: _FakeChildModule(),
    )
    _bind_repair_module_factory(agent, _raise_repair_should_stay_off)
    _bind_reflection_module_factory(agent, _FakeReflectionModule)

    result = await spawn_delegate_sub_agent_async(
        agent,
        prompt="repair the recursive failure",
        context="Use only the staged traceback summary.",
    )

    assert result["status"] == "ok"
    assert len(calls) == 2
    assert "Recursive reflection guidance:" in calls[1][1]
    assert "Recursive repair plan:" not in calls[1][1]
    assert "recursive_repair" not in result


@pytest.mark.asyncio
async def test_spawn_delegate_sub_agent_async_propagates_recursive_repair_task_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = RLMReActChatAgent(
        interpreter=FakeInterpreter(),
        recursive_reflection_enabled=True,
        recursive_repair_enabled=True,
    )
    agent.prepare_routed_turn()

    calls: list[tuple[str, str]] = []

    class _FakeChildModule:
        async def acall(self, *, prompt: str, context: str) -> dspy.Prediction:
            calls.append((prompt, context))
            if prompt == "Run one bounded verification rerun":
                raise RuntimeError("verification rerun failed")
            return dspy.Prediction(
                answer=f"{prompt} done",
                trajectory={"trajectory": [{"thought": prompt}]},
                final_reasoning=f"{prompt} finished",
            )

    class _FakeReflectionModule:
        async def acall(self, **_kwargs: Any) -> dict[str, Any]:
            return {
                "next_action": "repair_and_retry",
                "revised_plan": "Repair the import path and run one bounded rerun.",
                "rationale": "The failure is narrow enough for bounded repair.",
                "confidence": 0.9,
            }

    class _FakeRepairModule:
        async def acall(self, **_kwargs: Any) -> dict[str, Any]:
            return {
                "repair_mode": "bounded_repair_loop",
                "repair_target": "Repair the failing import path.",
                "repair_steps": [
                    "Inspect the failing import path.",
                    "Run one bounded verification rerun.",
                ],
                "repair_subqueries": [
                    "Inspect the failing import path",
                    "Run one bounded verification rerun",
                    "This task should never run",
                ],
                "repair_rationale": "Use the narrow traceback evidence before broader recursion.",
            }

    monkeypatch.setattr(
        "fleet_rlm.runtime.models.builders.build_recursive_subquery_rlm",
        lambda **_kwargs: _FakeChildModule(),
    )
    _bind_reflection_module_factory(agent, _FakeReflectionModule)
    _bind_repair_module_factory(agent, _FakeRepairModule)

    result = await spawn_delegate_sub_agent_async(
        agent,
        prompt="repair the recursive failure",
        context="Use only the staged traceback summary.",
    )

    assert [prompt for prompt, _ in calls] == [
        "repair the recursive failure",
        "Inspect the failing import path",
        "Run one bounded verification rerun",
    ]
    assert result["status"] == "error"
    assert "Recursive repair failed:" in result["error"]
    assert "verification rerun failed" in result["error"]
    assert "This task should never run" not in result["answer"]
    assert result["recursive_repair"]["repair_mode"] == "bounded_repair_loop"


@pytest.mark.asyncio
async def test_spawn_delegate_sub_agent_async_preserves_human_review_path_from_repair_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = RLMReActChatAgent(
        interpreter=FakeInterpreter(),
        recursive_reflection_enabled=True,
        recursive_repair_enabled=True,
    )
    agent.prepare_routed_turn()

    calls: list[tuple[str, str]] = []

    class _FakeChildModule:
        async def acall(self, *, prompt: str, context: str) -> dspy.Prediction:
            calls.append((prompt, context))
            return dspy.Prediction(answer=f"{prompt} done", trajectory={})

    class _FakeReflectionModule:
        async def acall(self, **_kwargs: Any) -> dict[str, Any]:
            return {
                "next_action": "repair_and_retry",
                "revised_plan": "Pause retries and request human review for the risky repair.",
                "rationale": "The worker should not keep retrying once the repair becomes risky.",
                "confidence": 0.85,
            }

    class _FakeRepairModule:
        async def acall(self, **_kwargs: Any) -> dict[str, Any]:
            return {
                "repair_mode": "needs_human_review",
                "repair_target": "Review the risky filesystem mutation before another retry.",
                "repair_steps": [
                    "Summarize the risky workspace mutation for human review.",
                ],
                "repair_subqueries": [],
                "repair_rationale": "The remaining repair path is too risky for another automated recursive pass.",
            }

    monkeypatch.setattr(
        "fleet_rlm.runtime.models.builders.build_recursive_subquery_rlm",
        lambda **_kwargs: _FakeChildModule(),
    )
    _bind_reflection_module_factory(agent, _FakeReflectionModule)
    _bind_repair_module_factory(agent, _FakeRepairModule)

    result = await spawn_delegate_sub_agent_async(
        agent,
        prompt="repair the recursive failure",
        context="Use only the staged traceback summary.",
    )

    assert result["status"] == "ok"
    assert [prompt for prompt, _ in calls] == ["repair the recursive failure"]
    assert result["recursive_repair"]["repair_mode"] == "needs_human_review"
    assert (
        "Recursive repair planning chose needs_human_review."
        in result["final_reasoning"]
    )
    assert "Recursive reflection guidance:" not in calls[0][1]
