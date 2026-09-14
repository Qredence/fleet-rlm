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


def test_campaign_unknown_spend_charges_reservation_and_continues_with_confirmed_cleanup() -> None:
    budget = CampaignBudget(_campaign(total_spend_cap=1.0, max_admissions=3), started_at=0)
    budget.reserve(upper_bound_usd=0.5, now=0, max_trial_seconds=30)
    budget.settle_unknown(cleanup_confirmed=True)
    assert budget.receipt()["halted"] is False
    assert budget.receipt()["observed_spend_usd"] == "0.5"
    assert budget.receipt()["reserved_spend_usd"] is None
    assert budget.reserve(upper_bound_usd=0.5, now=30, max_trial_seconds=30) == 2
    budget.settle(actual_usd=0.1, cleanup_confirmed=True)
    assert budget.receipt()["observed_spend_usd"] == "0.6"


def test_campaign_unknown_spend_with_unconfirmed_cleanup_still_halts() -> None:
    budget = CampaignBudget(_campaign(), started_at=0)
    budget.reserve(upper_bound_usd=0.5, now=0, max_trial_seconds=30)
    with pytest.raises(CampaignAdmissionError, match="unavailable"):
        budget.settle_unknown(cleanup_confirmed=False)
    assert budget.receipt()["halted"] is True
    assert budget.receipt()["halt_reason"] == "unconfirmed_cleanup"
    with pytest.raises(CampaignAdmissionError, match="halted"):
        budget.reserve(upper_bound_usd=0.1, now=30, max_trial_seconds=30)


def test_campaign_halt_reason_distinguishes_unknown_spend_from_bound_breach() -> None:
    budget = CampaignBudget(_campaign(), started_at=0)
    budget.reserve(upper_bound_usd=0.5, now=0, max_trial_seconds=30)
    with pytest.raises(CampaignAdmissionError):
        budget.settle(actual_usd=None, cleanup_confirmed=True)
    assert budget.receipt()["halt_reason"] == "unknown_spend"

    budget = CampaignBudget(_campaign(), started_at=0)
    budget.reserve(upper_bound_usd=0.5, now=0, max_trial_seconds=30)
    with pytest.raises(CampaignAdmissionError, match="asserted cost bound"):
        budget.settle(actual_usd=0.6, cleanup_confirmed=True)
    assert budget.receipt()["halt_reason"] == "cost_bound_breach"


def test_campaign_charged_reservations_count_toward_the_spend_cap() -> None:
    budget = CampaignBudget(_campaign(total_spend_cap=1.0, max_admissions=5), started_at=0)
    budget.reserve(upper_bound_usd=0.6, now=0, max_trial_seconds=30)
    budget.settle_unknown(cleanup_confirmed=True)
    with pytest.raises(CampaignAdmissionError, match="spend reservation"):
        budget.reserve(upper_bound_usd=0.6, now=30, max_trial_seconds=30)
    assert budget.receipt()["halted"] is False


def test_campaign_cost_and_time_caps_reject_before_admission() -> None:
    budget = CampaignBudget(_campaign(total_spend_cap=1.0), started_at=0)
    with pytest.raises(CampaignAdmissionError, match="spend reservation"):
        budget.reserve(upper_bound_usd=1.01, now=0, max_trial_seconds=30)
    with pytest.raises(CampaignAdmissionError, match="time reserve"):
        budget.reserve(upper_bound_usd=0.1, now=890, max_trial_seconds=30)
    assert budget.receipt()["admissions"] == 0


def test_receipt_writer_preserves_canonical_bytes_permissions_and_prior_evidence(tmp_path):
    import hashlib
    import json
    import stat

    from scripts.benchmarks.campaign import write_receipt_once

    path = tmp_path / "receipts" / "result.json"
    payload = {"status": "incomplete", "admissions": 0}
    expected = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
    assert write_receipt_once(path, payload) == hashlib.sha256(expected).hexdigest()
    assert path.read_bytes() == expected
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with pytest.raises(FileExistsError):
        write_receipt_once(path, {"status": "passed"})
    assert path.read_bytes() == expected


@pytest.mark.parametrize("payload,limit", [({"status": "large"}, 1), ({"cost": math.nan}, None)])
def test_receipt_writer_rejects_invalid_or_oversized_payload_before_creation(tmp_path, payload, limit):
    from scripts.benchmarks.campaign import write_receipt_once

    path = tmp_path / "result.json"
    with pytest.raises(ValueError):
        write_receipt_once(path, payload, max_bytes=limit)
    assert not path.exists()


@pytest.mark.parametrize("stage", ["fdopen", "fsync"])
def test_receipt_writer_removes_partial_file_and_closes_descriptor(tmp_path, monkeypatch, stage):
    import os

    from scripts.benchmarks import campaign

    path = tmp_path / "result.json"
    descriptors = []
    real_open = os.open

    def recording_open(*args, **kwargs):
        descriptor = real_open(*args, **kwargs)
        descriptors.append(descriptor)
        return descriptor

    def fail(*_args, **_kwargs):
        raise OSError("injected write failure")

    monkeypatch.setattr(campaign.os, "open", recording_open)
    monkeypatch.setattr(campaign.os, stage, fail)
    with pytest.raises(OSError, match="injected write failure"):
        campaign.write_receipt_once(path, {"status": "incomplete"})
    assert not path.exists()
    assert len(descriptors) == 1
    with pytest.raises(OSError):
        os.fstat(descriptors[0])
