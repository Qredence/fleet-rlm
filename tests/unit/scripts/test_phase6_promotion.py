"""Contracts for sealed Phase 6 promotion and rollback identities."""

from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scripts import phase6_promotion as promotion
from scripts.validate_release import build_artifact_manifest


def _measurements(bundle, *, score=1.0, seconds=10.0, cost=1.0):
    body = {
        "schema": "fleet.phase6-quality-measurements/v1",
        "bundle_sha256": bundle["bundle_sha256"],
        "execution_mode": "live",
        "complete": True,
        "samples": [
            {"case_id": "case-1", "repetition": repetition, "score": score, "seconds": seconds, "cost_usd": cost}
            for repetition in range(2)
        ],
    }
    return _reseal_measurements(body)


def _reseal_measurements(body):
    body.pop("receipt_sha256", None)
    return {**body, "receipt_sha256": hashlib.sha256(promotion._canonical_bytes(body)).hexdigest()}


@pytest.fixture
def bundle_pair(tmp_path, monkeypatch):
    return (
        _bundle(tmp_path / "baseline", monkeypatch, revision="a" * 40),
        _bundle(tmp_path / "candidate", monkeypatch, revision="b" * 40),
    )


def test_quality_tolerance_boundary_is_not_promotion_authority(bundle_pair):
    baseline, candidate = bundle_pair
    result = promotion.compare_quality(
        baseline, candidate, _measurements(baseline), _measurements(candidate, seconds=11, cost=1.1)
    )
    assert result["comparison_passed"] is True
    assert result["promotion_eligible"] is False


@pytest.mark.parametrize("change", [{"score": 0.9}, {"seconds": 11.01}, {"cost": 1.101}])
def test_quality_regression_fails_comparison(bundle_pair, change):
    baseline, candidate = bundle_pair
    assert (
        promotion.compare_quality(baseline, candidate, _measurements(baseline), _measurements(candidate, **change))[
            "comparison_passed"
        ]
        is False
    )


@pytest.mark.parametrize("mutation", ["partial", "scripted", "bundle", "duplicate", "bool", "unmatched", "gap", "hash"])
def test_quality_rejects_invalid_or_unmatched_evidence(bundle_pair, mutation):
    baseline, candidate = bundle_pair
    receipt = _measurements(candidate)
    if mutation == "partial":
        receipt["complete"] = False
    elif mutation == "scripted":
        receipt["execution_mode"] = "scripted"
    elif mutation == "bundle":
        receipt["bundle_sha256"] = baseline["bundle_sha256"]
    elif mutation == "duplicate":
        receipt["samples"][1] = receipt["samples"][0]
    elif mutation == "bool":
        receipt["samples"][0]["cost_usd"] = True
    elif mutation == "unmatched":
        for sample in receipt["samples"]:
            sample["case_id"] = "case-2"
    elif mutation == "gap":
        receipt["samples"][1]["repetition"] = 2
    if mutation != "hash":
        receipt = _reseal_measurements(receipt)
    else:
        receipt["complete"] = False
    with pytest.raises(promotion.PromotionBundleError):
        promotion.compare_quality(baseline, candidate, _measurements(baseline), receipt)


def _observation(baseline, candidate, now):
    return {
        "schema": "fleet.phase6-switch-observation/v1",
        "baseline_bundle_sha256": baseline["bundle_sha256"],
        "candidate_bundle_sha256": candidate["bundle_sha256"],
        "observed_at": now.isoformat(),
        "admissions_closed": True,
        "active_runs": 0,
        "active_workers": 0,
        "pending_cleanup": 0,
        "provider_cleanup_confirmed": True,
        "database_head": candidate["database_head"],
        "database_compatibility_sha256": "c" * 64,
    }


def test_quiescent_observation_does_not_authorize_switch(bundle_pair):
    baseline, candidate = bundle_pair
    now = datetime.now(UTC)
    result = promotion.validate_switch_observation(baseline, candidate, _observation(baseline, candidate, now), now=now)
    assert result["preflight_passed"] is True
    assert result["switch_eligible"] is False


@pytest.mark.parametrize(
    "field,value",
    [
        ("admissions_closed", False),
        ("admissions_closed", 1),
        ("active_runs", 1),
        ("active_workers", 1),
        ("pending_cleanup", 1),
        ("active_runs", False),
        ("provider_cleanup_confirmed", False),
        ("database_head", "different"),
        ("candidate_bundle_sha256", "e" * 64),
        ("database_compatibility_sha256", "short"),
    ],
)
def test_switch_preflight_refuses_unsafe_or_unknown_state(bundle_pair, field, value):
    baseline, candidate = bundle_pair
    now = datetime.now(UTC)
    observation = _observation(baseline, candidate, now)
    observation[field] = value
    with pytest.raises(promotion.PromotionBundleError):
        promotion.validate_switch_observation(baseline, candidate, observation, now=now)


@pytest.mark.parametrize("offset", [-61, 1])
def test_switch_preflight_rejects_stale_or_future_observation(bundle_pair, offset):
    baseline, candidate = bundle_pair
    now = datetime.now(UTC)
    observation = _observation(baseline, candidate, now + timedelta(seconds=offset))
    with pytest.raises(promotion.PromotionBundleError, match="stale or future"):
        promotion.validate_switch_observation(baseline, candidate, observation, now=now)


def _files(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    config = tmp_path / "config/fleet.toml"
    config.parent.mkdir()
    lock = tmp_path / "uv.lock"
    manifest = tmp_path / "artifact-manifest.json"
    images = tmp_path / "images.json"
    shutil.copy(Path("config/fleet.toml"), config)
    lock.write_text("lock", encoding="utf-8")
    source = tmp_path / "src/fleet_rlm/__init__.py"
    source.parent.mkdir(parents=True)
    source.write_text('__version__ = "0.7.8"\n', encoding="utf-8")
    with zipfile.ZipFile(tmp_path / "fleet_rlm-0.7.8-py3-none-any.whl", "w") as archive:
        archive.writestr("fleet_rlm/__init__.py", source.read_bytes())
    (tmp_path / "fleet_rlm-0.7.8.tar.gz").write_bytes(b"source archive fixture")
    manifest.write_text(json.dumps(build_artifact_manifest(tmp_path)), encoding="utf-8")
    images.write_text(
        json.dumps(
            {
                role: {"snapshot": f"fleet-{role}-v1", "manifest_sha256": "1" * 64, "probe_sha256": "2" * 64}
                for role in ("session", "semantic_child")
            }
        ),
        encoding="utf-8",
    )
    return config, lock, manifest, images


def _bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, revision: str = "a" * 40) -> dict[str, object]:
    config, lock, manifest, images = _files(tmp_path)
    monkeypatch.setattr(promotion, "ROOT", tmp_path)
    monkeypatch.setattr(
        promotion,
        "_git_output",
        lambda *args: {"status": "", "rev-parse": revision, "ls-files": "src/fleet_rlm/__init__.py"}[args[0]],
    )
    return promotion.build_bundle(
        profile="daytona-recursive",
        config=config,
        lock=lock,
        artifact_manifest=manifest,
        images=images,
        database_head="20260914_01",
        dataset_digest="d" * 64,
        scorer_digest="e" * 64,
    )


def test_build_bundle_is_bound_to_clean_head_and_exact_file_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = _bundle(tmp_path, monkeypatch)

    assert bundle["schema"] == promotion.SCHEMA
    assert bundle["revision"] == "a" * 40
    assert bundle["config_sha256"] == hashlib.sha256((tmp_path / "config/fleet.toml").read_bytes()).hexdigest()
    assert promotion.validate_bundle(bundle) == bundle


def test_build_bundle_rejects_dirty_checkout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, lock, manifest, images = _files(tmp_path)
    monkeypatch.setattr(
        promotion, "_git_output", lambda *args: " M src/fleet_rlm/runtime.py" if args[0] == "status" else "a" * 40
    )

    with pytest.raises(promotion.PromotionBundleError, match="must be clean"):
        promotion.build_bundle(
            profile="daytona-recursive",
            config=config,
            lock=lock,
            artifact_manifest=manifest,
            images=images,
            database_head="20260914_01",
            dataset_digest="d" * 64,
            scorer_digest="e" * 64,
        )


def test_rollback_pair_requires_distinct_revisions_and_same_database_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    baseline = _bundle(tmp_path / "baseline", monkeypatch, revision="a" * 40)
    candidate = _bundle(tmp_path / "candidate", monkeypatch, revision="b" * 40)

    pair = promotion.validate_rollback_pair(baseline, candidate)

    assert pair["database_head"] == "20260914_01"
    assert pair["switch_eligible"] is False
    candidate["database_head"] = "20260915_01"
    with pytest.raises(promotion.PromotionBundleError, match="bundle digest"):
        promotion.validate_rollback_pair(baseline, candidate)
    candidate["bundle_sha256"] = hashlib.sha256(
        promotion._canonical_bytes({key: value for key, value in candidate.items() if key != "bundle_sha256"})
    ).hexdigest()
    with pytest.raises(promotion.PromotionBundleError, match="same database head"):
        promotion.validate_rollback_pair(baseline, candidate)


def test_receipts_are_write_once(tmp_path: Path) -> None:
    path = tmp_path / "receipt.json"
    promotion._write_once(path, {"ok": True})

    with pytest.raises(promotion.PromotionBundleError, match="overwrite"):
        promotion._write_once(path, {"ok": False})


@pytest.mark.parametrize(
    "field,value", [("revision", "main"), ("dataset_sha256", "fake"), ("scorer_sha256", 123), ("database_head", True)]
)
def test_resealed_malformed_identities_are_rejected(tmp_path, monkeypatch, field, value):
    bundle = _bundle(tmp_path, monkeypatch)
    bundle[field] = value
    bundle.pop("bundle_sha256")
    bundle["bundle_sha256"] = hashlib.sha256(promotion._canonical_bytes(bundle)).hexdigest()
    with pytest.raises(promotion.PromotionBundleError):
        promotion.validate_bundle(bundle)


@pytest.mark.parametrize("mutation", ["manifest", "wheel", "source", "profile", "revision"])
def test_capture_rejects_unrelated_or_changed_inputs(tmp_path, monkeypatch, mutation):
    bundle = _bundle(tmp_path, monkeypatch)
    if mutation == "manifest":
        (tmp_path / "artifact-manifest.json").write_text("{}")
    elif mutation == "wheel":
        (tmp_path / "fleet_rlm-0.7.8-py3-none-any.whl").write_bytes(b"changed")
    elif mutation == "source":
        (tmp_path / "src/fleet_rlm/__init__.py").write_text("different source")
    elif mutation == "revision":
        revisions = iter([bundle["revision"], "b" * 40])
        monkeypatch.setattr(
            promotion,
            "_git_output",
            lambda *args: (
                next(revisions)
                if args[0] == "rev-parse"
                else "src/fleet_rlm/__init__.py"
                if args[0] == "ls-files"
                else ""
            ),
        )
    with pytest.raises(ValueError):
        promotion.build_bundle(
            profile="absent" if mutation == "profile" else "daytona-recursive",
            config=tmp_path / "config/fleet.toml",
            lock=tmp_path / "uv.lock",
            artifact_manifest=tmp_path / "artifact-manifest.json",
            images=tmp_path / "images.json",
            database_head="20260914_01",
            dataset_digest="d" * 64,
            scorer_digest="e" * 64,
        )


def test_bundle_rejects_unsafe_image_identity(tmp_path, monkeypatch):
    bundle = deepcopy(_bundle(tmp_path, monkeypatch))
    bundle["images"]["session"]["snapshot"] = "https://user:password@host"
    bundle.pop("bundle_sha256")
    bundle["bundle_sha256"] = hashlib.sha256(promotion._canonical_bytes(bundle)).hexdigest()
    with pytest.raises(promotion.PromotionBundleError):
        promotion.validate_bundle(bundle)
