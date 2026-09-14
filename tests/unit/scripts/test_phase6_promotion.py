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
from tests.unit.optimization.test_evidence import _block_all_receipt


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


def test_quality_measurement_builder_seals_only_complete_repeated_rows(bundle_pair):
    baseline, _candidate = bundle_pair
    receipt = promotion.build_quality_measurements(
        bundle_sha256=baseline["bundle_sha256"],
        samples=[
            {"case_id": "case-1", "repetition": 0, "score": 1, "seconds": 2, "cost_usd": 0.1},
            {"case_id": "case-1", "repetition": 1, "score": 1, "seconds": 2, "cost_usd": 0.1},
        ],
    )
    assert receipt["execution_mode"] == "live"
    assert receipt["complete"] is True
    assert (
        receipt["receipt_sha256"]
        == hashlib.sha256(
            promotion._canonical_bytes({key: value for key, value in receipt.items() if key != "receipt_sha256"})
        ).hexdigest()
    )
    with pytest.raises(promotion.PromotionBundleError):
        promotion.build_quality_measurements(
            bundle_sha256=baseline["bundle_sha256"],
            samples=[{"case_id": "case-1", "repetition": 0, "score": 1, "seconds": 2, "cost_usd": 0.1}],
        )


def _rehearsal_stages(baseline, candidate):
    stage_rows = []
    for index, (name, bundle) in enumerate(
        (
            ("baseline", baseline),
            ("candidate", candidate),
            ("baseline", baseline),
            ("candidate", candidate),
        )
    ):
        digest = hashlib.sha256(f"{name}-{index}".encode()).hexdigest()
        stage_rows.append(
            {
                "stage": name,
                "bundle_sha256": bundle["bundle_sha256"],
                "observed_at": f"2026-09-14T12:0{index}:00+00:00",
                "session_history_sha256": digest,
                "workspace_sha256": hashlib.sha256(f"workspace-{index}".encode()).hexdigest(),
                "artifacts_sha256": hashlib.sha256(f"artifact-{index}".encode()).hexdigest(),
                "new_turn_sha256": hashlib.sha256(f"turn-{index}".encode()).hexdigest(),
                "provider_cleanup_confirmed": True,
                "durable_continuity": True,
            }
        )
    return stage_rows


def test_campaign_binds_live_measurements_and_all_external_identities(bundle_pair):
    baseline, candidate = bundle_pair
    campaign = promotion.build_quality_campaign(
        baseline_bundle=baseline,
        candidate_bundle=candidate,
        baseline_measurements=_measurements(baseline),
        candidate_measurements=_measurements(candidate, seconds=11, cost=1.1),
        split="held_out",
        model_id="task-model-v1",
        policy_id="strict-block-all-v2",
        seed=42,
        strict_proof_id="f" * 64,
        capability_coverage_sha256="c" * 64,
    )

    assert campaign["schema"] == promotion.CAMPAIGN_SCHEMA
    assert campaign["comparison_passed"] is True
    assert campaign["repetitions"] == [0, 1]
    assert (
        promotion.validate_quality_campaign(campaign, baseline_bundle=baseline, candidate_bundle=candidate) == campaign
    )


@pytest.mark.parametrize("mutation", ["split", "proof", "measurement", "incomplete"])
def test_campaign_rejects_untrusted_or_incomplete_envelopes(bundle_pair, mutation):
    baseline, candidate = bundle_pair
    campaign = promotion.build_quality_campaign(
        baseline_bundle=baseline,
        candidate_bundle=candidate,
        baseline_measurements=_measurements(baseline),
        candidate_measurements=_measurements(candidate),
        split="held_out",
        model_id="task-model-v1",
        policy_id="strict-block-all-v2",
        seed=42,
        strict_proof_id="f" * 64,
        capability_coverage_sha256="c" * 64,
    )
    if mutation == "split":
        campaign["split"] = "train"
    elif mutation == "proof":
        campaign["strict_proof_id"] = "0" * 64
    elif mutation == "measurement":
        campaign["measurement_receipts"]["candidate"] = "0" * 64
    else:
        campaign["complete"] = False
    with pytest.raises(promotion.PromotionBundleError):
        promotion.validate_quality_campaign(campaign, baseline_bundle=baseline, candidate_bundle=candidate)


def test_rehearsal_requires_exact_baseline_candidate_sequence(bundle_pair):
    baseline, candidate = bundle_pair
    rehearsal = promotion.build_rollback_rehearsal(
        baseline_bundle=baseline,
        candidate_bundle=candidate,
        stages=_rehearsal_stages(baseline, candidate),
    )
    assert rehearsal["rehearsal_passed"] is True
    assert rehearsal["switch_eligible"] is True
    assert (
        promotion.validate_rollback_rehearsal(rehearsal, baseline_bundle=baseline, candidate_bundle=candidate)
        == rehearsal
    )
    broken = deepcopy(rehearsal)
    broken["stages"][2]["bundle_sha256"] = candidate["bundle_sha256"]
    with pytest.raises(promotion.PromotionBundleError):
        promotion.validate_rollback_rehearsal(broken, baseline_bundle=baseline, candidate_bundle=candidate)


def test_promotion_decision_is_true_only_when_every_gate_is_proven(bundle_pair):
    baseline, candidate = bundle_pair
    now = datetime.now(UTC)
    observation = _observation(baseline, candidate, now)
    preflight = promotion.validate_switch_observation(baseline, candidate, observation, now=now)
    strict_proof = _block_all_receipt().public_payload()
    strict_proof_id = strict_proof["proof_id"]
    campaign = promotion.build_quality_campaign(
        baseline_bundle=baseline,
        candidate_bundle=candidate,
        baseline_measurements=_measurements(baseline),
        candidate_measurements=_measurements(candidate, seconds=11, cost=1.1),
        split="held_out",
        model_id="task-model-v1",
        policy_id="strict-block-all-v2",
        seed=42,
        strict_proof_id=strict_proof_id,
        capability_coverage_sha256="c" * 64,
    )
    rehearsal = promotion.build_rollback_rehearsal(
        baseline_bundle=baseline,
        candidate_bundle=candidate,
        stages=_rehearsal_stages(baseline, candidate),
    )
    deletion_inventory = promotion.build_deletion_inventory(
        rehearsal_sha256=rehearsal["rehearsal_sha256"],
        candidates=[
            {
                "path": "src/fleet_rlm/daytona/broker.py",
                "status": "retained",
                "rehearsal_covered": True,
                "owner": "retained_broker_execution",
            }
        ],
        checks={
            "import_search": True,
            "dependency_boundaries": True,
            "deterministic_suite": True,
            "security_checks": True,
            "release_checks": True,
            "artifacts_rebuilt": True,
        },
    )
    pair = promotion.validate_rollback_pair(baseline, candidate)
    decision = promotion.build_promotion_decision(
        baseline_bundle=baseline,
        candidate_bundle=candidate,
        rollback_pair=pair,
        switch_preflight=preflight,
        campaign=campaign,
        rehearsal=rehearsal,
        strict_proof_id=strict_proof_id,
        strict_proof_receipt=strict_proof,
        deletion_inventory=deletion_inventory,
        clean_candidate_verified=True,
        trusted_scorer_verified=True,
        database_compatibility_verified=True,
    )
    assert decision["promotion_eligible"] is True
    assert decision["blockers"] == []
    assert promotion.validate_promotion_decision(decision) == decision

    blocked = deepcopy(decision)
    blocked["gates"]["database_compatibility"] = False
    blocked["blockers"] = ["database_compatibility"]
    blocked["promotion_eligible"] = False
    blocked["decision_sha256"] = hashlib.sha256(
        promotion._canonical_bytes({key: value for key, value in blocked.items() if key != "decision_sha256"})
    ).hexdigest()
    assert promotion.validate_promotion_decision(blocked)["promotion_eligible"] is False


def test_promotion_decision_blocks_without_validated_strict_proof_or_deletion_inventory(bundle_pair):
    baseline, candidate = bundle_pair
    now = datetime.now(UTC)
    preflight = promotion.validate_switch_observation(
        baseline, candidate, _observation(baseline, candidate, now), now=now
    )
    campaign = promotion.build_quality_campaign(
        baseline_bundle=baseline,
        candidate_bundle=candidate,
        baseline_measurements=_measurements(baseline),
        candidate_measurements=_measurements(candidate),
        split="held_out",
        model_id="task-model-v1",
        policy_id="strict-block-all-v2",
        seed=42,
        strict_proof_id="f" * 64,
        capability_coverage_sha256="c" * 64,
    )
    rehearsal = promotion.build_rollback_rehearsal(
        baseline_bundle=baseline,
        candidate_bundle=candidate,
        stages=_rehearsal_stages(baseline, candidate),
    )
    decision = promotion.build_promotion_decision(
        baseline_bundle=baseline,
        candidate_bundle=candidate,
        rollback_pair=promotion.validate_rollback_pair(baseline, candidate),
        switch_preflight=preflight,
        campaign=campaign,
        rehearsal=rehearsal,
        strict_proof_id="f" * 64,
        clean_candidate_verified=True,
        trusted_scorer_verified=True,
        database_compatibility_verified=True,
    )
    assert decision["promotion_eligible"] is False
    assert decision["blockers"] == ["deletion_inventory", "strict_daytona"]
    assert promotion.validate_promotion_decision(decision) == decision


def test_blocked_promotion_decision_seals_missing_evidence_without_fabrication(bundle_pair):
    baseline, candidate = bundle_pair
    blockers = [
        "campaign_complete",
        "cost_within_tolerance",
        "database_compatibility",
        "deletion_inventory",
        "latency_within_tolerance",
        "quality_noninferior",
        "quiescent",
        "rollback_rehearsal",
        "strict_daytona",
        "trusted_scorer",
    ]
    decision = promotion.build_blocked_promotion_decision(
        baseline_bundle=baseline,
        candidate_bundle=candidate,
        blockers=blockers,
        clean_candidate_verified=True,
    )
    assert decision["promotion_eligible"] is False
    assert decision["blockers"] == blockers
    assert decision["campaign_sha256"] is None
    assert decision["rehearsal_sha256"] is None
    assert promotion.validate_promotion_decision(decision) == decision


def test_blocked_promotion_decision_rejects_inexact_blocker_list(bundle_pair):
    baseline, candidate = bundle_pair
    with pytest.raises(promotion.PromotionBundleError, match="blockers do not match"):
        promotion.build_blocked_promotion_decision(
            baseline_bundle=baseline,
            candidate_bundle=candidate,
            blockers=["strict_daytona"],
        )


def test_deletion_inventory_records_explicit_noop_and_preserves_safety_owners(bundle_pair):
    baseline, candidate = bundle_pair
    rehearsal = promotion.build_rollback_rehearsal(
        baseline_bundle=baseline,
        candidate_bundle=candidate,
        stages=_rehearsal_stages(baseline, candidate),
    )
    inventory = promotion.build_deletion_inventory(
        rehearsal_sha256=rehearsal["rehearsal_sha256"],
        candidates=[],
        checks={
            "import_search": True,
            "dependency_boundaries": True,
            "deterministic_suite": True,
            "security_checks": True,
            "release_checks": True,
            "artifacts_rebuilt": True,
        },
    )
    assert inventory["result"] == "no-op"
    assert "retained_broker_execution" in inventory["retained_safety_owners"]
    assert promotion.validate_deletion_inventory(inventory, rehearsal_sha256=rehearsal["rehearsal_sha256"]) == inventory


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
