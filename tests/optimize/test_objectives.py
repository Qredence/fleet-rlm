"""Pure objectives: numbers a reviewer can recompute without MLflow, an LM, or a sandbox."""

from __future__ import annotations

import pytest

from fleet_rlm.optimize.objectives import (
    aggregate,
    efficiency,
    efficiency_metric,
    expected_values_metric,
    missing_values,
    route_metric,
    routing_efficiency,
)


@pytest.mark.parametrize("used_rlm", [False, True])
def test_routing_efficiency_never_rewards_more_budget_units(used_rlm):
    scores = [routing_efficiency(used_rlm, calls)[0] for calls in range(0, 40)]
    assert scores == sorted(scores, reverse=True)


def test_routing_efficiency_keeps_its_bands():
    assert routing_efficiency(False, 1) == (1.0, "Fast path completed in 1 budget units.")
    assert routing_efficiency(True, 10) == (0.95, "Bounded RLM/Flex execution used 10 budget units.")


def test_direct_route_is_penalised_after_its_allowance_instead_of_scoring_one():
    # The previous fall-through scored any non-RLM Turn with 3-15 units as 1.0.
    assert routing_efficiency(False, 2)[0] == 1.0
    assert routing_efficiency(False, 3)[0] == pytest.approx(0.98)
    assert routing_efficiency(False, 15)[0] == pytest.approx(0.74)


def test_routing_efficiency_floors_instead_of_going_negative():
    assert routing_efficiency(True, 10_000)[0] == 0.2
    assert routing_efficiency(False, 10_000)[0] == 0.2


def test_efficiency_penalties_and_availability():
    assert efficiency(3, 0, 0) == 1.0
    assert efficiency(5, 1, 1) == 0.67
    pred = type("Pred", (), {"lm_calls": 4, "sandbox_errors": 0, "submit_rejections": 1})()
    assert efficiency_metric(pred) == (0.835, "4 budget units, 0 remote Python errors, 1 submission rejections")
    legacy = type("Pred", (), {"lm_calls": 4, "sandbox_errors": 0})()
    assert efficiency_metric(legacy) is None


def test_missing_values_respects_token_boundaries():
    assert missing_values("the total is 120 and 1.25", [12, 120, 1.25]) == [12]


def test_expected_values_and_route_metrics():
    gold = type("Gold", (), {"expected_values": ["42"], "route": "rlm"})()
    assert expected_values_metric(gold, "answer 42") == (1.0, "all present")
    assert expected_values_metric(gold, "answer 41") == (0.0, "missing ['42']")
    assert expected_values_metric(type("Gold", (), {"expected_values": []})(), "answer") is None
    assert route_metric(gold, type("Pred", (), {"route": "rlm"})()) == (1.0, "expected rlm, took rlm")
    assert route_metric(type("Gold", (), {"route": None})(), type("Pred", (), {"route": "rlm"})()) is None


def test_aggregate_zero_floor_keeps_efficiency_from_offsetting_errors():
    applied = {"answer_correctness": (0.0, "wrong"), "efficiency": (1.0, "cheap")}
    assert aggregate(applied) == (0.0, 0.0)
    correct = {"answer_correctness": (1.0, "right"), "efficiency": (0.5, "medium")}
    assert aggregate(correct) == pytest.approx((0.95, 1.0))


def test_objectives_stay_free_of_services():
    import inspect

    import fleet_rlm.optimize.objectives as objectives

    source = inspect.getsource(objectives)
    assert "mlflow" not in source
    assert "import dspy" not in source
    assert "open(" not in source
