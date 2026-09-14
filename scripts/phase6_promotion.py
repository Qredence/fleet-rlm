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

from scripts.benchmarks.campaign import write_receipt_once
from scripts.validate_release import ReleaseValidationError, verify_artifact_manifest

SCHEMA = "fleet.phase6-promotion-bundle/v2"
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_REVISION = re.compile(r"^(?:[a-f0-9]{40}|[a-f0-9]{64})$")
_MAX_BYTES = 256 * 1024


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
        else:
            payload = validate_rollback_pair(_read_bundle(args.baseline), _read_bundle(args.candidate))
            _write_once(args.output, payload)
    except (ValueError, OSError, zipfile.BadZipFile, ReleaseValidationError) as exc:
        print(f"ERROR: Phase 6 identity validation failed ({type(exc).__name__})", file=sys.stderr)
        return 1
    print(json.dumps(payload, sort_keys=True))
    return 1 if payload.get("comparison_passed") is False else 0


if __name__ == "__main__":
    raise SystemExit(main())
