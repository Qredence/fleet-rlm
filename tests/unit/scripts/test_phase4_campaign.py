from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from scripts.benchmarks.campaign import CampaignPreflight
from scripts.benchmarks.phase4_campaign import (
    PublicRateCard,
    TrialEnvelope,
    TrialObservation,
    arm_specs,
    balanced_schedule,
    campaign_summary,
    execute_campaign,
    load_cases,
    observation_from_mapping,
    paired_bootstrap,
    phase4_decision,
    receipt,
    score_trial,
)

_CASES = Path(__file__).resolve().parents[3] / "scripts" / "benchmarks" / "phase4_cases.json"


def _envelope() -> TrialEnvelope:
    return TrialEnvelope(1000, 500, 0, 1, 60, 1, 1, 2, 1)


def _observation(case, *, completed: bool = True, cleanup: bool = True) -> TrialObservation:
    return TrialObservation(
        answer=case.expected_answer,
        cited_evidence=case.required_evidence,
        uncertainty=case.required_uncertainty,
        completed=completed,
        authorization_confirmed=True,
        cleanup_confirmed=cleanup,
        input_tokens=100,
        output_tokens=50,
        cache_read_tokens=0,
        sandbox_seconds=1,
        latency_ms=10.0,
        root_lm_calls=1,
        child_lm_calls=0,
        delegated_bytes=0,
        sandbox_count=1,
        resource_shape=(1, 2, 1),
    )


def test_phase4_fixture_is_sealed_and_schedule_is_exactly_balanced() -> None:
    cases = load_cases(_CASES)
    schedule = balanced_schedule(cases)
    assert len(schedule) == 144
    assert {case.classification for case in cases} == {"suitable", "conflict", "control"}
    assert {arm: sum(trial.arm == arm for trial in schedule) for arm in ("A", "B", "C", "D")} == {
        "A": 36,
        "B": 36,
        "C": 36,
        "D": 36,
    }
    assert all(tuple(sorted(trial.arm_order)) == ("A", "B", "C", "D") for trial in schedule)


def test_four_arm_contract_keeps_c_on_the_frozen_revision_only() -> None:
    baseline = "9b526f50f0aeec37ca399bc8ef19ec8a95d3bead"
    candidate = "a" * 40
    specs = arm_specs(baseline_revision=baseline, candidate_revision=candidate)
    assert [(spec.arm, spec.execution, spec.uses_child_sandboxes, spec.source_revision) for spec in specs] == [
        ("A", "direct_dspy", False, candidate),
        ("B", "native_rlm", False, candidate),
        ("C", "frozen_recursive", True, baseline),
        ("D", "simplified_recursive", True, candidate),
    ]


def test_trial_upper_bound_uses_approved_standard_rate_card() -> None:
    assert _envelope().upper_bound_usd(PublicRateCard()) == Decimal("0.001661800000000000000000000000")
    assert _envelope().maximum_lifetime_seconds == 150
    with pytest.raises(ValueError):
        TrialEnvelope(1, 1, 0, 0, 1, 1, 1, 1, 1).upper_bound_usd(PublicRateCard())
    with pytest.raises(ValueError):
        TrialEnvelope(1, 1, 0, 1, 60, 1, 1, 1, 1, 30).upper_bound_usd(PublicRateCard())


def test_observation_parser_and_resource_shape_cost_are_fail_closed() -> None:
    valid = {
        "answer": "ok",
        "cited_evidence": [],
        "uncertainty": "",
        "completed": True,
        "authorization_confirmed": True,
        "cleanup_confirmed": True,
        "input_tokens": 10,
        "output_tokens": 5,
        "cache_read_tokens": 0,
        "sandbox_seconds": 60,
        "latency_ms": 1.0,
        "root_lm_calls": 1,
        "child_lm_calls": 0,
        "delegated_bytes": 0,
        "sandbox_count": 1,
        "resource_shape": [2, 4, 8],
    }
    observation = observation_from_mapping(valid)
    assert observation.resource_shape == (2, 4, 8)
    assert observation.observed_cost(PublicRateCard(), _envelope()) < Decimal("0.003")
    assert (
        observation_from_mapping({**valid, "input_tokens": None}).observed_cost(PublicRateCard(), _envelope()) is None
    )
    for field, value in (("sandbox_count", 6), ("resource_shape", [0, 4, 8])):
        invalid = {**valid, field: value}
        with pytest.raises(ValueError):
            observation_from_mapping(invalid)


def test_summary_keeps_unknown_observations_unknown() -> None:
    case = load_cases(_CASES)[0]
    trial = balanced_schedule((case,) * 12)[0]
    row = score_trial(
        case,
        trial,
        TrialObservation(
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
        ),
        PublicRateCard(),
        _envelope(),
    )
    summary = campaign_summary((row,))
    assert summary["A:suitable"]["observed_spend_usd"] is None


def test_scoring_requires_exact_oracle_evidence_uncertainty_and_no_forbidden_claim() -> None:
    case = load_cases(_CASES)[0]
    trial = balanced_schedule((case,) * 12)[0]
    result = score_trial(case, trial, _observation(case), PublicRateCard(), _envelope())
    assert result.verified_success and result.evidence_valid
    forbidden = score_trial(
        case,
        trial,
        replace(_observation(case), answer="185 and 1085"),
        PublicRateCard(),
        _envelope(),
    )
    assert not forbidden.verified_success


def test_campaign_halts_on_unknown_cost_or_missing_cleanup() -> None:
    cases = load_cases(_CASES)
    policy = CampaignPreflight("p4", "daytona", 14_400, 144, 5, 50.0)

    def runner(trial, case):
        observation = _observation(case)
        if trial.arm == "B":
            return replace(observation, input_tokens=None)
        return observation

    rows = execute_campaign(cases=cases, preflight=policy, envelope=_envelope(), runner=runner, started_at=0)
    assert len(rows) == 2
    assert rows[-1].observed_cost_usd is None


def test_campaign_halts_when_non_cost_observation_is_unknown() -> None:
    cases = load_cases(_CASES)
    policy = CampaignPreflight("p4", "daytona", 14_400, 144, 5, 50.0)

    def runner(trial, case):
        observation = _observation(case)
        if trial.arm == "B":
            return replace(observation, delegated_bytes=None)
        return observation

    rows = execute_campaign(cases=cases, preflight=policy, envelope=_envelope(), runner=runner, started_at=0)
    assert len(rows) == 2
    assert rows[-1].observation.delegated_bytes is None


def test_campaign_budget_includes_prior_observed_spend() -> None:
    cases = load_cases(_CASES)
    policy = CampaignPreflight("p4", "daytona", 14_400, 144, 5, 0.003)
    rows = execute_campaign(
        cases=cases,
        preflight=policy,
        envelope=_envelope(),
        runner=lambda _trial, case: _observation(case),
        started_at=0,
        initial_spent_usd=0.001,
    )

    assert rows
    assert rows[0].observed_cost_usd is not None
    assert 0 < len(rows) < 144


def test_bootstrap_and_decision_require_complete_nonregressing_evidence() -> None:
    cases = load_cases(_CASES)
    rows = []
    for trial in balanced_schedule(cases):
        case = next(item for item in cases if item.identifier == trial.case_id)
        succeeded = (
            trial.arm == "D"
            or case.classification != "suitable"
            or (trial.arm == "B" and case.identifier in {"p4-suitable-01", "p4-suitable-02", "p4-suitable-03"})
        )
        rows.append(score_trial(case, trial, _observation(case, completed=succeeded), PublicRateCard(), _envelope()))
    bootstrap = paired_bootstrap(rows, samples=200)
    assert bootstrap["point_estimate"] == 0.5
    assert phase4_decision(rows, bootstrap=bootstrap) == "retain_simplified_profile"
    payload = receipt(
        rows,
        corpus_digest="c" * 64,
        policy_digest="b" * 64,
        baseline_revision="9b526f50f0aeec37ca399bc8ef19ec8a95d3bead",
        candidate_revision="a" * 40,
        bootstrap=bootstrap,
        cases=cases,
    )
    assert payload["schema"] == "fleet.phase4-ablation/v1"
    assert "185 and 1085" not in str(payload)
    assert "selected_fragments" not in str(payload)
    oracle = payload["oracle_results"]["p4-suitable-01"]
    assert oracle["expected_answer_sha256"]
    assert oracle["required_uncertainty_sha256"]
    assert oracle["forbidden_claims_sha256"]
