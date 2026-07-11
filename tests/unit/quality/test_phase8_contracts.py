from __future__ import annotations

import pytest

from fleet_rlm.quality.checkpointing import (
    ResumeNotAllowedError,
    build_run_fingerprint,
    checkpoint_can_resume,
    require_resumable_status,
    require_resume_fingerprint,
)
from fleet_rlm.quality.contracts import (
    AutoBudget,
    MetricCallsBudget,
    ModelProfileRef,
    OptimizationRunSpec,
    OptimizationSearchConfig,
    OptimizationTargetRef,
)
from fleet_rlm.quality.evaluation_protocol import DatasetPartition, partition_rows
from fleet_rlm.quality.promotion import PromotionEvidence, PromotionGatePolicy, evaluate_promotion_gate


def _run_spec(*, metric_calls: int = 20) -> OptimizationRunSpec:
    return OptimizationRunSpec(
        target=OptimizationTargetRef(kind="module", target_id="plan-code-change", version="1"),
        dataset_version_id="dataset-v1",
        metric_profile_id="plan-code-change@1",
        task_model=ModelProfileRef(profile_id="task-profile", model_id="openai/task", wire_format="openai_responses"),
        reflection_model=ModelProfileRef(
            profile_id="reflection-profile", model_id="openai/reflection", wire_format="openai_responses"
        ),
        budget=MetricCallsBudget(value=metric_calls, wall_clock_seconds=600),
        search=OptimizationSearchConfig(),
        adapter="chat",
        dspy_version="3.3.0b1",
        gepa_version="0.1.1",
    )


def test_partition_rows_keeps_promotion_test_sealed() -> None:
    rows = [
        {"id": "train", "partition": "training"},
        {"id": "selection", "partition": "selection"},
        {"id": "test", "partition": "promotion_test"},
    ]

    partitions = partition_rows(rows, require_promotion_test=True)

    assert [row["id"] for row in partitions.training] == ["train"]
    assert [row["id"] for row in partitions.selection] == ["selection"]
    assert [row["id"] for row in partitions.promotion_test] == ["test"]
    assert partitions.partition_for("test") is DatasetPartition.PROMOTION_TEST


def test_run_fingerprint_changes_when_budget_changes() -> None:
    first = build_run_fingerprint(_run_spec(metric_calls=20))
    second = build_run_fingerprint(_run_spec(metric_calls=21))

    assert first != second
    assert checkpoint_can_resume(expected_fingerprint=first, checkpoint_fingerprint=first)
    assert not checkpoint_can_resume(expected_fingerprint=first, checkpoint_fingerprint=second)


def test_require_resume_fingerprint_is_exact_match_only() -> None:
    first = build_run_fingerprint(_run_spec(metric_calls=20))
    assert require_resume_fingerprint(stored_fingerprint=first) == first
    with pytest.raises(ResumeNotAllowedError, match="no trusted fingerprint"):
        require_resume_fingerprint(stored_fingerprint=None)
    with pytest.raises(ResumeNotAllowedError, match="does not match"):
        require_resume_fingerprint(stored_fingerprint=first, expected_fingerprint="deadbeef")


def test_require_resumable_status_rejects_running() -> None:
    assert require_resumable_status("failed") == "failed"
    with pytest.raises(ResumeNotAllowedError, match="not resumable"):
        require_resumable_status("running")


def test_budget_is_a_discriminated_union() -> None:
    exploratory = _run_spec().model_copy(update={"budget": AutoBudget(value="light", wall_clock_seconds=300)})

    assert exploratory.budget.kind == "auto"
    assert exploratory.budget.value == "light"


def test_promotion_gate_requires_test_lift_and_no_regressions() -> None:
    policy = PromotionGatePolicy(
        minimum_test_examples=2,
        maximum_cost_increase_ratio=0.20,
        maximum_p95_latency_increase_ratio=0.20,
    )
    evidence = PromotionEvidence(
        baseline_score=0.70,
        candidate_score=0.80,
        test_examples=2,
        hard_gate_failures=0,
        critical_slice_deltas={"safety": 0.0},
        baseline_failure_rate=0.05,
        candidate_failure_rate=0.05,
        baseline_cost=1.0,
        candidate_cost=1.1,
        baseline_p95_latency_ms=1000,
        candidate_p95_latency_ms=1100,
        artifact_round_trip_passed=True,
        metric_call_budget_used=True,
    )

    result = evaluate_promotion_gate(evidence, policy)

    assert result.ready is True
    assert result.failures == ()


def test_promotion_gate_rejects_selection_only_or_regressing_candidate() -> None:
    result = evaluate_promotion_gate(
        PromotionEvidence(
            baseline_score=0.70,
            candidate_score=0.69,
            test_examples=0,
            hard_gate_failures=1,
            critical_slice_deltas={"safety": -0.1},
            baseline_failure_rate=0.0,
            candidate_failure_rate=0.1,
            artifact_round_trip_passed=False,
        ),
        PromotionGatePolicy(minimum_test_examples=1),
    )

    assert result.ready is False
    assert set(result.failures) >= {
        "insufficient_promotion_test_examples",
        "aggregate_score_did_not_improve",
        "hard_gate_failure",
        "critical_slice_regression:safety",
        "task_failure_rate_regression",
        "artifact_round_trip_failed",
        "promotion_requires_max_metric_calls",
    }


def test_promotion_gate_fails_closed_when_required_evidence_is_missing() -> None:
    result = evaluate_promotion_gate(
        PromotionEvidence(
            baseline_score=0.5,
            candidate_score=0.8,
            test_examples=3,
            hard_gate_failures=None,
            critical_slice_deltas=None,
            baseline_failure_rate=None,
            candidate_failure_rate=None,
            artifact_round_trip_passed=None,
            metric_call_budget_used=True,
        ),
        PromotionGatePolicy(minimum_test_examples=1),
    )

    assert result.ready is False
    assert set(result.failures) >= {
        "hard_gate_evidence_missing",
        "critical_slice_evidence_missing",
        "task_failure_rate_evidence_missing",
        "cost_evidence_missing",
        "latency_evidence_missing",
        "artifact_round_trip_evidence_missing",
    }
