from __future__ import annotations

import math

import pytest

from scripts.benchmarks.campaign import CampaignPreflight, CampaignPreflightError


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
