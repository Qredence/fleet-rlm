from __future__ import annotations

from typing import Any

from fleet_rlm.runtime.agent.recursive_verification import (
    VerifyRecursiveAggregationModule,
    build_recursive_verification_inputs,
    coerce_recursive_verification_decision,
)


def test_recursive_verification_module_coerces_bounded_outputs() -> None:
    class _FakePredictor:
        def __call__(self, **_kwargs: Any) -> Any:
            return {
                "verification_status": "needs-repair",
                "missing_evidence": [
                    "Confirm the repair against the failing import path.",
                    "Confirm the repair against the failing import path.",
                    "Capture one bounded test rerun.",
                ],
                "contradictions": "One subquery says fixed, another says retry",
                "verified_summary": "x" * 1600,
                "verification_rationale": "",
            }

    module = VerifyRecursiveAggregationModule(predictor=_FakePredictor())

    prediction = module(
        user_request="Repair the failing recursive workspace step",
        assembled_recursive_context="Use only the bounded traceback summary.",
        decomposition_plan_summary="fan_out with batched aggregation in Python/runtime.",
        collected_subquery_outputs=[
            "[1] Inspect traceback\nanswer=ImportError",
            "[2] Repair import path\nanswer=Updated path",
        ],
        latest_sandbox_evidence="memory_handle=meta/workspaces/ws-1/users/u/session.json",
    )

    assert prediction.verification_status == "needs_repair"
    assert prediction.missing_evidence == [
        "Confirm the repair against the failing import path.",
        "Capture one bounded test rerun.",
    ]
    assert prediction.contradictions == [
        "One subquery says fixed",
        "another says retry",
    ]
    assert len(prediction.verified_summary) == 800
    assert prediction.verification_rationale


def test_build_recursive_verification_inputs_stays_summary_only() -> None:
    inputs = build_recursive_verification_inputs(
        user_request="Repair the workspace",
        assembled_recursive_context="Use only the staged traceback and memory handle.",
        decomposition_decision=type(
            "_Decision",
            (),
            {
                "decomposition_mode": "fan_out",
                "batching_strategy": "batched",
                "aggregation_plan": "Combine bounded findings in Python/runtime.",
                "decomposition_rationale": "Split inspection and synthesis.",
            },
        )(),
        results=[
            {
                "subquery": "Inspect traceback",
                "answer": "ImportError in one module",
                "final_reasoning": "The failure is localized to one import path.",
            },
            {
                "subquery": "Repair import path",
                "answer": "Updated the import path",
                "final_reasoning": "One bounded repair is available.",
            },
        ],
        runtime_metadata={
            "volume_name": "tenant-a",
            "workspace_path": "/workspace/repo",
            "sandbox_id": "sbx-123",
            "memory_handle": "meta/workspaces/tenant-a/users/u/react-session.json",
            "memory_blob": "SECRET" * 200,
        },
        interpreter_context_paths=["src/fleet_rlm/runtime/agent"],
    )

    assert "decomposition_mode=fan_out" in inputs.decomposition_plan_summary
    assert any(row.startswith("[1] Inspect traceback") for row in inputs.collected_subquery_outputs)
    assert "memory_handle=meta/workspaces/tenant-a/users/u/react-session.json" in (
        inputs.latest_sandbox_evidence
    )
    assert "SECRET" not in inputs.assembled_recursive_context
    assert "SECRET" not in inputs.latest_sandbox_evidence


def test_coerce_recursive_verification_decision_defaults_to_sufficient() -> None:
    decision = coerce_recursive_verification_decision(
        {
            "verification_status": "unknown",
            "missing_evidence": "",
            "contradictions": [],
            "verified_summary": "",
            "verification_rationale": "",
        },
        fallback_summary="[1] Inspect traceback\nanswer=ImportError",
    )

    assert decision.verification_status == "sufficient"
    assert decision.missing_evidence == []
    assert decision.contradictions == []
    assert "[1] Inspect traceback" in decision.verified_summary
    assert decision.verification_rationale
