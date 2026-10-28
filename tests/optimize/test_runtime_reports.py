from __future__ import annotations

import copy
import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "compare_runtime_reports", Path(__file__).parents[2] / "scripts/compare_runtime_reports.py"
)
reports = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reports)


def _report():
    return {
        "status": "passed",
        "metrics": {
            case: {
                "route": case.removeprefix("route_"),
                "answer": answer,
                "lm_calls": 3,
                "wall_clock_s": 1.0,
                "outcome": "turn_complete",
                "event_kinds": ["turn_complete", "done"],
            }
            for case, answer in reports.EXPECTED_ANSWERS.items()
        },
    }


def _compare(baseline, after):
    return reports.compare([(Path("baseline.json"), baseline)], after)


def test_identical_correct_reports_pass():
    assert _compare(_report(), _report())["passed"]


@pytest.mark.parametrize("case", ["route_rlm", "route_flex", "route_hybrid"])
@pytest.mark.parametrize("side", ["baseline", "after"])
def test_wrong_nonempty_route_answer_fails(case, side):
    baseline, after = _report(), _report()
    (baseline if side == "baseline" else after)["metrics"][case]["answer"] = "wrong"
    verdict = _compare(baseline, after)
    assert not verdict["passed"]
    assert any("answer" in finding for finding in verdict["cases"][case]["findings"])


@pytest.mark.parametrize("omit_after", [False, True])
def test_case_missing_from_one_baseline_cannot_be_excluded(omit_after):
    baseline = _report()
    incomplete = copy.deepcopy(baseline)
    del incomplete["metrics"]["route_flex"]
    after = _report()
    if omit_after:
        del after["metrics"]["route_flex"]
    verdict = reports.compare([(Path("one"), baseline), (Path("two"), incomplete)], after)
    assert not verdict["passed"]
    if omit_after:
        assert verdict["missing_in_after"] == ["route_flex"]
    else:
        assert "case missing from a baseline report" in verdict["cases"]["route_flex"]["findings"]


@pytest.mark.parametrize("calls", [None, "3", True, -1, 4])
def test_missing_invalid_or_increased_after_budget_fails(calls):
    after = _report()
    after["metrics"]["route_rlm"]["lm_calls"] = calls
    assert not _compare(_report(), after)["passed"]


def test_omitted_after_budget_fails():
    after = _report()
    del after["metrics"]["route_rlm"]["lm_calls"]
    assert not _compare(_report(), after)["passed"]
