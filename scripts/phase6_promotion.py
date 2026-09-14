#!/usr/bin/env python3
"""Create and compare immutable, non-secret Phase 6 promotion bundles.

This command performs no provider, database, package-publication, or service
switch action. Operators use its receipt as the identity precondition for a
separately authorized maintenance-window promotion or rollback rehearsal.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import subprocess
import sys
import tomllib
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fleet_rlm.optimization.evidence import (
    StrictDaytonaProofError,
    StrictDaytonaProofReceipt,
    validate_strict_daytona_proof,
)
from scripts.benchmarks.campaign import write_receipt_once
from scripts.validate_release import ReleaseValidationError, verify_artifact_manifest

SCHEMA = "fleet.phase6-promotion-bundle/v2"
CAMPAIGN_SCHEMA = "fleet.phase6-quality-campaign/v1"
REHEARSAL_SCHEMA = "fleet.phase6-rollback-rehearsal/v1"
DECISION_SCHEMA = "fleet.phase6-promotion-decision/v1"
DELETION_SCHEMA = "fleet.phase6-deletion-inventory/v1"
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_REVISION = re.compile(r"^(?:[a-f0-9]{40}|[a-f0-9]{64})$")
_MAX_BYTES = 256 * 1024
_SPLITS = frozenset({"selection", "held_out"})
_REHEARSAL_STAGES = ("baseline", "candidate", "baseline", "candidate")
_DELETION_RESULTS = frozenset({"no-op", "deleted-migration-only"})


class PromotionBundleError(ValueError):
    """A promotion identity is incomplete, unsafe, or incompatible."""


def _canonical_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha256_file(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise PromotionBundleError(f"cannot read required identity file: {path}") from exc


def _safe_identifier(value: str, field: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise PromotionBundleError(f"{field} must be a bounded non-secret identifier")
    return value


def _read_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            raw = handle.read(_MAX_BYTES + 1)
        if len(raw) > _MAX_BYTES:
            raise PromotionBundleError("identity JSON exceeds size bound")
        payload = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PromotionBundleError("identity JSON is unreadable") from exc
    if not isinstance(payload, dict):
        raise PromotionBundleError("identity JSON must be an object")
    return payload


def _require_digest(value: object, field: str, *, revision: bool = False) -> str:
    pattern = _REVISION if revision else _SHA256
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise PromotionBundleError(f"{field} must be a full lowercase {'Git SHA' if revision else 'SHA-256'}")
    return value


def _require_bool(value: object, field: str) -> bool:
    if type(value) is not bool:
        raise PromotionBundleError(f"{field} must be a boolean")
    return value


def _require_nonnegative_number(value: object, field: str) -> float:
    if type(value) not in (int, float) or not math.isfinite(float(value)) or float(value) < 0:
        raise PromotionBundleError(f"{field} must be a finite nonnegative number")
    return float(value)


def _sealed_digest(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    return _require_digest(value, field)


def _validated_block_all_proof(payload: dict[str, Any] | None, expected_id: str) -> bool:
    """Validate the persisted strict proof before admitting it to a decision.

    A self-reported proof ID is not evidence.  This adapter accepts only the
    sanitized public receipt emitted by the evidence validator, requires the
    production block-all schema, and binds its canonical proof ID to the
    campaign/decision identity.
    """
    if not isinstance(payload, dict):
        return False
    required = {"schema", "policy_id", "snapshot", "gateway_domains", "controls", "outcomes", "proof_id"}
    if set(payload) != required or payload.get("schema") != "fleet.strict-daytona-proof/v2":
        return False
    if payload.get("proof_id") != expected_id:
        return False
    domains = payload.get("gateway_domains")
    controls = payload.get("controls")
    outcomes = payload.get("outcomes")
    if not isinstance(domains, list) or not isinstance(controls, dict) or not isinstance(outcomes, dict):
        return False
    try:
        receipt = StrictDaytonaProofReceipt(
            schema="fleet.strict-daytona-proof/v2",
            policy_id=payload["policy_id"],
            snapshot=payload["snapshot"],
            gateway_domains=tuple(domains),
            controls=controls,
            outcomes=outcomes,
        )
        proof = validate_strict_daytona_proof(receipt)
    except (StrictDaytonaProofError, TypeError, ValueError, KeyError):
        return False
    return proof.proof_id == expected_id


def _policy_digest(path: Path, profile: str) -> str:
    # The loader validates policy without resolving credentials or contacting
    # providers. Hash only the selected, merged policy, never resolved secrets.
    from fleet_rlm.config.loader import _deep_merge, load_profile_environment_contracts

    if profile not in {item.name for item in load_profile_environment_contracts(path)}:
        raise PromotionBundleError("selected profile is absent from policy")
    document = tomllib.loads(path.read_text(encoding="utf-8"))
    merged = _deep_merge(document["defaults"], document["profiles"][profile])
    return hashlib.sha256(_canonical_bytes(merged)).hexdigest()


def _verify_wheel_sources(manifest: dict[str, Any], directory: Path) -> None:
    wheel = next(item for item in manifest["artifacts"] if item["kind"] == "wheel")
    expected = {
        path.removeprefix("src/"): ROOT / path
        for path in _git_output("ls-files", "src/fleet_rlm").splitlines()
        if path.endswith(".py")
    }
    if not expected:
        raise PromotionBundleError("candidate has no tracked package sources")
    with zipfile.ZipFile(directory / wheel["filename"]) as archive:
        actual = [name for name in archive.namelist() if name.startswith("fleet_rlm/") and name.endswith(".py")]
        if len(actual) != len(set(actual)) or set(actual) != set(expected):
            raise PromotionBundleError("wheel source inventory does not match candidate")
        if any(archive.read(name) != path.read_bytes() for name, path in expected.items()):
            raise PromotionBundleError("wheel source bytes do not match candidate")


def _git_output(*args: str) -> str:
    try:
        return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise PromotionBundleError("git identity cannot be determined") from exc


def build_bundle(
    *,
    profile: str,
    config: Path,
    lock: Path,
    artifact_manifest: Path,
    images: Path,
    database_head: str,
    dataset_digest: str,
    scorer_digest: str,
    revision: str | None = None,
) -> dict[str, Any]:
    """Build a verified, non-secret identity bundle for one clean checkout."""
    if _git_output("status", "--porcelain"):
        raise PromotionBundleError("promotion candidate checkout must be clean")
    head = _git_output("rev-parse", "HEAD")
    selected_revision = _require_digest(revision or head, "revision", revision=True)
    if selected_revision != head:
        raise PromotionBundleError("revision must equal the clean checkout HEAD")
    # Refuse arbitrary policy/lock files that are unrelated to this SHA.
    if config.resolve() != (ROOT / "config/fleet.toml").resolve() or lock.resolve() != (ROOT / "uv.lock").resolve():
        raise PromotionBundleError("config and lock must belong to candidate checkout")
    _read_json(artifact_manifest)  # Enforce the identity-file bound before the shared verifier reads it.
    manifest = verify_artifact_manifest(artifact_manifest, artifact_manifest.parent)
    _verify_wheel_sources(manifest, artifact_manifest.parent)
    manifest_digest = _sha256_file(artifact_manifest)
    image_payload = _read_json(images)
    _load_image_payload(image_payload)
    bundle: dict[str, Any] = {
        "schema": SCHEMA,
        "revision": selected_revision,
        "profile": _safe_identifier(profile, "profile"),
        "config_sha256": _sha256_file(config),
        "resolved_policy_sha256": _policy_digest(config, profile),
        "lock_sha256": _sha256_file(lock),
        "artifact_manifest_sha256": manifest_digest,
        "images": image_payload,
        "database_head": _safe_identifier(database_head, "database_head"),
        "dataset_sha256": _require_digest(dataset_digest, "dataset_digest"),
        "scorer_sha256": _require_digest(scorer_digest, "scorer_digest"),
    }
    if _git_output("rev-parse", "HEAD") != head or _git_output("status", "--porcelain"):
        raise PromotionBundleError("candidate changed during capture")
    # Recheck non-tracked artifacts and external image inputs as well.
    verify_artifact_manifest(artifact_manifest, artifact_manifest.parent)
    if _sha256_file(artifact_manifest) != manifest_digest or _read_json(images) != image_payload:
        raise PromotionBundleError("artifact or image identities changed during capture")
    bundle["bundle_sha256"] = hashlib.sha256(_canonical_bytes(bundle)).hexdigest()
    return bundle


def validate_bundle(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate a persisted bundle and return its canonical representation."""
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
        raise PromotionBundleError("unsupported promotion bundle schema")
    required = {
        "schema",
        "revision",
        "profile",
        "config_sha256",
        "resolved_policy_sha256",
        "lock_sha256",
        "artifact_manifest_sha256",
        "images",
        "database_head",
        "dataset_sha256",
        "scorer_sha256",
        "bundle_sha256",
    }
    if set(payload) != required:
        raise PromotionBundleError("promotion bundle fields are incomplete")
    unsigned = {key: value for key, value in payload.items() if key != "bundle_sha256"}
    if payload["bundle_sha256"] != hashlib.sha256(_canonical_bytes(unsigned)).hexdigest():
        raise PromotionBundleError("promotion bundle digest does not match")
    for field in required:
        if field.endswith("sha256"):
            _require_digest(payload[field], field)
    _require_digest(payload["revision"], "revision", revision=True)
    for field in ("profile", "database_head"):
        _safe_identifier(payload[field], field)
    if not isinstance(payload["images"], dict):
        raise PromotionBundleError("promotion bundle images are invalid")
    _load_image_payload(payload["images"])
    return payload


def _load_image_payload(payload: dict[str, Any]) -> None:
    if set(payload) != {"session", "semantic_child"}:
        raise PromotionBundleError("promotion bundle images are incomplete")
    for key, value in payload.items():
        if not isinstance(value, dict) or set(value) != {"snapshot", "manifest_sha256", "probe_sha256"}:
            raise PromotionBundleError("image must bind snapshot, manifest digest, and probe receipt digest")
        _safe_identifier(value["snapshot"], f"images.{key}.snapshot")
        _require_digest(value["manifest_sha256"], f"images.{key}.manifest_sha256")
        _require_digest(value["probe_sha256"], f"images.{key}.probe_sha256")


def validate_rollback_pair(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    """Check pair identity only; matching heads do not prove data compatibility."""
    baseline = validate_bundle(baseline)
    candidate = validate_bundle(candidate)
    if baseline["revision"] == candidate["revision"]:
        raise PromotionBundleError("rollback baseline and candidate must be distinct revisions")
    if baseline["database_head"] != candidate["database_head"]:
        raise PromotionBundleError("rollback pair requires the same database head; migration review is separate")
    return {
        "schema": "fleet.phase6-rollback-pair/v2",
        "baseline_bundle_sha256": baseline["bundle_sha256"],
        "candidate_bundle_sha256": candidate["bundle_sha256"],
        "database_head": baseline["database_head"],
        "scope": "identity-only",
        "switch_eligible": False,
        "missing_evidence": ["database_compatibility", "quiescence", "durable_continuity"],
    }


def validate_switch_observation(
    baseline: dict[str, Any], candidate: dict[str, Any], observation: dict[str, Any], *, now: datetime
) -> dict[str, Any]:
    """Validate a recent operator observation; never acquire a fence or switch.

    The maintenance controller must keep the admission fence held after this
    observation. An offline receipt cannot eliminate that TOCTOU boundary.
    """
    pair = validate_rollback_pair(baseline, candidate)
    required = {
        "schema",
        "baseline_bundle_sha256",
        "candidate_bundle_sha256",
        "observed_at",
        "admissions_closed",
        "active_runs",
        "active_workers",
        "pending_cleanup",
        "provider_cleanup_confirmed",
        "database_head",
        "database_compatibility_sha256",
    }
    if set(observation) != required or observation["schema"] != "fleet.phase6-switch-observation/v1":
        raise PromotionBundleError("unsupported switch observation")
    for key in ("baseline_bundle_sha256", "candidate_bundle_sha256", "database_head"):
        if observation[key] != pair[key]:
            raise PromotionBundleError("switch observation does not match rollback pair")
    _require_digest(observation["database_compatibility_sha256"], "database_compatibility_sha256")
    try:
        observed_at = datetime.fromisoformat(observation["observed_at"])
        if observed_at.tzinfo is None or now.tzinfo is None:
            raise ValueError("timezone missing")
        age = (now - observed_at).total_seconds()
    except (TypeError, ValueError) as exc:
        raise PromotionBundleError("switch observation timestamp is invalid") from exc
    if not 0 <= age <= 60:
        raise PromotionBundleError("switch observation is stale or future-dated")
    if observation["admissions_closed"] is not True or observation["provider_cleanup_confirmed"] is not True:
        raise PromotionBundleError("admissions or provider cleanup are not confirmed")
    for key in ("active_runs", "active_workers", "pending_cleanup"):
        if type(observation[key]) is not int or observation[key] != 0:
            raise PromotionBundleError("switch observation is not quiescent")
    return {
        "schema": "fleet.phase6-switch-preflight/v1",
        "baseline_bundle_sha256": pair["baseline_bundle_sha256"],
        "candidate_bundle_sha256": pair["candidate_bundle_sha256"],
        "observation_sha256": hashlib.sha256(_canonical_bytes(observation)).hexdigest(),
        "scope": "operator-observation-only",
        "preflight_passed": True,
        "switch_eligible": False,
        "remaining_requirements": [
            "verify_database_compatibility_evidence",
            "hold_admission_fence",
            "durable_continuity",
        ],
    }


def compare_quality(
    baseline_bundle: dict[str, Any],
    candidate_bundle: dict[str, Any],
    baseline: dict[str, Any],
    candidate: dict[str, Any],
) -> dict[str, Any]:
    """Compare matched, sealed measurements, not certify their live provenance.

    Scores and cost must come from the trusted campaign producer. This reader
    does not execute a scorer or authenticate observations, so even a passing
    comparison alone cannot authorize promotion.
    """
    bundles = (validate_bundle(baseline_bundle), validate_bundle(candidate_bundle))
    if bundles[0]["revision"] == bundles[1]["revision"]:
        raise PromotionBundleError("quality comparison requires distinct revisions")
    for field in ("dataset_sha256", "scorer_sha256"):
        if bundles[0][field] != bundles[1][field]:
            raise PromotionBundleError("quality dataset and scorer must match")
    samples_by_arm = []
    required = {"schema", "bundle_sha256", "execution_mode", "complete", "samples", "receipt_sha256"}
    for bundle, receipt in zip(bundles, (baseline, candidate), strict=True):
        if set(receipt) != required or receipt["schema"] != "fleet.phase6-quality-measurements/v1":
            raise PromotionBundleError("unsupported quality measurements")
        body = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
        if receipt["receipt_sha256"] != hashlib.sha256(_canonical_bytes(body)).hexdigest():
            raise PromotionBundleError("quality measurements digest does not match")
        if receipt["bundle_sha256"] != bundle["bundle_sha256"]:
            raise PromotionBundleError("quality measurements do not match bundle")
        if receipt["execution_mode"] != "live" or receipt["complete"] is not True:
            raise PromotionBundleError("quality measurements must be complete live observations")
        samples = receipt["samples"]
        if not isinstance(samples, list) or not 2 <= len(samples) <= 1000:
            raise PromotionBundleError("quality measurements require bounded repeated samples")
        indexed = {}
        for sample in samples:
            if not isinstance(sample, dict) or set(sample) != {"case_id", "repetition", "score", "seconds", "cost_usd"}:
                raise PromotionBundleError("quality sample fields are invalid")
            case_id = _safe_identifier(sample["case_id"], "case_id")
            repetition = sample["repetition"]
            if type(repetition) is not int or not 0 <= repetition <= 999:
                raise PromotionBundleError("quality repetition is invalid")
            for field in ("score", "seconds", "cost_usd"):
                value = sample[field]
                if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1e12:
                    raise PromotionBundleError("quality measurements must be finite nonnegative numbers")
            if sample["score"] > 1:
                raise PromotionBundleError("quality score exceeds one")
            key = (case_id, repetition)
            if key in indexed:
                raise PromotionBundleError("quality sample is duplicated")
            indexed[key] = sample
        repetitions = {key[1] for key in indexed}
        cases = {key[0] for key in indexed}
        if (
            len(repetitions) < 2
            or repetitions != set(range(len(repetitions)))
            or len(indexed) != len(cases) * len(repetitions)
        ):
            raise PromotionBundleError("quality sample repetitions are incomplete")
        samples_by_arm.append(indexed)
    if set(samples_by_arm[0]) != set(samples_by_arm[1]):
        raise PromotionBundleError("quality sample identities must match")
    summaries = []
    for indexed in samples_by_arm:
        rows = list(indexed.values())
        latencies = sorted(row["seconds"] for row in rows)
        summaries.append(
            {
                "quality": math.fsum(row["score"] for row in rows) / len(rows),
                "p95_seconds": latencies[math.ceil(0.95 * len(rows)) - 1],
                "cost_usd": math.fsum(row["cost_usd"] for row in rows),
            }
        )
    before, after = summaries
    gates = {
        "quality_noninferior": after["quality"] >= before["quality"],
        "latency_within_tolerance": after["p95_seconds"] <= before["p95_seconds"] * 1.10,
        "cost_within_tolerance": after["cost_usd"] <= before["cost_usd"] * 1.10,
    }
    return {
        "schema": "fleet.phase6-quality-comparison/v1",
        "baseline_bundle_sha256": bundles[0]["bundle_sha256"],
        "candidate_bundle_sha256": bundles[1]["bundle_sha256"],
        "measurement_receipts": [baseline["receipt_sha256"], candidate["receipt_sha256"]],
        "max_regression_fraction": 0.10,
        "gates": gates,
        "comparison_passed": all(gates.values()),
        "promotion_eligible": False,
        "remaining_requirements": ["validate_dataset_coverage", "authenticate_live_evidence", "remaining_phase6_gates"],
    }


def build_quality_measurements(
    *,
    bundle_sha256: str,
    samples: list[dict[str, Any]],
    execution_mode: str = "live",
    complete: bool = True,
) -> dict[str, Any]:
    """Seal one host-produced, repeated quality measurement receipt.

    This function only seals already-observed rows; it never executes a model
    or turns an unknown cost into zero.  ``compare_quality`` remains the
    compatibility-bound reader for the resulting v1 receipt.
    """
    _require_digest(bundle_sha256, "bundle_sha256")
    if execution_mode not in {"live", "scripted"} or type(complete) is not bool:
        raise PromotionBundleError("quality measurement execution identity is invalid")
    if not isinstance(samples, list) or not 2 <= len(samples) <= 1000:
        raise PromotionBundleError("quality measurements require bounded repeated samples")
    normalized: list[dict[str, Any]] = []
    identities: set[tuple[str, int]] = set()
    for sample in samples:
        if not isinstance(sample, dict) or set(sample) != {"case_id", "repetition", "score", "seconds", "cost_usd"}:
            raise PromotionBundleError("quality sample fields are invalid")
        case_id = _safe_identifier(sample["case_id"], "case_id")
        repetition = sample["repetition"]
        if type(repetition) is not int or not 0 <= repetition <= 999:
            raise PromotionBundleError("quality repetition is invalid")
        checked: dict[str, Any] = {"case_id": case_id, "repetition": repetition}
        for field in ("score", "seconds", "cost_usd"):
            value = sample[field]
            if type(value) not in (int, float) or isinstance(value, bool) or not math.isfinite(value) or value < 0:
                raise PromotionBundleError("quality measurements must be finite nonnegative numbers")
            if value > 1 and field == "score":
                raise PromotionBundleError("quality score exceeds one")
            checked[field] = float(value)
        identity = (case_id, repetition)
        if identity in identities:
            raise PromotionBundleError("quality sample is duplicated")
        identities.add(identity)
        normalized.append(checked)
    repetitions = {repetition for _, repetition in identities}
    cases = {case_id for case_id, _ in identities}
    if (
        len(repetitions) < 2
        or repetitions != set(range(len(repetitions)))
        or len(identities) != len(cases) * len(repetitions)
    ):
        raise PromotionBundleError("quality sample repetitions are incomplete")
    body = {
        "schema": "fleet.phase6-quality-measurements/v1",
        "bundle_sha256": bundle_sha256,
        "execution_mode": execution_mode,
        "complete": complete,
        "samples": normalized,
    }
    return {**body, "receipt_sha256": hashlib.sha256(_canonical_bytes(body)).hexdigest()}


def build_quality_campaign(
    *,
    baseline_bundle: dict[str, Any],
    candidate_bundle: dict[str, Any],
    baseline_measurements: dict[str, Any],
    candidate_measurements: dict[str, Any],
    split: str,
    model_id: str,
    policy_id: str,
    seed: int,
    strict_proof_id: str,
    capability_coverage_sha256: str,
) -> dict[str, Any]:
    """Seal provenance for one complete, matched live quality campaign.

    The campaign envelope is intentionally separate from the measurement
    reader.  Archived v1 measurement receipts remain readable, while this
    envelope binds the producer's split, models, policy, strict proof, and
    matched comparison into one auditable identity.
    """
    baseline = validate_bundle(baseline_bundle)
    candidate = validate_bundle(candidate_bundle)
    if split not in _SPLITS:
        raise PromotionBundleError("quality campaign split must be selection or held_out")
    _safe_identifier(model_id, "model_id")
    _safe_identifier(policy_id, "policy_id")
    if type(seed) is not int or seed < 0:
        raise PromotionBundleError("quality campaign seed must be a nonnegative integer")
    _require_digest(strict_proof_id, "strict_proof_id")
    _require_digest(capability_coverage_sha256, "capability_coverage_sha256")
    comparison = compare_quality(baseline, candidate, baseline_measurements, candidate_measurements)
    measurement_receipts = {
        "baseline": _sealed_digest(baseline_measurements, "receipt_sha256"),
        "candidate": _sealed_digest(candidate_measurements, "receipt_sha256"),
    }
    baseline_repetitions = {
        sample["repetition"] for sample in baseline_measurements["samples"] if isinstance(sample, dict)
    }
    candidate_repetitions = {
        sample["repetition"] for sample in candidate_measurements["samples"] if isinstance(sample, dict)
    }
    if baseline_repetitions != candidate_repetitions or len(baseline_repetitions) < 2:
        raise PromotionBundleError("quality campaign requires matched repeated measurements")
    unsigned = {
        "schema": CAMPAIGN_SCHEMA,
        "baseline_bundle_sha256": baseline["bundle_sha256"],
        "candidate_bundle_sha256": candidate["bundle_sha256"],
        "dataset_sha256": baseline["dataset_sha256"],
        "scorer_sha256": baseline["scorer_sha256"],
        "split": split,
        "model_id": model_id,
        "policy_id": policy_id,
        "seed": seed,
        "repetitions": sorted(baseline_repetitions),
        "strict_proof_id": strict_proof_id,
        "capability_coverage_sha256": capability_coverage_sha256,
        "measurement_receipts": measurement_receipts,
        "comparison_sha256": hashlib.sha256(_canonical_bytes(comparison)).hexdigest(),
        "comparison_passed": comparison["comparison_passed"],
        "execution_mode": "live",
        "complete": True,
    }
    return {**unsigned, "campaign_sha256": hashlib.sha256(_canonical_bytes(unsigned)).hexdigest()}


def validate_quality_campaign(
    payload: dict[str, Any],
    *,
    baseline_bundle: dict[str, Any] | None = None,
    candidate_bundle: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate a sealed campaign envelope without trusting its comparison claim."""
    required = {
        "schema",
        "baseline_bundle_sha256",
        "candidate_bundle_sha256",
        "dataset_sha256",
        "scorer_sha256",
        "split",
        "model_id",
        "policy_id",
        "seed",
        "repetitions",
        "strict_proof_id",
        "capability_coverage_sha256",
        "measurement_receipts",
        "comparison_sha256",
        "comparison_passed",
        "execution_mode",
        "complete",
        "campaign_sha256",
    }
    if not isinstance(payload, dict) or set(payload) != required or payload.get("schema") != CAMPAIGN_SCHEMA:
        raise PromotionBundleError("quality campaign envelope is incomplete or unsupported")
    unsigned = {key: value for key, value in payload.items() if key != "campaign_sha256"}
    if payload["campaign_sha256"] != hashlib.sha256(_canonical_bytes(unsigned)).hexdigest():
        raise PromotionBundleError("quality campaign digest does not match")
    for field in (
        "baseline_bundle_sha256",
        "candidate_bundle_sha256",
        "dataset_sha256",
        "scorer_sha256",
        "strict_proof_id",
        "capability_coverage_sha256",
        "comparison_sha256",
    ):
        _require_digest(payload[field], field)
    if payload["split"] not in _SPLITS:
        raise PromotionBundleError("quality campaign split is invalid")
    _safe_identifier(payload["model_id"], "model_id")
    _safe_identifier(payload["policy_id"], "policy_id")
    if type(payload["seed"]) is not int or payload["seed"] < 0:
        raise PromotionBundleError("quality campaign seed is invalid")
    repetitions = payload["repetitions"]
    if (
        not isinstance(repetitions, list)
        or len(repetitions) < 2
        or any(type(value) is not int or not 0 <= value <= 999 for value in repetitions)
        or repetitions != sorted(set(repetitions))
    ):
        raise PromotionBundleError("quality campaign repetitions are invalid")
    if set(payload["measurement_receipts"]) != {"baseline", "candidate"}:
        raise PromotionBundleError("quality campaign measurement identities are incomplete")
    for value in payload["measurement_receipts"].values():
        _require_digest(value, "measurement_receipt")
    if payload["execution_mode"] != "live" or payload["complete"] is not True:
        raise PromotionBundleError("quality campaign must be complete live evidence")
    _require_bool(payload["comparison_passed"], "comparison_passed")
    if baseline_bundle is not None or candidate_bundle is not None:
        if baseline_bundle is None or candidate_bundle is None:
            raise PromotionBundleError("quality campaign bundle validation requires both bundles")
        baseline = validate_bundle(baseline_bundle)
        candidate = validate_bundle(candidate_bundle)
        if payload["baseline_bundle_sha256"] != baseline["bundle_sha256"]:
            raise PromotionBundleError("quality campaign baseline does not match bundle")
        if payload["candidate_bundle_sha256"] != candidate["bundle_sha256"]:
            raise PromotionBundleError("quality campaign candidate does not match bundle")
        if (
            payload["dataset_sha256"] != baseline["dataset_sha256"]
            or payload["dataset_sha256"] != candidate["dataset_sha256"]
        ):
            raise PromotionBundleError("quality campaign dataset does not match both bundles")
        if (
            payload["scorer_sha256"] != baseline["scorer_sha256"]
            or payload["scorer_sha256"] != candidate["scorer_sha256"]
        ):
            raise PromotionBundleError("quality campaign scorer does not match both bundles")
    return payload


def build_rollback_rehearsal(
    *,
    baseline_bundle: dict[str, Any],
    candidate_bundle: dict[str, Any],
    stages: list[dict[str, Any]],
) -> dict[str, Any]:
    """Seal the four-stage rollback rehearsal without asserting deployment authority."""
    pair = validate_rollback_pair(baseline_bundle, candidate_bundle)
    if len(stages) != len(_REHEARSAL_STAGES):
        raise PromotionBundleError("rollback rehearsal must contain four ordered stages")
    normalized: list[dict[str, Any]] = []
    for expected_stage, stage in zip(_REHEARSAL_STAGES, stages, strict=True):
        if not isinstance(stage, dict):
            raise PromotionBundleError("rollback rehearsal stages must be objects")
        required = {
            "stage",
            "bundle_sha256",
            "observed_at",
            "session_history_sha256",
            "workspace_sha256",
            "artifacts_sha256",
            "new_turn_sha256",
            "provider_cleanup_confirmed",
            "durable_continuity",
        }
        if set(stage) != required or stage["stage"] != expected_stage:
            raise PromotionBundleError("rollback rehearsal stage identity is invalid")
        expected_bundle = (
            baseline_bundle["bundle_sha256"] if expected_stage == "baseline" else candidate_bundle["bundle_sha256"]
        )
        if stage["bundle_sha256"] != expected_bundle:
            raise PromotionBundleError("rollback rehearsal stage does not match its bundle")
        for field in (
            "bundle_sha256",
            "session_history_sha256",
            "workspace_sha256",
            "artifacts_sha256",
            "new_turn_sha256",
        ):
            _require_digest(stage[field], f"stage.{field}")
        if not isinstance(stage["observed_at"], str) or not stage["observed_at"].strip():
            raise PromotionBundleError("rollback rehearsal timestamp is required")
        _require_bool(stage["provider_cleanup_confirmed"], "stage.provider_cleanup_confirmed")
        _require_bool(stage["durable_continuity"], "stage.durable_continuity")
        normalized.append(stage)
    passed = all(stage["provider_cleanup_confirmed"] and stage["durable_continuity"] for stage in normalized)
    unsigned = {
        "schema": REHEARSAL_SCHEMA,
        "baseline_bundle_sha256": pair["baseline_bundle_sha256"],
        "candidate_bundle_sha256": pair["candidate_bundle_sha256"],
        "database_head": pair["database_head"],
        "stages": normalized,
        "rehearsal_passed": passed,
        "switch_eligible": passed,
    }
    return {**unsigned, "rehearsal_sha256": hashlib.sha256(_canonical_bytes(unsigned)).hexdigest()}


def validate_rollback_rehearsal(
    payload: dict[str, Any],
    *,
    baseline_bundle: dict[str, Any],
    candidate_bundle: dict[str, Any],
) -> dict[str, Any]:
    """Validate a rehearsal receipt and its exact stage/bundle binding."""
    required = {
        "schema",
        "baseline_bundle_sha256",
        "candidate_bundle_sha256",
        "database_head",
        "stages",
        "rehearsal_passed",
        "switch_eligible",
        "rehearsal_sha256",
    }
    if not isinstance(payload, dict) or set(payload) != required or payload.get("schema") != REHEARSAL_SCHEMA:
        raise PromotionBundleError("rollback rehearsal receipt is incomplete or unsupported")
    unsigned = {key: value for key, value in payload.items() if key != "rehearsal_sha256"}
    if payload["rehearsal_sha256"] != hashlib.sha256(_canonical_bytes(unsigned)).hexdigest():
        raise PromotionBundleError("rollback rehearsal digest does not match")
    pair = validate_rollback_pair(baseline_bundle, candidate_bundle)
    if (
        payload["baseline_bundle_sha256"] != pair["baseline_bundle_sha256"]
        or payload["candidate_bundle_sha256"] != pair["candidate_bundle_sha256"]
    ):
        raise PromotionBundleError("rollback rehearsal bundles do not match pair")
    if payload["database_head"] != pair["database_head"]:
        raise PromotionBundleError("rollback rehearsal database head does not match pair")
    checked = build_rollback_rehearsal(
        baseline_bundle=baseline_bundle,
        candidate_bundle=candidate_bundle,
        stages=payload["stages"],
    )
    if checked != payload:
        raise PromotionBundleError("rollback rehearsal receipt is not canonical")
    _require_bool(payload["rehearsal_passed"], "rehearsal_passed")
    _require_bool(payload["switch_eligible"], "switch_eligible")
    if payload["switch_eligible"] is not payload["rehearsal_passed"]:
        raise PromotionBundleError("rollback rehearsal switch eligibility is inconsistent")
    return payload


def build_deletion_inventory(
    *,
    rehearsal_sha256: str,
    candidates: list[dict[str, Any]],
    checks: dict[str, bool],
) -> dict[str, Any]:
    """Seal the post-rehearsal ownership inventory, including an explicit no-op.

    The inventory is deliberately receipt-only.  It cannot authorize deleting
    a path; a deleted entry is accepted only when the operator has already
    supplied a matching rehearsal receipt and all deterministic/release checks.
    When no migration-only path is proven safe, ``result`` is ``no-op`` and the
    retained safety owners are recorded instead of being removed speculatively.
    """
    _require_digest(rehearsal_sha256, "rehearsal_sha256")
    if not isinstance(candidates, list) or len(candidates) > 128:
        raise PromotionBundleError("deletion inventory candidates are invalid")
    required_checks = {
        "import_search",
        "dependency_boundaries",
        "deterministic_suite",
        "security_checks",
        "release_checks",
        "artifacts_rebuilt",
    }
    if set(checks) != required_checks or any(type(value) is not bool for value in checks.values()):
        raise PromotionBundleError("deletion inventory checks are incomplete")
    normalized: list[dict[str, Any]] = []
    for candidate in candidates:
        if not isinstance(candidate, dict) or set(candidate) != {
            "path",
            "status",
            "rehearsal_covered",
            "owner",
        }:
            raise PromotionBundleError("deletion inventory entry is invalid")
        path = candidate["path"]
        owner = candidate["owner"]
        if (
            not isinstance(path, str)
            or not path
            or path.startswith("/")
            or ".." in Path(path).parts
            or not isinstance(owner, str)
            or not owner
            or not _IDENTIFIER.fullmatch(owner)
        ):
            raise PromotionBundleError("deletion inventory path or owner is invalid")
        if candidate["status"] not in {"retained", "deleted-migration-only"}:
            raise PromotionBundleError("deletion inventory status is invalid")
        _require_bool(candidate["rehearsal_covered"], "candidate.rehearsal_covered")
        if candidate["status"] == "deleted-migration-only" and candidate["rehearsal_covered"] is not True:
            raise PromotionBundleError("deleted migration-only code requires rehearsal coverage")
        normalized.append(candidate)
    if not all(checks.values()) or any(candidate["status"] == "retained" for candidate in normalized):
        result = "no-op"
    else:
        result = "deleted-migration-only" if normalized else "no-op"
    unsigned = {
        "schema": DELETION_SCHEMA,
        "rehearsal_sha256": rehearsal_sha256,
        "candidates": normalized,
        "checks": checks,
        "result": result,
        "retained_safety_owners": [
            "retained_broker_execution",
            "generation_and_fencing",
            "provider_cleanup_ownership",
            "tool_progress_fingerprints",
            "historical_receipt_readers",
            "compatibility_parsers",
        ],
        "warm_capacity": "deferred-0.7.8",
    }
    return {**unsigned, "inventory_sha256": hashlib.sha256(_canonical_bytes(unsigned)).hexdigest()}


def validate_deletion_inventory(payload: dict[str, Any], *, rehearsal_sha256: str) -> dict[str, Any]:
    """Validate the write-once deletion inventory and its rehearsal binding."""
    required = {
        "schema",
        "rehearsal_sha256",
        "candidates",
        "checks",
        "result",
        "retained_safety_owners",
        "warm_capacity",
        "inventory_sha256",
    }
    if not isinstance(payload, dict) or set(payload) != required or payload.get("schema") != DELETION_SCHEMA:
        raise PromotionBundleError("deletion inventory is incomplete or unsupported")
    unsigned = {key: value for key, value in payload.items() if key != "inventory_sha256"}
    if payload["inventory_sha256"] != hashlib.sha256(_canonical_bytes(unsigned)).hexdigest():
        raise PromotionBundleError("deletion inventory digest does not match")
    if payload["rehearsal_sha256"] != rehearsal_sha256:
        raise PromotionBundleError("deletion inventory is not bound to the rollback rehearsal")
    if payload["result"] not in _DELETION_RESULTS:
        raise PromotionBundleError("deletion inventory result is invalid")
    if payload["warm_capacity"] != "deferred-0.7.8":
        raise PromotionBundleError("deletion inventory changed the deferred warm-capacity policy")
    owners = payload["retained_safety_owners"]
    if not isinstance(owners, list) or any(
        not isinstance(owner, str) or not _IDENTIFIER.fullmatch(owner) for owner in owners
    ):
        raise PromotionBundleError("retained safety owners are invalid")
    if len(owners) != len(set(owners)):
        raise PromotionBundleError("retained safety owners are invalid")
    checked = build_deletion_inventory(
        rehearsal_sha256=rehearsal_sha256,
        candidates=payload["candidates"],
        checks=payload["checks"],
    )
    if checked != payload:
        raise PromotionBundleError("deletion inventory is not canonical")
    return payload


def build_promotion_decision(
    *,
    baseline_bundle: dict[str, Any],
    candidate_bundle: dict[str, Any],
    rollback_pair: dict[str, Any],
    switch_preflight: dict[str, Any],
    campaign: dict[str, Any],
    rehearsal: dict[str, Any],
    strict_proof_id: str,
    strict_proof_receipt: dict[str, Any] | None = None,
    deletion_inventory: dict[str, Any] | None = None,
    clean_candidate_verified: bool,
    trusted_scorer_verified: bool,
    database_compatibility_verified: bool,
) -> dict[str, Any]:
    """Produce the single fail-closed promotion decision receipt."""
    baseline = validate_bundle(baseline_bundle)
    candidate = validate_bundle(candidate_bundle)
    pair = validate_rollback_pair(baseline, candidate)
    if rollback_pair != pair:
        raise PromotionBundleError("rollback pair receipt does not match bundles")
    if not isinstance(switch_preflight, dict) or switch_preflight.get("preflight_passed") is not True:
        raise PromotionBundleError("promotion decision requires a passing switch preflight")
    if switch_preflight.get("schema") != "fleet.phase6-switch-preflight/v1":
        raise PromotionBundleError("promotion decision requires the versioned switch preflight")
    if (
        switch_preflight.get("baseline_bundle_sha256") != baseline["bundle_sha256"]
        or switch_preflight.get("candidate_bundle_sha256") != candidate["bundle_sha256"]
        or switch_preflight.get("switch_eligible") is not False
    ):
        raise PromotionBundleError("switch preflight is not bound to the rollback pair")
    _require_digest(switch_preflight.get("observation_sha256"), "observation_sha256")
    validate_quality_campaign(campaign, baseline_bundle=baseline, candidate_bundle=candidate)
    if campaign["strict_proof_id"] != strict_proof_id:
        raise PromotionBundleError("campaign and decision strict proof identities differ")
    validate_rollback_rehearsal(rehearsal, baseline_bundle=baseline, candidate_bundle=candidate)
    if deletion_inventory is not None:
        if rehearsal.get("rehearsal_passed") is not True:
            raise PromotionBundleError("deletion inventory requires a passing rollback rehearsal")
        validate_deletion_inventory(deletion_inventory, rehearsal_sha256=rehearsal["rehearsal_sha256"])
    _require_digest(strict_proof_id, "strict_proof_id")
    for field, value in (
        ("clean_candidate_verified", clean_candidate_verified),
        ("trusted_scorer_verified", trusted_scorer_verified),
        ("database_compatibility_verified", database_compatibility_verified),
    ):
        _require_bool(value, field)
    gates = {
        "clean_candidate": clean_candidate_verified,
        "strict_daytona": _validated_block_all_proof(strict_proof_receipt, strict_proof_id),
        "trusted_scorer": trusted_scorer_verified,
        "campaign_complete": campaign["complete"],
        "quality_noninferior": campaign["comparison_passed"],
        "latency_within_tolerance": campaign["comparison_passed"],
        "cost_within_tolerance": campaign["comparison_passed"],
        "database_compatibility": database_compatibility_verified,
        "rollback_rehearsal": rehearsal["rehearsal_passed"],
        "quiescent": switch_preflight["preflight_passed"],
        "deletion_inventory": deletion_inventory is not None,
    }
    blockers = sorted(name for name, passed in gates.items() if not passed)
    unsigned = {
        "schema": DECISION_SCHEMA,
        "baseline_bundle_sha256": baseline["bundle_sha256"],
        "candidate_bundle_sha256": candidate["bundle_sha256"],
        "rollback_pair_sha256": hashlib.sha256(_canonical_bytes(pair)).hexdigest(),
        "switch_preflight_sha256": _sealed_digest(switch_preflight, "observation_sha256"),
        "campaign_sha256": campaign["campaign_sha256"],
        "rehearsal_sha256": rehearsal["rehearsal_sha256"],
        "deletion_inventory_sha256": (
            deletion_inventory["inventory_sha256"] if deletion_inventory is not None else None
        ),
        "strict_proof_id": strict_proof_id,
        "gates": gates,
        "blockers": blockers,
        "promotion_eligible": not blockers,
    }
    return {**unsigned, "decision_sha256": hashlib.sha256(_canonical_bytes(unsigned)).hexdigest()}


def validate_promotion_decision(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate the final decision's self-hash and fail-closed gate semantics."""
    required = {
        "schema",
        "baseline_bundle_sha256",
        "candidate_bundle_sha256",
        "rollback_pair_sha256",
        "switch_preflight_sha256",
        "campaign_sha256",
        "rehearsal_sha256",
        "strict_proof_id",
        "deletion_inventory_sha256",
        "gates",
        "blockers",
        "promotion_eligible",
        "decision_sha256",
    }
    if not isinstance(payload, dict) or set(payload) != required or payload.get("schema") != DECISION_SCHEMA:
        raise PromotionBundleError("promotion decision is incomplete or unsupported")
    unsigned = {key: value for key, value in payload.items() if key != "decision_sha256"}
    if payload["decision_sha256"] != hashlib.sha256(_canonical_bytes(unsigned)).hexdigest():
        raise PromotionBundleError("promotion decision digest does not match")
    for field in (
        "baseline_bundle_sha256",
        "candidate_bundle_sha256",
        "rollback_pair_sha256",
        "switch_preflight_sha256",
        "campaign_sha256",
        "rehearsal_sha256",
        "strict_proof_id",
    ):
        _require_digest(payload[field], field)
    deletion_digest = payload["deletion_inventory_sha256"]
    if deletion_digest is not None:
        _require_digest(deletion_digest, "deletion_inventory_sha256")
    expected_gate_names = {
        "clean_candidate",
        "strict_daytona",
        "trusted_scorer",
        "campaign_complete",
        "quality_noninferior",
        "latency_within_tolerance",
        "cost_within_tolerance",
        "database_compatibility",
        "rollback_rehearsal",
        "quiescent",
        "deletion_inventory",
    }
    if set(payload["gates"]) != expected_gate_names or any(
        type(value) is not bool for value in payload["gates"].values()
    ):
        raise PromotionBundleError("promotion decision gates are invalid")
    blockers = payload["blockers"]
    if (
        not isinstance(blockers, list)
        or blockers != sorted(set(blockers))
        or any(not isinstance(item, str) or item not in expected_gate_names for item in blockers)
    ):
        raise PromotionBundleError("promotion decision blockers are invalid")
    expected_blockers = sorted(name for name, passed in payload["gates"].items() if not passed)
    if blockers != expected_blockers:
        raise PromotionBundleError("promotion decision blockers do not match gates")
    _require_bool(payload["promotion_eligible"], "promotion_eligible")
    if payload["gates"]["deletion_inventory"] is not (deletion_digest is not None):
        raise PromotionBundleError("deletion inventory gate and identity are inconsistent")
    if payload["promotion_eligible"] is not (not blockers):
        raise PromotionBundleError("promotion decision eligibility is inconsistent")
    return payload


def _write_once(path: Path, payload: dict[str, Any]) -> None:
    try:
        write_receipt_once(path, payload, max_bytes=_MAX_BYTES)
    except FileExistsError as exc:
        raise PromotionBundleError(f"refusing to overwrite receipt: {path}") from exc


def _read_bundle(path: Path) -> dict[str, Any]:
    return validate_bundle(_read_json(path))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--profile", required=True)
    prepare.add_argument("--config", type=Path, default=ROOT / "config" / "fleet.toml")
    prepare.add_argument("--lock", type=Path, default=ROOT / "uv.lock")
    prepare.add_argument("--artifact-manifest", type=Path, required=True)
    prepare.add_argument("--images", type=Path, required=True)
    prepare.add_argument("--database-head", required=True)
    prepare.add_argument("--dataset-digest", required=True)
    prepare.add_argument("--scorer-digest", required=True)
    prepare.add_argument("--revision")
    prepare.add_argument("--output", type=Path, required=True)
    pair = commands.add_parser("validate-rollback-pair")
    pair.add_argument("--baseline", type=Path, required=True)
    pair.add_argument("--candidate", type=Path, required=True)
    pair.add_argument("--output", type=Path, required=True)
    preflight = commands.add_parser("validate-switch-preflight")
    preflight.add_argument("--baseline", type=Path, required=True)
    preflight.add_argument("--candidate", type=Path, required=True)
    preflight.add_argument("--observation", type=Path, required=True)
    preflight.add_argument("--output", type=Path, required=True)
    quality = commands.add_parser("compare-quality")
    quality.add_argument("--baseline", type=Path, required=True)
    quality.add_argument("--candidate", type=Path, required=True)
    quality.add_argument("--baseline-measurements", type=Path, required=True)
    quality.add_argument("--candidate-measurements", type=Path, required=True)
    quality.add_argument("--output", type=Path, required=True)
    measurements = commands.add_parser("seal-measurements")
    measurements.add_argument("--bundle-sha256", required=True)
    measurements.add_argument("--samples", type=Path, required=True)
    measurements.add_argument("--execution-mode", choices=("live", "scripted"), default="live")
    measurements.add_argument("--complete", action=argparse.BooleanOptionalAction, default=True)
    measurements.add_argument("--output", type=Path, required=True)
    campaign = commands.add_parser("seal-campaign")
    campaign.add_argument("--baseline", type=Path, required=True)
    campaign.add_argument("--candidate", type=Path, required=True)
    campaign.add_argument("--baseline-measurements", type=Path, required=True)
    campaign.add_argument("--candidate-measurements", type=Path, required=True)
    campaign.add_argument("--split", choices=sorted(_SPLITS), required=True)
    campaign.add_argument("--model-id", required=True)
    campaign.add_argument("--policy-id", required=True)
    campaign.add_argument("--seed", type=int, required=True)
    campaign.add_argument("--strict-proof-id", required=True)
    campaign.add_argument("--capability-coverage-sha256", required=True)
    campaign.add_argument("--output", type=Path, required=True)
    rehearsal = commands.add_parser("seal-rehearsal")
    rehearsal.add_argument("--baseline", type=Path, required=True)
    rehearsal.add_argument("--candidate", type=Path, required=True)
    rehearsal.add_argument("--stages", type=Path, required=True)
    rehearsal.add_argument("--output", type=Path, required=True)
    deletion = commands.add_parser("seal-deletion-inventory")
    deletion.add_argument("--rehearsal-sha256", required=True)
    deletion.add_argument("--candidates", type=Path, required=True)
    deletion.add_argument("--checks", type=Path, required=True)
    deletion.add_argument("--output", type=Path, required=True)
    decision = commands.add_parser("decide")
    decision.add_argument("--baseline", type=Path, required=True)
    decision.add_argument("--candidate", type=Path, required=True)
    decision.add_argument("--rollback-pair", type=Path, required=True)
    decision.add_argument("--switch-preflight", type=Path, required=True)
    decision.add_argument("--campaign", type=Path, required=True)
    decision.add_argument("--rehearsal", type=Path, required=True)
    decision.add_argument("--strict-proof-id", required=True)
    decision.add_argument("--strict-proof", type=Path)
    decision.add_argument("--deletion-inventory", type=Path)
    decision.add_argument("--clean-candidate-verified", action=argparse.BooleanOptionalAction, default=False)
    decision.add_argument("--trusted-scorer-verified", action=argparse.BooleanOptionalAction, default=False)
    decision.add_argument("--database-compatibility-verified", action=argparse.BooleanOptionalAction, default=False)
    decision.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            payload = build_bundle(
                profile=args.profile,
                config=args.config,
                lock=args.lock,
                artifact_manifest=args.artifact_manifest,
                images=args.images,
                database_head=args.database_head,
                dataset_digest=args.dataset_digest,
                scorer_digest=args.scorer_digest,
                revision=args.revision,
            )
            _write_once(args.output, payload)
        elif args.command == "validate-switch-preflight":
            payload = validate_switch_observation(
                _read_bundle(args.baseline),
                _read_bundle(args.candidate),
                _read_json(args.observation),
                now=datetime.now(UTC),
            )
            _write_once(args.output, payload)
        elif args.command == "compare-quality":
            payload = compare_quality(
                _read_bundle(args.baseline),
                _read_bundle(args.candidate),
                _read_json(args.baseline_measurements),
                _read_json(args.candidate_measurements),
            )
            _write_once(args.output, payload)
        elif args.command == "seal-measurements":
            samples = _read_json(args.samples).get("samples")
            if not isinstance(samples, list):
                raise PromotionBundleError("samples file must contain a samples list")
            payload = build_quality_measurements(
                bundle_sha256=args.bundle_sha256,
                samples=samples,
                execution_mode=args.execution_mode,
                complete=args.complete,
            )
            _write_once(args.output, payload)
        elif args.command == "seal-campaign":
            payload = build_quality_campaign(
                baseline_bundle=_read_bundle(args.baseline),
                candidate_bundle=_read_bundle(args.candidate),
                baseline_measurements=_read_json(args.baseline_measurements),
                candidate_measurements=_read_json(args.candidate_measurements),
                split=args.split,
                model_id=args.model_id,
                policy_id=args.policy_id,
                seed=args.seed,
                strict_proof_id=args.strict_proof_id,
                capability_coverage_sha256=args.capability_coverage_sha256,
            )
            _write_once(args.output, payload)
        elif args.command == "seal-rehearsal":
            stages = _read_json(args.stages).get("stages")
            if not isinstance(stages, list):
                raise PromotionBundleError("rehearsal stages file must contain a stages list")
            payload = build_rollback_rehearsal(
                baseline_bundle=_read_bundle(args.baseline),
                candidate_bundle=_read_bundle(args.candidate),
                stages=stages,
            )
            _write_once(args.output, payload)
        elif args.command == "seal-deletion-inventory":
            candidates = _read_json(args.candidates).get("candidates")
            checks = _read_json(args.checks)
            if not isinstance(candidates, list):
                raise PromotionBundleError("candidates file must contain a candidates list")
            payload = build_deletion_inventory(
                rehearsal_sha256=args.rehearsal_sha256,
                candidates=candidates,
                checks=checks,
            )
            _write_once(args.output, payload)
        elif args.command == "decide":
            payload = build_promotion_decision(
                baseline_bundle=_read_bundle(args.baseline),
                candidate_bundle=_read_bundle(args.candidate),
                rollback_pair=_read_json(args.rollback_pair),
                switch_preflight=_read_json(args.switch_preflight),
                campaign=_read_json(args.campaign),
                rehearsal=_read_json(args.rehearsal),
                strict_proof_id=args.strict_proof_id,
                strict_proof_receipt=_read_json(args.strict_proof) if args.strict_proof else None,
                deletion_inventory=_read_json(args.deletion_inventory) if args.deletion_inventory else None,
                clean_candidate_verified=args.clean_candidate_verified,
                trusted_scorer_verified=args.trusted_scorer_verified,
                database_compatibility_verified=args.database_compatibility_verified,
            )
            _write_once(args.output, payload)
        else:
            payload = validate_rollback_pair(_read_bundle(args.baseline), _read_bundle(args.candidate))
            _write_once(args.output, payload)
    except (ValueError, OSError, zipfile.BadZipFile, ReleaseValidationError) as exc:
        print(f"ERROR: Phase 6 identity validation failed ({type(exc).__name__})", file=sys.stderr)
        return 1
    print(json.dumps(payload, sort_keys=True))
    return 1 if payload.get("comparison_passed") is False or payload.get("promotion_eligible") is False else 0


if __name__ == "__main__":
    raise SystemExit(main())
