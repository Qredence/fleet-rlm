from __future__ import annotations

from typing import Any

from fleet_rlm.runtime.agent.recursive_decomposition import (
    PlanRecursiveSubqueriesModule,
    build_recursive_decomposition_inputs,
    coerce_recursive_decomposition_decision,
)


def test_recursive_decomposition_module_coerces_bounded_outputs() -> None:
    class _FakePredictor:
        def __call__(self, **_kwargs: Any) -> Any:
            return {
                "decomposition_mode": "fan-out",
                "subqueries": [
                    "Inspect the traceback",
                    "Repair the import path",
                    "Summarize the bounded fix",
                    "Ignore this overflow query",
                ],
                "batching_strategy": "parallel",
                "aggregation_plan": "Combine the bounded findings in Python.",
                "decomposition_rationale": "",
            }

    module = PlanRecursiveSubqueriesModule(predictor=_FakePredictor())

    prediction = module(
        user_request="Repair the failing recursive workspace step",
        assembled_recursive_context="Only the bounded traceback summary is available.",
        current_plan="Inspect, repair, and summarize the fix.",
        loop_state="recursion_depth=1; max_depth=3",
        latest_sandbox_evidence="workspace_path=/workspace/repo",
        subquery_budget=3,
    )

    assert prediction.decomposition_mode == "fan_out"
    assert prediction.subqueries == [
        "Inspect the traceback",
        "Repair the import path",
        "Summarize the bounded fix",
    ]
    assert prediction.batching_strategy == "batched"
    assert prediction.aggregation_plan.startswith("Combine the bounded findings")
    assert prediction.decomposition_rationale


def test_build_recursive_decomposition_inputs_stays_summary_only() -> None:
    inputs = build_recursive_decomposition_inputs(
        user_request="Repair the workspace",
        current_plan="Inspect the failing recursive path.",
        assembled_recursive_context="Use only the staged traceback and memory handle.",
        runtime_metadata={
            "volume_name": "tenant-a",
            "workspace_path": "/workspace/repo",
            "sandbox_id": "sbx-123",
            "memory_handle": "meta/workspaces/tenant-a/users/u/react-session.json",
            "memory_blob": "SECRET" * 200,
        },
        recursion_depth=1,
        max_depth=4,
        fallback_used=False,
        subquery_budget=3,
        interpreter_context_paths=["src/fleet_rlm/runtime/agent"],
    )

    assert "Use only the staged traceback" in inputs.assembled_recursive_context
    assert "volume_name=tenant-a" in inputs.latest_sandbox_evidence
    assert "memory_handle=meta/workspaces/tenant-a/users/u/react-session.json" in (
        inputs.latest_sandbox_evidence
    )
    assert "SECRET" not in inputs.assembled_recursive_context
    assert "SECRET" not in inputs.latest_sandbox_evidence
    assert inputs.subquery_budget == 3


def test_coerce_recursive_decomposition_decision_defaults_to_single_pass() -> None:
    decision = coerce_recursive_decomposition_decision(
        {
            "decomposition_mode": "unknown",
            "subqueries": [],
            "batching_strategy": "unknown",
            "aggregation_plan": "",
            "decomposition_rationale": "",
        },
        fallback_query="Investigate the failure",
        subquery_budget=2,
    )

    assert decision.decomposition_mode == "single_pass"
    assert decision.subqueries == ["Investigate the failure"]
    assert decision.batching_strategy == "serial"
    assert "Aggregate" in decision.aggregation_plan
    assert decision.decomposition_rationale
