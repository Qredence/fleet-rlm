from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.benchmarks.run_rlm_latency import (
    PHASE6_ARMS,
    PHASE6_CASES_PATH,
    BenchmarkError,
    load_phase6_cases,
    main,
    phase6_plan_receipt,
)


def test_frozen_cases_cover_six_families_with_valid_input_and_rubric_hashes() -> None:
    cases = load_phase6_cases()
    plan = phase6_plan_receipt()

    assert len(cases) == 6
    assert len({case["family"] for case in cases}) == 6
    assert all(len(case["input_sha256"]) == 64 for case in cases)
    assert all(len(case["rubric_sha256"]) == 64 for case in cases)
    assert plan["status"] == "planning_only"
    assert plan["paired_cells_per_arm"] == 36
    assert plan["total_planned_task_executions"] == 108
    assert plan["maximum_root_turn_admissions"] == 126
    assert plan["conditions"] == ["cold", "warm"]
    assert [(arm["id"], arm["child_concurrency"]) for arm in PHASE6_ARMS] == [("A", 0), ("B", 1), ("C", 2)]
    assert plan["resource_envelope"]["shared_maximum_root_turn_admissions"] == 126
    assert {"cost_usd", "spend_status"}.issubset(plan["required_outcomes"])
    assert "not_implemented" in plan["execution_support"]


def test_frozen_case_loader_rejects_input_or_rubric_drift(tmp_path: Path) -> None:
    payload = json.loads(PHASE6_CASES_PATH.read_text(encoding="utf-8"))
    payload["records"][0]["input"] += " changed"
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(BenchmarkError, match="input hash mismatch"):
        load_phase6_cases(cases)


def test_frozen_case_loader_rejects_rubric_drift(tmp_path: Path) -> None:
    payload = json.loads(PHASE6_CASES_PATH.read_text(encoding="utf-8"))
    payload["records"][0]["rubric"]["criteria"].append("new criterion")
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(BenchmarkError, match="rubric hash mismatch"):
        load_phase6_cases(cases)


def test_existing_runner_cli_writes_phase6_planning_receipt(tmp_path: Path) -> None:
    output = tmp_path / "plan.json"

    assert main(["phase6-plan", "--output", str(output)]) == 0
    receipt = json.loads(output.read_text(encoding="utf-8"))
    assert receipt["status"] == "planning_only"
    assert receipt["required_outcomes"]
    assert "token_and_spend_caps" in receipt["resource_envelope"]


def test_plan_command_needs_no_live_opt_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output = tmp_path / "plan.json"
    monkeypatch.delenv("FLEET_LIVE", raising=False)

    assert main(["phase6-plan", "--output", str(output)]) == 0
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "planning_only"


def _synthetic_phase6_outcomes(*, trials: int = 3) -> list[dict[str, object]]:
    from scripts.benchmarks.run_rlm_latency import build_phase6_schedule, load_phase6_cases

    return [
        {
            **cell,
            "correctness": True,
            "grounded_evidence": True,
            "coverage": True,
            "task_completion": True,
            "quality_source": "synthetic_fixture",
            "wall_time_ms": 100.0 if cell["arm"] == "A" else 80.0,
            "input_tokens": None,
            "output_tokens": None,
            "cost_usd": None,
            "spend_status": "unknown",
            "usage_status": "unknown",
            "staging_ms": 0.0,
            "cleanup_status": "unknown",
            "operational_failure": None,
        }
        for cell in build_phase6_schedule(load_phase6_cases(), trials=trials)
    ]


def test_quality_first_analysis_reports_fixture_quality_before_performance() -> None:
    from scripts.benchmarks.run_rlm_latency import analyze_phase6_outcomes

    result = analyze_phase6_outcomes(_synthetic_phase6_outcomes())

    assert result["status"] == "analyzed"
    assert result["quality"]["source_labels"] == ["synthetic_fixture"]
    assert result["quality"]["scores"]["A"]["correctness"] == 1.0
    assert result["performance"]["status"] == "available_descriptive_only"
    assert set(result["performance"]["by_condition"]) == {"cold", "warm"}
    assert result["performance"]["paired_deltas_ms"]["B"] == -20.0
    assert result["observability"]["usage_unknown_outcomes"] == 108
    assert result["observability"]["cleanup_unknown_outcomes"] == 108
    assert result["cost"]["by_arm"]["A"]["status"] == "unknown"
    assert result["cost"]["by_arm"]["A"]["mean_usd"] is None
    assert "no live execution claim" in result["provenance"]


def test_quality_regression_suppresses_performance_comparison() -> None:
    from scripts.benchmarks.run_rlm_latency import analyze_phase6_outcomes

    outcomes = _synthetic_phase6_outcomes()
    next(row for row in outcomes if row["arm"] == "C" and row["family"] == "sparse_retrieval")["grounded_evidence"] = (
        False
    )

    result = analyze_phase6_outcomes(outcomes)

    assert result["status"] == "quality_regression"
    assert result["quality"]["regressions_vs_A"]["C"]["sparse_retrieval/cold"] == ["grounded_evidence"]
    assert result["performance"]["status"] == "suppressed_until_quality_passes"


def test_missing_quality_or_paired_cells_suppresses_all_performance() -> None:
    from scripts.benchmarks.run_rlm_latency import analyze_phase6_outcomes

    outcomes = _synthetic_phase6_outcomes()
    outcomes.pop()
    outcomes[0]["coverage"] = None
    result = analyze_phase6_outcomes(outcomes)

    assert result["status"] == "quality_incomplete"
    assert result["missing_outcomes"] == 1
    assert result["quality"]["status"] == "incomplete"
    assert result["performance"]["status"] == "suppressed_until_quality_passes"


def test_offline_analyze_cli_runs_without_live_opt_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    outcomes_path = tmp_path / "outcomes.json"
    output_path = tmp_path / "analysis.json"
    outcomes_path.write_text(json.dumps({"outcomes": _synthetic_phase6_outcomes(trials=1)}), encoding="utf-8")
    monkeypatch.delenv("FLEET_LIVE", raising=False)

    assert (
        main(
            [
                "phase6-analyze",
                "--phase6-outcomes",
                str(outcomes_path),
                "--phase6-trials",
                "1",
                "--output",
                str(output_path),
            ]
        )
        == 0
    )
    assert json.loads(output_path.read_text(encoding="utf-8"))["status"] == "analyzed"


def test_schedule_pairs_every_arm_and_rotates_order_across_three_trials() -> None:
    from scripts.benchmarks.run_rlm_latency import build_phase6_schedule, load_phase6_cases

    schedule = build_phase6_schedule(load_phase6_cases())
    for family in {cell["family"] for cell in schedule}:
        for condition in ("cold", "warm"):
            orders = [
                [
                    cell["arm"]
                    for cell in sorted(
                        (
                            row
                            for row in schedule
                            if row["family"] == family and row["trial"] == trial and row["condition"] == condition
                        ),
                        key=lambda row: row["arm_order"],
                    )
                ]
                for trial in (1, 2, 3)
            ]
            assert len({tuple(order) for order in orders}) == 3
    assert len(schedule) == 108


def test_analyzer_requires_declared_quality_source() -> None:
    from scripts.benchmarks.run_rlm_latency import analyze_phase6_outcomes

    outcomes = _synthetic_phase6_outcomes()
    outcomes[0]["quality_source"] = ""

    result = analyze_phase6_outcomes(outcomes)

    assert result["quality"]["status"] == "incomplete"
    assert "quality_source" in result["quality"]["unknown_fields"][0]
    assert result["performance"]["status"] == "suppressed_until_quality_passes"


def test_phase6_dry_run_cli_materializes_unexecuted_pairs_without_live_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan_path = tmp_path / "dry-run.json"
    analysis_path = tmp_path / "analysis.json"
    monkeypatch.delenv("FLEET_LIVE", raising=False)

    assert main(["phase6-dry-run", "--phase6-trials", "1", "--output", str(plan_path)]) == 0
    fixture = json.loads(plan_path.read_text(encoding="utf-8"))
    assert fixture["status"] == "dry_run_not_executed"
    assert len(fixture["outcomes"]) == 36
    assert {row["sample_status"] for row in fixture["outcomes"]} == {"not_executed"}
    assert all(row["wall_time_ms"] is None for row in fixture["outcomes"])

    assert (
        main(
            [
                "phase6-analyze",
                "--phase6-outcomes",
                str(plan_path),
                "--phase6-trials",
                "1",
                "--output",
                str(analysis_path),
            ]
        )
        == 0
    )
    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    assert analysis["status"] == "quality_incomplete"
    assert analysis["performance"]["status"] == "suppressed_until_quality_passes"
    assert analysis["observability"]["usage_unknown_outcomes"] == 36
    assert analysis["observability"]["cleanup_unknown_outcomes"] == 36
    assert analysis["observability"]["operational_failure_unknown_outcomes"] == 36


def test_quality_gate_checks_each_family_even_when_pooled_score_matches() -> None:
    from scripts.benchmarks.run_rlm_latency import analyze_phase6_outcomes

    outcomes = _synthetic_phase6_outcomes()
    next(row for row in outcomes if row["arm"] == "A" and row["family"] == "sparse_retrieval")["correctness"] = False
    next(row for row in outcomes if row["arm"] == "B" and row["family"] == "exhaustive_semantic_aggregation")[
        "correctness"
    ] = False

    result = analyze_phase6_outcomes(outcomes)

    assert result["quality"]["scores"]["A"]["correctness"] == result["quality"]["scores"]["B"]["correctness"]
    assert result["quality"]["regressions_vs_A"]["B"]["exhaustive_semantic_aggregation/cold"] == ["correctness"]
    assert result["performance"]["status"] == "suppressed_until_quality_passes"


def _reviewable_phase6_outcomes() -> list[dict[str, object]]:
    outcomes = _synthetic_phase6_outcomes()
    for row in outcomes:
        row.update(
            quality_source="reference",
            input_tokens=60,
            output_tokens=40,
            usage_status="observed",
            cleanup_status="confirmed" if row["arm"] != "A" else "not_applicable",
            operational_failure=False,
            source_authorized=True,
            containment_confirmed=True,
            commit_safe=True,
            cost_usd=0.02 if row["arm"] == "A" else 0.03,
            spend_status="provider_reported",
        )
    return outcomes


def test_phase6_provider_cost_is_reported_separately_from_latency() -> None:
    from scripts.benchmarks.run_rlm_latency import analyze_phase6_outcomes

    result = analyze_phase6_outcomes(_reviewable_phase6_outcomes())

    assert result["cost"]["source"] == "provider_reported_only"
    assert result["cost"]["by_arm"]["A"]["mean_usd"] == 0.02
    assert result["cost"]["by_arm"]["B"]["mean_usd"] == pytest.approx(0.03)
    assert result["performance"]["by_arm"]["A"]["mean_ms"] == 100.0
    assert result["performance"]["by_arm"]["B"]["mean_ms"] == 80.0


def test_phase6_retain_gate_requires_repeatable_family_gain() -> None:
    from scripts.benchmarks.run_rlm_latency import analyze_phase6_outcomes

    outcomes = _reviewable_phase6_outcomes()
    for row in outcomes:
        if row["family"] == "sparse_retrieval" and row["arm"] == "A" and row["trial"] in {1, 2}:
            row["grounded_evidence"] = False
    result = analyze_phase6_outcomes(outcomes)
    gate = result["policy_gate"]

    assert gate["status"] == "conditional_analysis_only"
    assert gate["thresholds"]["quality_gain_required_for_retention"] is True
    assert gate["by_family"]["B"]["sparse_retrieval"]["quality_gain_fields"] == ["grounded_evidence"]
    assert gate["by_family"]["B"]["sparse_retrieval"]["retain_candidate"] is True
    assert gate["promotion_authorized"] is False


def test_phase6_latency_only_gate_needs_observed_tokens_within_tolerance() -> None:
    from scripts.benchmarks.run_rlm_latency import analyze_phase6_outcomes

    outcomes = _reviewable_phase6_outcomes()
    for row in outcomes:
        if row["arm"] == "B":
            row["input_tokens"] = 70
            row["output_tokens"] = 50
    gate = analyze_phase6_outcomes(outcomes)["policy_gate"]
    assert gate["by_family"]["B"]["sparse_retrieval"]["latency_gain"] is False
    assert gate["by_family"]["B"]["sparse_retrieval"]["retain_candidate"] is False
    assert gate["by_family"]["C"]["sparse_retrieval"]["latency_gain"] is True
    assert gate["by_family"]["C"]["sparse_retrieval"]["quality_gain_fields"] == []
    assert gate["by_family"]["C"]["sparse_retrieval"]["retain_candidate"] is False

    outcomes[0]["input_tokens"] = None
    gate = analyze_phase6_outcomes(outcomes)["policy_gate"]
    assert gate["by_family"]["B"]["sparse_retrieval"]["latency_gain"] is False


def test_phase6_synthetic_or_unsafe_outcomes_cannot_certify_promotion() -> None:
    from scripts.benchmarks.run_rlm_latency import analyze_phase6_outcomes

    assert analyze_phase6_outcomes(_synthetic_phase6_outcomes())["policy_gate"]["status"] == "fixture_only"
    outcomes = _reviewable_phase6_outcomes()
    outcomes[0]["commit_safe"] = False
    gate = analyze_phase6_outcomes(outcomes)["policy_gate"]
    assert gate["status"] == "safety_failed"
    assert gate["promotion_authorized"] is False
