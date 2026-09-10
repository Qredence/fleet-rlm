from __future__ import annotations

import math

import pytest

from scripts.benchmarks.campaign import (
    CampaignAdmissionError,
    CampaignBudget,
    CampaignPreflight,
    CampaignPreflightError,
)


def _campaign(**overrides: object) -> CampaignPreflight:
    values: dict[str, object] = {
        "name": "containment-20260910",
        "target": "daytona-disposable",
        "max_elapsed_seconds": 1800,
        "max_admissions": 20,
        "max_sandbox_concurrency": 4,
        "total_spend_cap": 25.0,
    }
    values.update(overrides)
    return CampaignPreflight(**values)  # type: ignore[arg-type]


def test_campaign_preflight_is_content_free_and_bounded() -> None:
    campaign = _campaign()
    assert campaign.as_dict() == {
        "name": "containment-20260910",
        "target": "daytona-disposable",
        "max_elapsed_seconds": 1800,
        "max_admissions": 20,
        "max_sandbox_concurrency": 4,
        "total_spend_cap": 25.0,
    }


@pytest.mark.parametrize(
    "field,value",
    [
        ("name", "bad target/name"),
        ("target", "https://provider.example/secret"),
        ("max_elapsed_seconds", 0),
        ("max_admissions", 0),
        ("max_sandbox_concurrency", 0),
        ("total_spend_cap", 0.0),
        ("total_spend_cap", -1.0),
        ("total_spend_cap", math.inf),
        ("total_spend_cap", "25"),
    ],
)
def test_campaign_preflight_rejects_unbounded_or_missing_limits(field: str, value: object) -> None:
    with pytest.raises(CampaignPreflightError):
        _campaign(**{field: value}).validate()


def test_campaign_reserves_before_work_and_only_releases_after_observed_cleanup() -> None:
    budget = CampaignBudget(_campaign(total_spend_cap=1.0, max_admissions=2), started_at=0)
    assert budget.reserve(upper_bound_usd=0.75, now=0, max_trial_seconds=30) == 1
    with pytest.raises(CampaignAdmissionError, match="remains owned"):
        budget.reserve(upper_bound_usd=0.1, now=0, max_trial_seconds=30)
    budget.settle(actual_usd=0.25, cleanup_confirmed=True)
    assert budget.reserve(upper_bound_usd=0.75, now=30, max_trial_seconds=30) == 2
    budget.settle(actual_usd=0.75, cleanup_confirmed=True)
    assert budget.receipt()["observed_spend_usd"] == "1.00"
    with pytest.raises(CampaignAdmissionError, match="admission limit"):
        budget.reserve(upper_bound_usd=0.1, now=60, max_trial_seconds=30)


@pytest.mark.parametrize("actual,clean", [(None, True), (0.1, False), (0.6, True)])
def test_campaign_unknown_cost_unconfirmed_cleanup_and_bound_breach_halt_admission(actual, clean) -> None:
    budget = CampaignBudget(_campaign(), started_at=0)
    budget.reserve(upper_bound_usd=0.5, now=0, max_trial_seconds=30)
    with pytest.raises(CampaignAdmissionError):
        budget.settle(actual_usd=actual, cleanup_confirmed=clean)
    assert budget.receipt()["halted"] is True
    with pytest.raises(CampaignAdmissionError, match="halted"):
        budget.reserve(upper_bound_usd=0.1, now=30, max_trial_seconds=30)


def test_campaign_cost_and_time_caps_reject_before_admission() -> None:
    budget = CampaignBudget(_campaign(total_spend_cap=1.0), started_at=0)
    with pytest.raises(CampaignAdmissionError, match="spend reservation"):
        budget.reserve(upper_bound_usd=1.01, now=0, max_trial_seconds=30)
    with pytest.raises(CampaignAdmissionError, match="time reserve"):
        budget.reserve(upper_bound_usd=0.1, now=890, max_trial_seconds=30)
    assert budget.receipt()["admissions"] == 0
