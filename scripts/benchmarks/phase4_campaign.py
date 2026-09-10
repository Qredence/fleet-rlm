"""Sealed, fail-closed accounting and analysis for the Phase 4 ablation.

This module deliberately owns no provider client.  A live operator supplies a
bounded ArmRunner; the campaign core verifies admission, records no prompt or
provider payload, and produces an immutable receipt-safe result.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from scripts.benchmarks.campaign import CampaignAdmissionError, CampaignBudget, CampaignPreflight

Arm = Literal["A", "B", "C", "D"]
Classification = Literal["suitable", "conflict", "control"]
ARMS: tuple[Arm, ...] = ("A", "B", "C", "D")
_CLASSIFICATIONS = frozenset(("suitable", "conflict", "control"))
_TOKEN_DIVISOR = Decimal(1_000_000)
_REVISION_RE = re.compile(r"^[0-9a-f]{40}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_CASE_ID_RE = re.compile(r"^p4-[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_SOURCE_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
_MAX_CASE_SOURCES = 32
_MAX_CASE_SOURCE_BYTES = 50_000
PHASE4_SCHEMA = "fleet.phase4-ablation/v1"


@dataclass(frozen=True, slots=True)
class ArmSpec:
    """Immutable arm contract; provider invocation stays behind ArmRunner."""

    arm: Arm
    execution: Literal["direct_dspy", "native_rlm", "frozen_recursive", "simplified_recursive"]
    uses_child_sandboxes: bool
    source_revision: str | None = None


def arm_specs(*, baseline_revision: str, candidate_revision: str) -> tuple[ArmSpec, ...]:
    """Construct the exact four-arm comparison without selecting a provider."""
    if not _REVISION_RE.fullmatch(baseline_revision) or not _REVISION_RE.fullmatch(candidate_revision):
        raise ValueError("Phase 4 arm revisions must be full commit identifiers")
    return (
        ArmSpec("A", "direct_dspy", False, candidate_revision),
        ArmSpec("B", "native_rlm", False, candidate_revision),
        ArmSpec("C", "frozen_recursive", True, baseline_revision),
        ArmSpec("D", "simplified_recursive", True, candidate_revision),
    )


@dataclass(frozen=True, slots=True)
class Phase4Case:
    identifier: str
    classification: Classification
    sources: Mapping[str, str]
    question: str
    expected_answer: str
    required_evidence: tuple[str, ...]
    required_uncertainty: str
    forbidden_claims: tuple[str, ...]

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> Phase4Case:
        expected_keys = {
            "id",
            "classification",
            "sources",
            "question",
            "expected_answer",
            "required_evidence",
            "required_uncertainty",
            "forbidden_claims",
        }
        if set(value) != expected_keys:
            raise ValueError("invalid Phase 4 corpus case keys")
        sources = value.get("sources")
        required = value.get("required_evidence")
        forbidden = value.get("forbidden_claims")
        classification = value.get("classification")
        if (
            not isinstance(value.get("id"), str)
            or classification not in _CLASSIFICATIONS
            or not isinstance(sources, Mapping)
            or not 0 <= len(sources) <= _MAX_CASE_SOURCES
            or not isinstance(required, list)
            or not isinstance(forbidden, list)
            or any(not isinstance(key, str) or not isinstance(item, str) for key, item in sources.items())
            or any(not isinstance(item, str) for item in (*required, *forbidden))
            or not all(
                isinstance(value.get(key), str) for key in ("question", "expected_answer", "required_uncertainty")
            )
        ):
            raise ValueError("invalid Phase 4 corpus case")
        if (
            not _CASE_ID_RE.fullmatch(value["id"])
            or not value["question"].strip()
            or not value["expected_answer"].strip()
            or len(value["question"].encode()) > 4_000
            or len(value["expected_answer"].encode()) > 4_000
            or len(value["required_uncertainty"].encode()) > 2_000
        ):
            raise ValueError("Phase 4 corpus case is incomplete")
        if classification != "control" and not sources:
            raise ValueError("non-control Phase 4 cases require source records")
        if any(not _SOURCE_ID_RE.fullmatch(key) for key in sources):
            raise ValueError("Phase 4 source identifiers are invalid")
        if len(set(required)) != len(required) or len(set(forbidden)) != len(forbidden):
            raise ValueError("Phase 4 evidence lists must be unique")
        source_bytes = sum(len(item.encode()) for item in sources.values())
        if source_bytes > _MAX_CASE_SOURCE_BYTES:
            raise ValueError("Phase 4 source material exceeds its byte bound")
        if any(item not in sources for item in required):
            raise ValueError("Phase 4 required evidence is not in the source set")
        return cls(
            value["id"],
            classification,
            dict(sources),
            value["question"],
            value["expected_answer"],
            tuple(required),
            value["required_uncertainty"],
            tuple(forbidden),
        )


@dataclass(frozen=True, slots=True)
class PublicRateCard:
    """Approved public standard prices; no account-specific data belongs here."""

    input_usd_per_million: Decimal = Decimal("0.14")
    output_usd_per_million: Decimal = Decimal("0.28")
    cache_read_usd_per_million: Decimal = Decimal("0.028")
    vcpu_usd_per_hour: Decimal = Decimal("0.0504")
    gib_ram_usd_per_hour: Decimal = Decimal("0.0162")
    gib_storage_usd_per_hour: Decimal = Decimal("0.000108")


@dataclass(frozen=True, slots=True)
class TrialEnvelope:
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    retries: int
    sandbox_seconds: int
    sandbox_count: int
    vcpus: int
    gib_ram: int
    gib_storage: int
    # Maximum wall-clock lifetime reserved for one worker, independent of the
    # shorter Daytona billing lifetime.  Admission uses this bound so a slow
    # provider attempt cannot push the campaign beyond its four-hour window.
    maximum_lifetime_seconds: int = 150

    def upper_bound_usd(self, rates: PublicRateCard) -> Decimal:
        values = asdict(self)
        if any(type(value) is not int or value < 0 for value in values.values()):
            raise ValueError("trial envelope values must be nonnegative integers")
        if self.retries < 1 or self.sandbox_count < 1 or self.sandbox_seconds < 1:
            raise ValueError("trial envelope must bound attempts and sandbox time")
        if self.maximum_lifetime_seconds < self.sandbox_seconds:
            raise ValueError("trial lifetime must cover the bounded Sandbox lifetime")
        attempts = Decimal(self.retries)
        model = attempts * (
            Decimal(self.input_tokens) * rates.input_usd_per_million / _TOKEN_DIVISOR
            + Decimal(self.output_tokens) * rates.output_usd_per_million / _TOKEN_DIVISOR
            + Decimal(self.cache_read_tokens) * rates.cache_read_usd_per_million / _TOKEN_DIVISOR
        )
        sandbox_hours = Decimal(self.sandbox_seconds * self.sandbox_count) / Decimal(3600)
        sandbox = sandbox_hours * (
            Decimal(self.vcpus) * rates.vcpu_usd_per_hour
            + Decimal(self.gib_ram) * rates.gib_ram_usd_per_hour
            + Decimal(self.gib_storage) * rates.gib_storage_usd_per_hour
        )
        return model + sandbox


@dataclass(frozen=True, slots=True)
class Trial:
    case_id: str
    classification: Classification
    repeat: int
    arm: Arm
    arm_order: tuple[Arm, ...]


@dataclass(frozen=True, slots=True)
class TrialObservation:
    answer: str
    cited_evidence: tuple[str, ...]
    uncertainty: str
    completed: bool
    authorization_confirmed: bool
    cleanup_confirmed: bool
    input_tokens: int | None
    output_tokens: int | None
    cache_read_tokens: int | None
    sandbox_seconds: int | None
    latency_ms: float | None
    root_lm_calls: int | None
    child_lm_calls: int | None
    delegated_bytes: int | None
    sandbox_count: int | None = None
    resource_shape: tuple[int, int, int] | None = None
    error_category: str | None = None

    def observed_cost(self, rates: PublicRateCard, envelope: TrialEnvelope) -> Decimal | None:
        input_tokens = self.input_tokens
        output_tokens = self.output_tokens
        cache_read_tokens = self.cache_read_tokens
        sandbox_seconds = self.sandbox_seconds
        sandbox_count = self.sandbox_count
        if any(
            type(value) is not int or value < 0
            for value in (input_tokens, output_tokens, cache_read_tokens, sandbox_seconds)
        ):
            return None
        if (
            input_tokens > envelope.input_tokens
            or output_tokens > envelope.output_tokens
            or cache_read_tokens > envelope.cache_read_tokens
        ):
            return None
        if type(sandbox_count) is not int or sandbox_count < 0:
            return None
        if sandbox_seconds == 0:
            if sandbox_count != 0 or self.resource_shape is not None:
                return None
        else:
            if sandbox_count < 1 or sandbox_count > envelope.sandbox_count:
                return None
            if sandbox_seconds > envelope.sandbox_seconds * sandbox_count:
                return None
            if (
                not isinstance(self.resource_shape, tuple)
                or len(self.resource_shape) != 3
                or any(type(value) is not int or value <= 0 for value in self.resource_shape)
            ):
                return None
        model = (
            Decimal(input_tokens) * rates.input_usd_per_million / _TOKEN_DIVISOR
            + Decimal(output_tokens) * rates.output_usd_per_million / _TOKEN_DIVISOR
            + Decimal(cache_read_tokens) * rates.cache_read_usd_per_million / _TOKEN_DIVISOR
        )
        sandbox_hours = Decimal(sandbox_seconds * sandbox_count) / Decimal(3600)
        shape = self.resource_shape
        sandbox = Decimal(0)
        if shape is not None:
            sandbox = sandbox_hours * (
                Decimal(shape[0]) * rates.vcpu_usd_per_hour
                + Decimal(shape[1]) * rates.gib_ram_usd_per_hour
                + Decimal(shape[2]) * rates.gib_storage_usd_per_hour
            )
        return model + sandbox


@dataclass(frozen=True, slots=True)
class ScoredTrial:
    trial: Trial
    verified_success: bool
    evidence_valid: bool
    observation: TrialObservation
    observed_cost_usd: Decimal | None

    def receipt(self) -> dict[str, object]:
        return {
            "case_id": self.trial.case_id,
            "classification": self.trial.classification,
            "repeat": self.trial.repeat,
            "arm": self.trial.arm,
            "arm_order": list(self.trial.arm_order),
            "verified_success": self.verified_success,
            "evidence_valid": self.evidence_valid,
            "completed": self.observation.completed,
            "authorization_confirmed": self.observation.authorization_confirmed,
            "cleanup_confirmed": self.observation.cleanup_confirmed,
            "input_tokens": self.observation.input_tokens,
            "output_tokens": self.observation.output_tokens,
            "cache_read_tokens": self.observation.cache_read_tokens,
            "sandbox_seconds": self.observation.sandbox_seconds,
            "latency_ms": self.observation.latency_ms,
            "root_lm_calls": self.observation.root_lm_calls,
            "child_lm_calls": self.observation.child_lm_calls,
            "delegated_bytes": self.observation.delegated_bytes,
            "sandbox_count": self.observation.sandbox_count,
            "resource_shape": list(self.observation.resource_shape)
            if self.observation.resource_shape is not None
            else None,
            "error_category": self.observation.error_category,
            "observed_cost_usd": str(self.observed_cost_usd) if self.observed_cost_usd is not None else None,
        }


def load_cases(path: Path) -> tuple[Phase4Case, ...]:
    raw = json.loads(path.read_text())
    if not isinstance(raw, list):
        raise ValueError("Phase 4 corpus must be an array")
    if any(not isinstance(item, Mapping) for item in raw):
        raise ValueError("Phase 4 corpus cases must be JSON objects")
    cases = tuple(Phase4Case.from_mapping(item) for item in raw)
    if len(cases) != 12 or len({case.identifier for case in cases}) != 12:
        raise ValueError("Phase 4 corpus must contain exactly 12 unique cases")
    counts = {kind: sum(case.classification == kind for case in cases) for kind in _CLASSIFICATIONS}
    if counts != {"suitable": 6, "conflict": 3, "control": 3}:
        raise ValueError("Phase 4 corpus classifications must be 6 suitable, 3 conflict, 3 control")
    return cases


def corpus_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def policy_sha256(path: Path, *, profile: str = "phase4-campaign") -> str:
    """Hash the exact non-secret policy file and selected campaign profile."""
    raw = path.read_bytes()
    marker = f"\nphase4-profile:{profile}\n".encode()
    return hashlib.sha256(raw + marker).hexdigest()


def observation_from_mapping(value: Mapping[str, Any]) -> TrialObservation:
    """Parse one worker observation without coercing unknown or missing values.

    A malformed worker response is intentionally rejected by the caller and
    converted into an unavailable observation.  Silent coercion would turn
    missing provider telemetry into a zero-cost successful trial.
    """
    required = {
        "answer",
        "cited_evidence",
        "uncertainty",
        "completed",
        "authorization_confirmed",
        "cleanup_confirmed",
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "sandbox_seconds",
        "latency_ms",
        "root_lm_calls",
        "child_lm_calls",
        "delegated_bytes",
    }
    if not isinstance(value, Mapping) or not required.issubset(value):
        raise ValueError("worker observation is missing required fields")

    def bounded_optional_int(name: str) -> int | None:
        raw = value.get(name)
        if raw is None:
            return None
        if type(raw) is not int or raw < 0:
            raise ValueError(f"worker observation field {name} is invalid")
        return raw

    def bounded_optional_float(name: str) -> float | None:
        raw = value.get(name)
        if raw is None:
            return None
        if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(float(raw)) or raw < 0:
            raise ValueError(f"worker observation field {name} is invalid")
        return float(raw)

    answer = value.get("answer")
    uncertainty = value.get("uncertainty")
    evidence = value.get("cited_evidence")
    if (
        not isinstance(answer, str)
        or len(answer.encode("utf-8")) > 50_000
        or not isinstance(uncertainty, str)
        or len(uncertainty.encode("utf-8")) > 2_000
        or not isinstance(evidence, list)
        or len(evidence) > 48
        or any(not isinstance(item, str) or not item.strip() or len(item) > 128 for item in evidence)
    ):
        raise ValueError("worker observation content is invalid")
    booleans = ("completed", "authorization_confirmed", "cleanup_confirmed")
    if any(type(value.get(name)) is not bool for name in booleans):
        raise ValueError("worker observation safety fields are invalid")
    shape = value.get("resource_shape")
    resource_shape: tuple[int, int, int] | None = None
    if shape is not None:
        if not isinstance(shape, list) or len(shape) != 3 or any(type(item) is not int or item <= 0 for item in shape):
            raise ValueError("worker resource shape is invalid")
        resource_shape = (shape[0], shape[1], shape[2])
    sandbox_count = bounded_optional_int("sandbox_count")
    if sandbox_count is not None and sandbox_count > 5:
        raise ValueError("worker sandbox count exceeds campaign bound")
    category = value.get("error_category")
    if category is not None and (not isinstance(category, str) or len(category) > 64):
        raise ValueError("worker error category is invalid")
    return TrialObservation(
        answer=answer,
        cited_evidence=tuple(evidence),
        uncertainty=uncertainty,
        completed=value["completed"],
        authorization_confirmed=value["authorization_confirmed"],
        cleanup_confirmed=value["cleanup_confirmed"],
        input_tokens=bounded_optional_int("input_tokens"),
        output_tokens=bounded_optional_int("output_tokens"),
        cache_read_tokens=bounded_optional_int("cache_read_tokens"),
        sandbox_seconds=bounded_optional_int("sandbox_seconds"),
        latency_ms=bounded_optional_float("latency_ms"),
        root_lm_calls=bounded_optional_int("root_lm_calls"),
        child_lm_calls=bounded_optional_int("child_lm_calls"),
        delegated_bytes=bounded_optional_int("delegated_bytes"),
        sandbox_count=sandbox_count,
        resource_shape=resource_shape,
        error_category=category,
    )


def balanced_schedule(cases: Sequence[Phase4Case], *, repeats: int = 3) -> tuple[Trial, ...]:
    if repeats != 3 or len(cases) != 12:
        raise ValueError("Phase 4 requires 12 cases and exactly three repeats")
    trials: list[Trial] = []
    for case_index, case in enumerate(cases):
        for repeat in range(repeats):
            offset = (case_index + repeat) % len(ARMS)
            order = ARMS[offset:] + ARMS[:offset]
            trials.extend(Trial(case.identifier, case.classification, repeat + 1, arm, order) for arm in order)
    return tuple(trials)


def score_trial(
    case: Phase4Case, trial: Trial, observation: TrialObservation, rates: PublicRateCard, envelope: TrialEnvelope
) -> ScoredTrial:
    answer = observation.answer.casefold()
    evidence = tuple(dict.fromkeys(observation.cited_evidence))
    evidence_set = set(evidence)
    evidence_valid = set(case.required_evidence).issubset(evidence_set) and evidence_set.issubset(case.sources)
    uncertainty_valid = (
        not case.required_uncertainty or case.required_uncertainty.casefold() in observation.uncertainty.casefold()
    )
    # The oracle stores conflicting conclusions, not token blacklists: do not
    # reject an expected phrase merely because it contains a shorter forbidden
    # phrase (for example, "not permitted" versus "permitted").
    forbidden = any(
        item.casefold() not in case.expected_answer.casefold() and item.casefold() in answer
        for item in case.forbidden_claims
    )
    verified = (
        observation.completed
        and evidence_valid
        and uncertainty_valid
        and case.expected_answer.casefold() in answer
        and not forbidden
    )
    return ScoredTrial(trial, verified, evidence_valid, observation, observation.observed_cost(rates, envelope))


def paired_bootstrap(rows: Sequence[ScoredTrial], *, seed: int = 7, samples: int = 10_000) -> dict[str, float | None]:
    """Cluster resampling by task, retaining each task's three repeats together."""
    d_rows = [row for row in rows if row.trial.arm == "D" and row.trial.classification == "suitable"]
    non_recursive = [row for row in rows if row.trial.arm in {"A", "B"} and row.trial.classification == "suitable"]
    by_case_d: dict[str, list[ScoredTrial]] = {}
    for row in d_rows:
        by_case_d.setdefault(row.trial.case_id, []).append(row)
    by_case_ab: dict[str, list[ScoredTrial]] = {}
    for row in non_recursive:
        by_case_ab.setdefault(row.trial.case_id, []).append(row)
    case_ids = sorted(set(by_case_d) & set(by_case_ab))
    if len(case_ids) != 6 or any(
        len(by_case_d[case]) != 3
        or len(by_case_ab.get(case, ())) != 6
        or any(len([row for row in by_case_ab[case] if row.trial.arm == arm]) != 3 for arm in ("A", "B"))
        for case in case_ids
    ):
        return {"point_estimate": None, "ci_lower": None, "ci_upper": None}

    def success(values: Sequence[ScoredTrial]) -> float:
        return sum(value.verified_success for value in values) / len(values)

    grouped_d = {case: by_case_d[case] for case in case_ids}
    grouped_ab = {
        (case, arm): [row for row in by_case_ab[case] if row.trial.arm == arm]
        for case in case_ids
        for arm in ("A", "B")
    }

    def delta(sampled: Sequence[str]) -> float:
        d = [row for case in sampled for row in grouped_d[case]]
        a = [row for case in sampled for row in grouped_ab[(case, "A")]]
        b = [row for case in sampled for row in grouped_ab[(case, "B")]]
        return success(d) - max(success(a), success(b))

    point = delta(case_ids)
    rng = random.Random(seed)
    values = sorted(delta([rng.choice(case_ids) for _ in case_ids]) for _ in range(samples))
    return {
        "point_estimate": point,
        "ci_lower": values[int(0.025 * samples)],
        "ci_upper": values[int(0.975 * samples) - 1],
    }


def phase4_decision(rows: Sequence[ScoredTrial], *, bootstrap: Mapping[str, float | None]) -> str:
    if len(rows) != 144 or any(row.observed_cost_usd is None for row in rows):
        return "incomplete"
    if {row.trial.arm for row in rows} != set(ARMS) or {row.trial.classification for row in rows} != _CLASSIFICATIONS:
        return "incomplete"
    if any(sum(row.trial.arm == arm for row in rows) != 36 for arm in ARMS):
        return "incomplete"
    expected_counts = {"suitable": 18, "conflict": 9, "control": 9}
    if any(
        sum(row.trial.arm == arm and row.trial.classification == classification for row in rows) != count
        for arm in ARMS
        for classification, count in expected_counts.items()
    ):
        return "incomplete"
    if any(not row.observation.authorization_confirmed or not row.observation.cleanup_confirmed for row in rows):
        return "safety_failure"
    if any(
        row.observation.latency_ms is None
        or row.observation.root_lm_calls is None
        or row.observation.child_lm_calls is None
        or row.observation.delegated_bytes is None
        for row in rows
    ):
        return "incomplete"
    if any(
        row.observation.error_category in {"authorization", "unauthorized", "cleanup", "cleanup_failed", "deadline"}
        for row in rows
    ):
        return "safety_failure"
    controls = [row for row in rows if row.trial.classification == "control"]

    def success_rate(values: Sequence[ScoredTrial]) -> float:
        return sum(row.verified_success for row in values) / len(values)

    d_controls = [row for row in controls if row.trial.arm == "D"]
    baseline_controls = [[row for row in controls if row.trial.arm == arm] for arm in ("A", "B")]
    if not d_controls or any(not values for values in baseline_controls):
        return "incomplete"
    if success_rate(d_controls) < max(success_rate(values) for values in baseline_controls):
        return "disable"
    point = bootstrap.get("point_estimate")
    lower = bootstrap.get("ci_lower")
    if not isinstance(point, float) or not isinstance(lower, float) or point < 0.10 or lower <= 0:
        return "disable"
    suitable = [row for row in rows if row.trial.classification == "suitable"]
    d = [row for row in suitable if row.trial.arm == "D"]
    alternatives = [[row for row in suitable if row.trial.arm == arm] for arm in ("A", "B")]
    if not d or any(not values for values in alternatives):
        return "incomplete"
    best = max(alternatives, key=success_rate)

    def cost_per_success(values: Sequence[ScoredTrial]) -> Decimal | None:
        successes = sum(row.verified_success for row in values)
        return sum((row.observed_cost_usd for row in values), Decimal(0)) / successes if successes else None

    def percentile95(values: Sequence[ScoredTrial]) -> float:
        ordered = sorted(float(row.observation.latency_ms) for row in values)
        return ordered[math.ceil(len(ordered) * 0.95) - 1]

    best_cost, d_cost = cost_per_success(best), cost_per_success(d)
    if best_cost is None or d_cost is None or d_cost > best_cost * 2 or percentile95(d) > percentile95(best) * 2:
        return "disable"
    return "retain_simplified_profile"


def _percentile95(values: Sequence[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[math.ceil(len(ordered) * 0.95) - 1]


def campaign_summary(rows: Sequence[ScoredTrial]) -> dict[str, object]:
    """Aggregate receipt-safe counts and measurements by arm/classification."""
    summary: dict[str, object] = {}
    for arm in ARMS:
        for classification in sorted(_CLASSIFICATIONS):
            selected = [row for row in rows if row.trial.arm == arm and row.trial.classification == classification]
            costs = [row.observed_cost_usd for row in selected if row.observed_cost_usd is not None]
            successes = sum(row.verified_success for row in selected)
            latencies = [
                float(row.observation.latency_ms) for row in selected if row.observation.latency_ms is not None
            ]
            inputs = [row.observation.input_tokens for row in selected]
            outputs = [row.observation.output_tokens for row in selected]
            caches = [row.observation.cache_read_tokens for row in selected]
            delegated = [row.observation.delegated_bytes for row in selected]
            sandboxes = [row.observation.sandbox_seconds for row in selected]
            summary[f"{arm}:{classification}"] = {
                "attempted": len(selected),
                "completed": sum(row.observation.completed for row in selected),
                "failed": sum(not row.observation.completed for row in selected),
                "verified_successes": successes,
                "evidence_valid": sum(row.evidence_valid for row in selected),
                "authorization_confirmed": sum(row.observation.authorization_confirmed for row in selected),
                "cleanup_confirmed": sum(row.observation.cleanup_confirmed for row in selected),
                "failure_categories": dict(
                    sorted(
                        (category, sum(row.observation.error_category == category for row in selected))
                        for category in sorted(
                            {row.observation.error_category for row in selected if row.observation.error_category}
                        )
                    )
                ),
                "input_tokens": sum(value for value in inputs if isinstance(value, int))
                if all(isinstance(value, int) for value in inputs)
                else None,
                "output_tokens": sum(value for value in outputs if isinstance(value, int))
                if all(isinstance(value, int) for value in outputs)
                else None,
                "cache_read_tokens": sum(value for value in caches if isinstance(value, int))
                if all(isinstance(value, int) for value in caches)
                else None,
                "delegated_bytes": sum(value for value in delegated if isinstance(value, int))
                if all(isinstance(value, int) for value in delegated)
                else None,
                "sandbox_seconds": sum(value for value in sandboxes if isinstance(value, int))
                if all(isinstance(value, int) for value in sandboxes)
                else None,
                "root_lm_calls": sum(row.observation.root_lm_calls or 0 for row in selected)
                if all(row.observation.root_lm_calls is not None for row in selected)
                else None,
                "child_lm_calls": sum(row.observation.child_lm_calls or 0 for row in selected)
                if all(row.observation.child_lm_calls is not None for row in selected)
                else None,
                "observed_spend_usd": str(sum(costs, Decimal(0))) if len(costs) == len(selected) else None,
                "cost_per_verified_success_usd": str(sum(costs, Decimal(0)) / successes)
                if len(costs) == len(selected) and successes
                else None,
                "p95_latency_ms": _percentile95(latencies) if len(latencies) == len(selected) else None,
            }
    return summary


def receipt(
    rows: Sequence[ScoredTrial],
    *,
    corpus_digest: str,
    policy_digest: str,
    baseline_revision: str,
    candidate_revision: str,
    bootstrap: Mapping[str, float | None],
    cases: Sequence[Phase4Case] | None = None,
    campaign: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Create a content-safe immutable receipt; answers and sources stay local."""
    if (
        not _DIGEST_RE.fullmatch(corpus_digest)
        or not _DIGEST_RE.fullmatch(policy_digest)
        or not _REVISION_RE.fullmatch(baseline_revision)
        or not _REVISION_RE.fullmatch(candidate_revision)
    ):
        raise ValueError("receipt digests and revisions must be full SHA-256/SHA-1 values")
    costs = [row.observed_cost_usd for row in rows]
    all_costs_observed = len(costs) == len(rows) and all(cost is not None for cost in costs)
    return {
        "schema": PHASE4_SCHEMA,
        "corpus_sha256": corpus_digest,
        "policy_sha256": policy_digest,
        "baseline_revision": baseline_revision,
        "candidate_revision": candidate_revision,
        "attempted": len(rows),
        "completed": sum(row.observation.completed for row in rows),
        "failed": sum(not row.observation.completed for row in rows),
        "observed_spend_usd": str(sum((cost for cost in costs if cost is not None), Decimal(0)))
        if all_costs_observed
        else None,
        "safety": {
            "all_authorization_confirmed": all(row.observation.authorization_confirmed for row in rows),
            "all_cleanup_confirmed": all(row.observation.cleanup_confirmed for row in rows),
            "authorization_failures": sum(not row.observation.authorization_confirmed for row in rows),
            "cleanup_failures": sum(not row.observation.cleanup_confirmed for row in rows),
            "cost_observation_complete": all_costs_observed,
        },
        "campaign": dict(campaign or {}),
        "oracle_results": {
            case.identifier: {
                "classification": case.classification,
                "required_evidence": list(case.required_evidence),
                "required_uncertainty": bool(case.required_uncertainty),
                "forbidden_claim_count": len(case.forbidden_claims),
                "source_ids": sorted(case.sources),
                "expected_answer_sha256": hashlib.sha256(case.expected_answer.encode("utf-8")).hexdigest(),
                "required_uncertainty_sha256": hashlib.sha256(case.required_uncertainty.encode("utf-8")).hexdigest(),
                "forbidden_claims_sha256": hashlib.sha256(
                    json.dumps(case.forbidden_claims, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                ).hexdigest(),
            }
            for case in (cases or ())
        },
        "rows": [row.receipt() for row in rows],
        "summary": campaign_summary(rows),
        "bootstrap": dict(bootstrap),
        "decision": phase4_decision(rows, bootstrap=bootstrap),
    }


def execute_campaign(
    *,
    cases: Sequence[Phase4Case],
    preflight: CampaignPreflight,
    envelope: TrialEnvelope,
    runner: Callable[[Trial, Phase4Case], TrialObservation],
    rates: PublicRateCard | None = None,
    started_at: float | None = None,
    clock: Callable[[], float] | None = None,
    initial_spent_usd: float = 0.0,
) -> tuple[ScoredTrial, ...]:
    """Run a serial campaign through one pre-admission/settlement owner."""
    simulated = started_at is not None
    started = time.monotonic() if started_at is None else started_at
    clock = clock or time.monotonic
    rates = rates or PublicRateCard()
    budget = CampaignBudget(
        preflight,
        started_at=started,
        cleanup_reserve_seconds=900,
        initial_spent_usd=initial_spent_usd,
    )
    by_id = {case.identifier: case for case in cases}
    output: list[ScoredTrial] = []
    upper = float(envelope.upper_bound_usd(rates))
    now = started
    for trial in balanced_schedule(cases):
        try:
            budget.reserve(upper_bound_usd=upper, now=now, max_trial_seconds=envelope.maximum_lifetime_seconds)
        except CampaignAdmissionError:
            break
        try:
            observation = runner(trial, by_id[trial.case_id])
        except Exception:
            # A worker/process failure is still one attempted trial. Missing
            # telemetry keeps the reservation owned and therefore halts the
            # campaign in ``settle``; it is never retried or replaced.
            observation = TrialObservation(
                answer="",
                cited_evidence=(),
                uncertainty="",
                completed=False,
                authorization_confirmed=False,
                cleanup_confirmed=False,
                input_tokens=None,
                output_tokens=None,
                cache_read_tokens=None,
                sandbox_seconds=None,
                latency_ms=None,
                root_lm_calls=None,
                child_lm_calls=None,
                delegated_bytes=None,
                sandbox_count=None,
                resource_shape=None,
                error_category="runner_failed",
            )
        scored = score_trial(by_id[trial.case_id], trial, observation, rates, envelope)
        metrics_complete = all(
            value is not None
            for value in (
                observation.input_tokens,
                observation.output_tokens,
                observation.cache_read_tokens,
                observation.sandbox_seconds,
                observation.sandbox_count,
                observation.latency_ms,
                observation.root_lm_calls,
                observation.child_lm_calls,
                observation.delegated_bytes,
            )
        )
        resource_shape_complete = (
            observation.sandbox_seconds == 0 and observation.sandbox_count == 0 and observation.resource_shape is None
        ) or (
            isinstance(observation.sandbox_seconds, int)
            and observation.sandbox_seconds > 0
            and isinstance(observation.sandbox_count, int)
            and observation.sandbox_count > 0
            and observation.resource_shape is not None
        )
        try:
            budget.settle(
                actual_usd=(
                    float(scored.observed_cost_usd)
                    if scored.observed_cost_usd is not None and metrics_complete and resource_shape_complete
                    else None
                ),
                cleanup_confirmed=observation.cleanup_confirmed and metrics_complete and resource_shape_complete,
            )
        except CampaignAdmissionError:
            output.append(scored)
            break
        output.append(scored)
        now = (
            now
            + (observation.sandbox_seconds if type(observation.sandbox_seconds) is int else envelope.sandbox_seconds)
            if simulated
            else clock()
        )
    return tuple(output)


__all__ = [
    "ARMS",
    "ArmSpec",
    "Phase4Case",
    "PublicRateCard",
    "ScoredTrial",
    "Trial",
    "TrialEnvelope",
    "TrialObservation",
    "arm_specs",
    "balanced_schedule",
    "campaign_summary",
    "corpus_sha256",
    "execute_campaign",
    "load_cases",
    "observation_from_mapping",
    "paired_bootstrap",
    "phase4_decision",
    "policy_sha256",
    "receipt",
    "score_trial",
]
