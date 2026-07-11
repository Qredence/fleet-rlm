"""Promotion readiness policy for sealed GEPA test evidence."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class PromotionGatePolicy:
    minimum_test_examples: int = 1
    minimum_score_delta: float = 0.0
    maximum_cost_increase_ratio: float = 0.20
    maximum_p95_latency_increase_ratio: float = 0.20


@dataclass(frozen=True)
class PromotionEvidence:
    baseline_score: float | None
    candidate_score: float | None
    test_examples: int
    hard_gate_failures: int | None
    critical_slice_deltas: dict[str, float] | None = field(default_factory=dict)
    baseline_failure_rate: float | None = None
    candidate_failure_rate: float | None = None
    baseline_cost: float | None = None
    candidate_cost: float | None = None
    baseline_p95_latency_ms: float | None = None
    candidate_p95_latency_ms: float | None = None
    artifact_round_trip_passed: bool | None = None
    metric_call_budget_used: bool = False


@dataclass(frozen=True)
class PromotionGateResult:
    ready: bool
    failures: tuple[str, ...]


def _ratio_regressed(baseline: float | None, candidate: float | None, maximum_increase: float) -> bool:
    if baseline is None or candidate is None:
        return False
    if baseline <= 0:
        return candidate > baseline
    return candidate > baseline * (1 + maximum_increase)


def evaluate_promotion_gate(
    evidence: PromotionEvidence,
    policy: PromotionGatePolicy,
) -> PromotionGateResult:
    failures: list[str] = []
    if evidence.test_examples < policy.minimum_test_examples:
        failures.append("insufficient_promotion_test_examples")
    if (
        evidence.baseline_score is None
        or evidence.candidate_score is None
        or evidence.candidate_score - evidence.baseline_score <= policy.minimum_score_delta
    ):
        failures.append("aggregate_score_did_not_improve")
    if evidence.hard_gate_failures is None:
        failures.append("hard_gate_evidence_missing")
    elif evidence.hard_gate_failures:
        failures.append("hard_gate_failure")
    if evidence.critical_slice_deltas is None:
        failures.append("critical_slice_evidence_missing")
    else:
        failures.extend(
            f"critical_slice_regression:{name}"
            for name, delta in sorted(evidence.critical_slice_deltas.items())
            if delta < 0
        )
    if evidence.baseline_failure_rate is None or evidence.candidate_failure_rate is None:
        failures.append("task_failure_rate_evidence_missing")
    elif evidence.candidate_failure_rate > evidence.baseline_failure_rate:
        failures.append("task_failure_rate_regression")
    if evidence.baseline_cost is None or evidence.candidate_cost is None:
        failures.append("cost_evidence_missing")
    elif _ratio_regressed(evidence.baseline_cost, evidence.candidate_cost, policy.maximum_cost_increase_ratio):
        failures.append("cost_regression")
    if evidence.baseline_p95_latency_ms is None or evidence.candidate_p95_latency_ms is None:
        failures.append("latency_evidence_missing")
    elif _ratio_regressed(
        evidence.baseline_p95_latency_ms,
        evidence.candidate_p95_latency_ms,
        policy.maximum_p95_latency_increase_ratio,
    ):
        failures.append("latency_regression")
    if evidence.artifact_round_trip_passed is None:
        failures.append("artifact_round_trip_evidence_missing")
    elif not evidence.artifact_round_trip_passed:
        failures.append("artifact_round_trip_failed")
    if not evidence.metric_call_budget_used:
        failures.append("promotion_requires_max_metric_calls")
    return PromotionGateResult(ready=not failures, failures=tuple(failures))


__all__ = [
    "PromotionEvidence",
    "PromotionGatePolicy",
    "PromotionGateResult",
    "evaluate_promotion_gate",
]
