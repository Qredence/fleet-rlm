from __future__ import annotations

import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from scripts.benchmarks import run_phase4_campaign
from scripts.benchmarks.campaign import CampaignPreflight
from scripts.benchmarks.phase4_campaign import (
    PublicRateCard,
    TrialEnvelope,
    TrialObservation,
    arm_specs,
    balanced_schedule,
    campaign_summary,
    execute_campaign,
    execute_partial_campaign,
    load_cases,
    load_continuation_rows,
    observation_from_mapping,
    paired_bootstrap,
    partial_schedule,
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
        trace_id="tr-test-001",
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


def test_partial_schedule_is_fixed_to_the_first_case_and_arm_quota() -> None:
    cases = load_cases(_CASES)

    schedule = partial_schedule(cases)

    assert [(trial.case_id, trial.repeat, trial.arm, "".join(trial.arm_order)) for trial in schedule] == [
        ("p4-suitable-01", 1, "A", "ABCD"),
        ("p4-suitable-01", 1, "B", "ABCD"),
        ("p4-suitable-01", 1, "C", "ABCD"),
        ("p4-suitable-01", 1, "D", "ABCD"),
        ("p4-suitable-01", 2, "B", "BCDA"),
        ("p4-suitable-01", 2, "C", "BCDA"),
        ("p4-suitable-01", 2, "D", "BCDA"),
        ("p4-suitable-01", 2, "A", "BCDA"),
        ("p4-suitable-01", 3, "A", "CDAB"),
        ("p4-suitable-01", 3, "B", "CDAB"),
    ]


def test_partial_campaign_continues_ordinary_failures_but_halts_safety_faults() -> None:
    cases = load_cases(_CASES)
    schedule = partial_schedule(cases)
    calls = 0

    def ordinary_failure(_trial, _case):
        nonlocal calls
        calls += 1
        if calls == 1:
            return replace(_observation(cases[0]), completed=False, error_category="provider_error")
        return _observation(cases[0])

    rows = execute_partial_campaign(
        cases=cases,
        trials=schedule[:3],
        envelope=_envelope(),
        runner=ordinary_failure,
        started_at=0,
        clock=lambda: 0,
    )
    assert len(rows) == 3

    calls = 0

    def safety_failure(_trial, _case):
        nonlocal calls
        calls += 1
        return replace(_observation(cases[0]), cleanup_confirmed=False, error_category="cleanup_failed")

    rows = execute_partial_campaign(
        cases=cases,
        trials=schedule[:3],
        envelope=_envelope(),
        runner=safety_failure,
        started_at=0,
        clock=lambda: 0,
    )
    assert len(rows) == 1

    def baseline_telemetry_failure(trial, case):
        observation = _observation(case)
        if trial.arm == "C":
            return replace(observation, cleanup_confirmed=False, error_category="cleanup_unavailable")
        return observation

    rows = execute_partial_campaign(
        cases=cases,
        trials=schedule[:4],
        envelope=_envelope(),
        runner=baseline_telemetry_failure,
        started_at=0,
        clock=lambda: 0,
    )
    assert len(rows) == 3

    def cleanup_flag_failure(_trial, _case):
        # The error category preserves the turn diagnosis; the cleanup
        # flag alone must still halt the exploratory sample.
        return replace(_observation(cases[0]), cleanup_confirmed=False, error_category="turn_failed")

    rows = execute_partial_campaign(
        cases=cases,
        trials=schedule[:3],
        envelope=_envelope(),
        runner=cleanup_flag_failure,
        started_at=0,
        clock=lambda: 0,
    )
    assert len(rows) == 1


def test_partial_campaign_reserves_cleanup_window_before_admission_deadline() -> None:
    cases = load_cases(_CASES)
    schedule = partial_schedule(cases)
    ticks = iter((0.0, 0.0, 61.0))

    rows = execute_partial_campaign(
        cases=cases,
        trials=schedule[:2],
        envelope=_envelope(),
        runner=lambda _trial, case: _observation(case),
        started_at=0.0,
        clock=lambda: next(ticks),
        max_elapsed_seconds=100,
        cleanup_reserve_seconds=40,
    )

    assert len(rows) == 1


def test_four_arm_contract_keeps_c_on_the_frozen_revision_only() -> None:
    baseline = "9b526f50f0aeec37ca399bc8ef19ec8a95d3bead"
    candidate = "a" * 40
    specs = arm_specs(baseline_revision=baseline, candidate_revision=candidate)
    assert [(spec.arm, spec.execution, spec.uses_child_sandboxes, spec.source_revision) for spec in specs] == [
        ("A", "api_direct", False, candidate),
        ("B", "api_native_rlm", False, candidate),
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


def test_campaign_charges_unknown_cost_and_continues_with_confirmed_cleanup() -> None:
    cases = load_cases(_CASES)
    policy = CampaignPreflight("p4", "daytona", 14_400, 144, 5, 50.0)

    def runner(trial, case):
        observation = _observation(case)
        if trial.arm == "B":
            return replace(observation, completed=False, input_tokens=None, output_tokens=None)
        return observation

    outcome = execute_campaign(cases=cases, preflight=policy, envelope=_envelope(), runner=runner, started_at=0)
    assert len(outcome.rows) == 144
    unknown = [row for row in outcome.rows if row.observed_cost_usd is None]
    assert len(unknown) == 36
    assert all(row.observation.cleanup_confirmed for row in unknown)
    assert all(row.reserved_cost_usd == _envelope().upper_bound_usd(PublicRateCard()) for row in unknown)
    assert outcome.budget["halted"] is False
    assert outcome.budget["halt_reason"] is None


def test_campaign_halts_on_unconfirmed_cleanup() -> None:
    cases = load_cases(_CASES)
    policy = CampaignPreflight("p4", "daytona", 14_400, 144, 5, 50.0)

    def runner(trial, case):
        observation = _observation(case)
        if trial.arm == "B":
            return replace(observation, cleanup_confirmed=False)
        return observation

    outcome = execute_campaign(cases=cases, preflight=policy, envelope=_envelope(), runner=runner, started_at=0)
    assert len(outcome.rows) == 2
    assert outcome.rows[-1].observation.cleanup_confirmed is False
    assert outcome.budget["halted"] is True
    assert outcome.budget["halt_reason"] == "unconfirmed_cleanup"


def test_campaign_halts_when_telemetry_exceeds_the_envelope() -> None:
    cases = load_cases(_CASES)
    policy = CampaignPreflight("p4", "daytona", 14_400, 144, 5, 50.0)

    def runner(trial, case):
        observation = _observation(case)
        if trial.arm == "B":
            return replace(observation, sandbox_seconds=3_600)
        return observation

    outcome = execute_campaign(cases=cases, preflight=policy, envelope=_envelope(), runner=runner, started_at=0)
    assert len(outcome.rows) == 2
    assert outcome.rows[-1].observed_cost_usd is None
    assert outcome.budget["halted"] is True
    assert outcome.budget["halt_reason"] == "cost_bound_breach"


def test_campaign_ignores_non_cost_telemetry_gaps_in_settlement() -> None:
    cases = load_cases(_CASES)
    policy = CampaignPreflight("p4", "daytona", 14_400, 144, 5, 50.0)

    def runner(trial, case):
        observation = _observation(case)
        if trial.arm == "B":
            return replace(observation, delegated_bytes=None)
        return observation

    outcome = execute_campaign(cases=cases, preflight=policy, envelope=_envelope(), runner=runner, started_at=0)
    assert len(outcome.rows) == 144
    assert all(row.observed_cost_usd is not None for row in outcome.rows)
    assert outcome.budget["halted"] is False


def test_campaign_budget_includes_prior_observed_spend() -> None:
    cases = load_cases(_CASES)
    policy = CampaignPreflight("p4", "daytona", 14_400, 144, 5, 0.003)
    outcome = execute_campaign(
        cases=cases,
        preflight=policy,
        envelope=_envelope(),
        runner=lambda _trial, case: _observation(case),
        started_at=0,
        initial_spent_usd=0.001,
    )
    rows = outcome.rows

    assert rows
    assert rows[0].observed_cost_usd is not None
    assert 0 < len(rows) < 144


def test_decision_requires_trace_linkage_for_completed_rows() -> None:
    cases = load_cases(_CASES)
    rows = []
    for trial in balanced_schedule(cases):
        case = next(item for item in cases if item.identifier == trial.case_id)
        rows.append(score_trial(case, trial, _observation(case), PublicRateCard(), _envelope()))
    bootstrap = paired_bootstrap(rows, samples=200)
    assert phase4_decision(rows, bootstrap=bootstrap) == "disable"
    stripped = [
        replace(row, observation=replace(row.observation, trace_id=None)) if index == 0 else row
        for index, row in enumerate(rows)
    ]
    assert phase4_decision(stripped, bootstrap=bootstrap) == "incomplete"


def test_observation_parser_accepts_bounded_trace_linkage() -> None:
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
        "trace_id": "tr-abc-123",
    }
    assert observation_from_mapping(valid).trace_id == "tr-abc-123"
    assert observation_from_mapping({k: v for k, v in valid.items() if k != "trace_id"}).trace_id is None
    for trace_id in ("", "  ", "x" * 257, 123):
        with pytest.raises(ValueError):
            observation_from_mapping({**valid, "trace_id": trace_id})


def test_mlflow_preflight_requires_reachable_tracking_server(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace
    from urllib.error import URLError

    class _Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    contract = SimpleNamespace(mlflow_tracing_enabled=True, mlflow_tracking_uri="http://127.0.0.1:5001")
    monkeypatch.setattr("urllib.request.urlopen", lambda *_args, **_kwargs: _Response())
    assert run_phase4_campaign._require_mlflow_server(contract) == "http://127.0.0.1:5001"

    def _unreachable(*_args, **_kwargs):
        raise URLError("refused")

    monkeypatch.setattr("urllib.request.urlopen", _unreachable)
    with pytest.raises(run_phase4_campaign.Phase4CampaignError):
        run_phase4_campaign._require_mlflow_server(contract)
    disabled = SimpleNamespace(mlflow_tracing_enabled=False, mlflow_tracking_uri="http://127.0.0.1:5001")
    with pytest.raises(run_phase4_campaign.Phase4CampaignError):
        run_phase4_campaign._require_mlflow_server(disabled)
    for uri in ("databricks", None, "file:///tmp/mlflow"):
        remote = SimpleNamespace(mlflow_tracing_enabled=True, mlflow_tracking_uri=uri)
        with pytest.raises(run_phase4_campaign.Phase4CampaignError):
            run_phase4_campaign._require_mlflow_server(remote)


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


def _retain_fixture_rows(cases) -> list:
    """Mirror the retain fixture: D sweeps suitable, B takes half, A none."""
    rows = []
    for trial in balanced_schedule(cases):
        case = next(item for item in cases if item.identifier == trial.case_id)
        succeeded = (
            trial.arm == "D"
            or case.classification != "suitable"
            or (trial.arm == "B" and case.identifier in {"p4-suitable-01", "p4-suitable-02", "p4-suitable-03"})
        )
        rows.append(score_trial(case, trial, _observation(case, completed=succeeded), PublicRateCard(), _envelope()))
    return rows


def test_decision_imputes_reservation_cost_for_unknown_rows_conservatively() -> None:
    cases = load_cases(_CASES)
    rows = _retain_fixture_rows(cases)
    bootstrap = paired_bootstrap(rows, samples=200)
    assert phase4_decision(rows, bootstrap=bootstrap) == "retain_simplified_profile"
    # Unknown spend on D's winning rows must count against retention at the
    # full reservation, flipping the cost gate to disable.
    imputed = [
        replace(
            row,
            observation=replace(row.observation, input_tokens=None, output_tokens=None),
            observed_cost_usd=None,
        )
        if row.trial.arm == "D" and row.trial.classification == "suitable"
        else row
        for row in rows
    ]
    assert phase4_decision(imputed, bootstrap=bootstrap) == "disable"


def test_decision_requires_call_metrics_only_for_completed_rows() -> None:
    cases = load_cases(_CASES)
    rows = _retain_fixture_rows(cases)
    bootstrap = paired_bootstrap(rows, samples=200)
    stripped_failures = [
        replace(
            row,
            observation=replace(row.observation, root_lm_calls=None, child_lm_calls=None, delegated_bytes=None),
        )
        if not row.observation.completed
        else row
        for row in rows
    ]
    assert any(not row.observation.completed for row in stripped_failures)
    assert phase4_decision(stripped_failures, bootstrap=bootstrap) == "retain_simplified_profile"
    completed_index = next(index for index, row in enumerate(rows) if row.observation.completed)
    stripped_completed = [
        replace(row, observation=replace(row.observation, delegated_bytes=None)) if index == completed_index else row
        for index, row in enumerate(rows)
    ]
    assert phase4_decision(stripped_completed, bootstrap=bootstrap) == "incomplete"


def test_receipt_accounts_unknown_spend_as_charged_reservations() -> None:
    cases = load_cases(_CASES)
    case = cases[0]
    schedule = balanced_schedule((case,) * 12)
    known = score_trial(case, schedule[0], _observation(case), PublicRateCard(), _envelope())
    unknown = score_trial(
        case,
        schedule[1],
        replace(_observation(case), completed=False, input_tokens=None, output_tokens=None),
        PublicRateCard(),
        _envelope(),
    )
    assert known.observed_cost_usd is not None
    assert unknown.observed_cost_usd is None
    payload = receipt(
        (known, unknown),
        corpus_digest="c" * 64,
        policy_digest="b" * 64,
        baseline_revision="9b526f50f0aeec37ca399bc8ef19ec8a95d3bead",
        candidate_revision="a" * 40,
        bootstrap={"point_estimate": None, "ci_lower": None, "ci_upper": None},
        cases=cases,
    )
    assert payload["observed_spend_usd"] is None
    assert payload["unknown_cost_rows"] == 1
    assert Decimal(payload["charged_spend_usd"]) == known.observed_cost_usd + unknown.reserved_cost_usd
    assert payload["rows"][0]["cost_unknown"] is False
    assert payload["rows"][1]["cost_unknown"] is True
    assert payload["rows"][1]["reserved_cost_usd"] == str(unknown.reserved_cost_usd)
    assert payload["summary"]["A:suitable"]["unknown_cost_rows"] == 0
    assert payload["summary"]["B:suitable"]["unknown_cost_rows"] == 1
    assert (
        Decimal(payload["summary"]["B:suitable"]["charged_spend_usd"])
        == unknown.reserved_cost_usd
        == _envelope().upper_bound_usd(PublicRateCard())
    )


def test_worker_evidence_drops_unknown_citations_for_scorer() -> None:
    from scripts.benchmarks.phase4_api_client import _parse_result

    source_ids = {"G1", "G2"}
    answer, cited, _ = _parse_result(
        {"answer": "uses [G1] and [ZZ9]", "evidence": ["G1", "ZZ9"], "uncertainty": ""},
        source_ids,
    )
    assert cited == ("G1",)
    assert "G1" in answer
    # Malformed transport shapes stay fatal instead of becoming scorer facts.
    with pytest.raises(ValueError):
        _parse_result({"answer": "x", "evidence": [1], "uncertainty": ""}, source_ids)
    cases = load_cases(_CASES)
    case = next(item for item in cases if item.identifier == "p4-suitable-01")
    trial = next(t for t in partial_schedule(cases) if t.arm == "B")
    scored = score_trial(case, trial, replace(_observation(case), cited_evidence=()), PublicRateCard(), _envelope())
    assert scored.evidence_valid is False
    assert scored.verified_success is False


def _write_prior_receipt(path: Path, payload: object) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_prior_receipt_null_spend_bounds_unknown_rows(tmp_path: Path) -> None:
    reservation = run_phase4_campaign._envelope().upper_bound_usd(PublicRateCard())
    assert reservation > 0
    receipt_path = _write_prior_receipt(
        tmp_path / "prior.json",
        {
            "observed_spend_usd": None,
            "rows": [
                {"observed_cost_usd": "0.00008512"},
                {"observed_cost_usd": "0.00098868"},
                {"observed_cost_usd": None},
            ],
        },
    )

    spend, status = run_phase4_campaign._prior_receipt_spend(receipt_path)

    assert status == "bounded_upper"
    assert spend is not None
    assert spend == pytest.approx(float(Decimal("0.00008512") + Decimal("0.00098868") + reservation))


def test_prior_receipt_all_known_rows_yields_exact_sum(tmp_path: Path) -> None:
    receipt_path = _write_prior_receipt(
        tmp_path / "prior.json",
        {
            "observed_spend_usd": None,
            "rows": [
                {"observed_cost_usd": "0.00008512"},
                {"observed_cost_usd": "0.00098868"},
            ],
        },
    )

    spend, status = run_phase4_campaign._prior_receipt_spend(receipt_path)

    assert status == "bounded_upper"
    assert spend == pytest.approx(0.00008512 + 0.00098868)


def test_prior_receipt_unreadable_still_blocks_admission(tmp_path: Path) -> None:
    receipt_path = tmp_path / "prior.json"
    receipt_path.write_text("{not json", encoding="utf-8")

    spend, status = run_phase4_campaign._prior_receipt_spend(receipt_path)

    assert spend is None
    assert status == "unreadable"


def test_prior_receipt_empty_rows_stays_blocking(tmp_path: Path) -> None:
    receipt_path = _write_prior_receipt(tmp_path / "prior.json", {"observed_spend_usd": None, "rows": []})

    spend, status = run_phase4_campaign._prior_receipt_spend(receipt_path)

    assert spend is None
    assert status == "unknown"


def test_prior_receipt_all_unknown_rows_bounded_above_zero(tmp_path: Path) -> None:
    reservation = run_phase4_campaign._envelope().upper_bound_usd(PublicRateCard())
    receipt_path = _write_prior_receipt(
        tmp_path / "prior.json",
        {"observed_spend_usd": None, "rows": [{"observed_cost_usd": None}, {"observed_cost_usd": None}]},
    )

    spend, status = run_phase4_campaign._prior_receipt_spend(receipt_path)

    assert status == "bounded_upper"
    assert spend is not None
    assert spend == pytest.approx(float(2 * reservation))
    assert spend > 0


def test_prior_receipt_invalid_rows_still_block_admission(tmp_path: Path) -> None:
    for rows in (
        [{"observed_cost_usd": 0.5}],
        [{"observed_cost_usd": "-0.1"}],
        [{"observed_cost_usd": "nan"}],
        [{"observed_cost_usd": "oops"}],
        ["not-a-mapping"],
        "not-a-list",
    ):
        receipt_path = _write_prior_receipt(tmp_path / "prior.json", {"observed_spend_usd": None, "rows": rows})
        spend, status = run_phase4_campaign._prior_receipt_spend(receipt_path)
        assert spend is None, rows
        assert status in {"unknown", "invalid"}, rows


def test_prior_receipt_top_level_spend_takes_precedence_over_rows(tmp_path: Path) -> None:
    receipt_path = _write_prior_receipt(
        tmp_path / "prior.json",
        {"observed_spend_usd": "0.125", "rows": [{"observed_cost_usd": None}]},
    )

    assert run_phase4_campaign._prior_receipt_spend(receipt_path) == (0.125, "observed")


def test_prior_receipt_charged_spend_takes_precedence_over_observed(tmp_path: Path) -> None:
    receipt_path = _write_prior_receipt(
        tmp_path / "prior.json",
        {
            "charged_spend_usd": "0.5",
            "observed_spend_usd": "0.125",
            "rows": [{"observed_cost_usd": "0.125"}],
        },
    )

    assert run_phase4_campaign._prior_receipt_spend(receipt_path) == (0.5, "charged")
    invalid_path = _write_prior_receipt(
        tmp_path / "invalid.json",
        {"charged_spend_usd": "oops", "rows": [{"observed_cost_usd": "0.125"}]},
    )

    assert run_phase4_campaign._prior_receipt_spend(invalid_path) == (None, "invalid")


def test_campaign_metadata_discloses_bounded_prior_status() -> None:
    specs = arm_specs(baseline_revision="9b526f50f0aeec37ca399bc8ef19ec8a95d3bead", candidate_revision="a" * 40)

    metadata = run_phase4_campaign._campaign_metadata(
        envelope=run_phase4_campaign._envelope(),
        specs=specs,
        name="phase4-test",
        target="databricks-gcp-standard",
        mode="live",
        prior_spend=0.06093188,
        prior_status="bounded_upper",
    )

    assert metadata["prior_receipt_status"] == "bounded_upper"
    assert metadata["prior_observed_spend_usd"] == "0.06093188"


def test_execute_campaign_retains_prior_rows_and_runs_only_the_remainder() -> None:
    cases = load_cases(_CASES)
    schedule = balanced_schedule(cases)
    rates = PublicRateCard()
    envelope = _envelope()
    by_id = {case.identifier: case for case in cases}
    retained = tuple(
        score_trial(by_id[trial.case_id], trial, _observation(by_id[trial.case_id]), rates, envelope)
        for trial in schedule[:-1]
    )
    ran: list[tuple[str, str, int]] = []

    def runner(trial, case):
        ran.append((trial.arm, trial.case_id, trial.repeat))
        return _observation(case)

    outcome = execute_campaign(
        cases=cases,
        preflight=CampaignPreflight("p4", "daytona", 14_400, 144, 5, 50.0),
        envelope=envelope,
        runner=runner,
        started_at=0,
        retained_rows=retained,
    )
    last = schedule[-1]
    assert ran == [(last.arm, last.case_id, last.repeat)]
    assert len(outcome.rows) == 144
    assert outcome.rows[-1].trial == last
    assert outcome.budget["halted"] is False


def test_load_continuation_rows_retries_pre_turn_http_faults(tmp_path: Path) -> None:
    cases = load_cases(_CASES)
    case = cases[0]
    trial = balanced_schedule(cases)[0]
    kept = score_trial(case, trial, _observation(case), PublicRateCard(), _envelope())
    fault_observation = replace(
        _observation(case),
        completed=False,
        authorization_confirmed=False,
        cleanup_confirmed=False,
        input_tokens=None,
        output_tokens=None,
        sandbox_seconds=None,
        sandbox_count=None,
        resource_shape=None,
        error_category="http_400",
        trace_id=None,
    )
    fault = score_trial(case, replace(trial, arm="B"), fault_observation, PublicRateCard(), _envelope())
    payload = {
        "schema": "fleet.phase4-ablation/v1",
        "rows": [kept.receipt(), fault.receipt()],
    }
    path = tmp_path / "halted.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    retained, faults = load_continuation_rows(path)

    assert len(retained) == 1
    assert retained[0].trial == trial
    assert len(faults) == 1
    assert faults[0].observation.error_category == "http_400"
