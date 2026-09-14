"""Phase 6 promotion identity helpers and inspection-only validators."""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

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
_DECISION_GATES = frozenset(
    {
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
)


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


def _optional_digest(value: object, field: str) -> str | None:
    if value is None:
        return None
    return _require_digest(value, field)


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


def validate_quality_campaign(
    payload: dict[str, Any],
    *,
    baseline_bundle: dict[str, Any] | None = None,
    candidate_bundle: dict[str, Any] | None = None,
    baseline_measurements: dict[str, Any] | None = None,
    candidate_measurements: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate a sealed campaign envelope and its bound comparison receipt.

    v1 receipts written before the comparison object was added remain readable
    for historical inspection.  They are deliberately not promotable because
    the decision builder requires a newly issued ``quality-campaign`` mapping.
    When measurements are supplied, the comparison is recomputed from those
    receipts rather than trusting any caller-provided pass/fail booleans.
    """
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
    comparison_required = required | {"comparison"}
    if (
        not isinstance(payload, dict)
        or set(payload) not in (required, comparison_required)
        or payload.get("schema") != CAMPAIGN_SCHEMA
    ):
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
    if not isinstance(payload["measurement_receipts"], dict) or set(payload["measurement_receipts"]) != {
        "baseline",
        "candidate",
    }:
        raise PromotionBundleError("quality campaign measurement identities are incomplete")
    for value in payload["measurement_receipts"].values():
        _require_digest(value, "measurement_receipt")
    if payload["execution_mode"] != "live" or payload["complete"] is not True:
        raise PromotionBundleError("quality campaign must be complete live evidence")
    _require_bool(payload["comparison_passed"], "comparison_passed")
    comparison = payload.get("comparison")
    if comparison is not None:
        if not isinstance(comparison, dict) or comparison.get("schema") != "fleet.phase6-quality-comparison/v1":
            raise PromotionBundleError("quality campaign comparison receipt is invalid")
        if payload["comparison_sha256"] != hashlib.sha256(_canonical_bytes(comparison)).hexdigest():
            raise PromotionBundleError("quality campaign comparison digest does not match")
        comparison_required = {
            "schema",
            "baseline_bundle_sha256",
            "candidate_bundle_sha256",
            "measurement_receipts",
            "max_regression_fraction",
            "gates",
            "comparison_passed",
            "promotion_eligible",
            "remaining_requirements",
        }
        if set(comparison) != comparison_required:
            raise PromotionBundleError("quality campaign comparison receipt is incomplete")
        if (
            comparison["baseline_bundle_sha256"] != payload["baseline_bundle_sha256"]
            or comparison["candidate_bundle_sha256"] != payload["candidate_bundle_sha256"]
            or comparison["measurement_receipts"]
            != [payload["measurement_receipts"]["baseline"], payload["measurement_receipts"]["candidate"]]
            or comparison["promotion_eligible"] is not False
            or comparison["max_regression_fraction"] != 0.10
        ):
            raise PromotionBundleError("quality campaign comparison is not bound to its envelope")
        gates = comparison["gates"]
        if not isinstance(gates, dict):
            raise PromotionBundleError("quality campaign comparison gates are invalid")
        if set(gates) != {"quality_noninferior", "latency_within_tolerance", "cost_within_tolerance"} or any(
            type(value) is not bool for value in gates.values()
        ):
            raise PromotionBundleError("quality campaign comparison gates are invalid")
        if comparison["comparison_passed"] is not all(gates.values()):
            raise PromotionBundleError("quality campaign comparison pass claim is inconsistent")
        if payload["comparison_passed"] is not comparison["comparison_passed"]:
            raise PromotionBundleError("quality campaign pass claim is inconsistent with comparison")
    elif baseline_measurements is not None or candidate_measurements is not None:
        raise PromotionBundleError("quality campaign comparison receipt is required for measurement validation")
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
    if baseline_measurements is not None or candidate_measurements is not None:
        if baseline_measurements is None or candidate_measurements is None:
            raise PromotionBundleError("quality campaign measurement validation requires both receipts")
        if baseline_bundle is None or candidate_bundle is None:
            raise PromotionBundleError("quality campaign measurement validation requires both bundles")
        recomputed = compare_quality(
            baseline_bundle,
            candidate_bundle,
            baseline_measurements,
            candidate_measurements,
        )
        if recomputed != comparison:
            raise PromotionBundleError("quality campaign comparison does not match measurements")
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
    for field in ("baseline_bundle_sha256", "candidate_bundle_sha256"):
        _require_digest(payload[field], field)
    for field in (
        "rollback_pair_sha256",
        "switch_preflight_sha256",
        "campaign_sha256",
        "rehearsal_sha256",
        "strict_proof_id",
    ):
        _optional_digest(payload[field], field)
    deletion_digest = payload["deletion_inventory_sha256"]
    if deletion_digest is not None:
        _require_digest(deletion_digest, "deletion_inventory_sha256")
    expected_gate_names = _DECISION_GATES
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
    if payload["promotion_eligible"] and any(
        payload[field] is None
        for field in (
            "rollback_pair_sha256",
            "switch_preflight_sha256",
            "campaign_sha256",
            "rehearsal_sha256",
            "strict_proof_id",
        )
    ):
        raise PromotionBundleError("eligible promotion decision has missing evidence identities")
    if payload["gates"]["strict_daytona"] and payload["strict_proof_id"] is None:
        raise PromotionBundleError("strict Daytona gate has no proof identity")
    if payload["gates"]["campaign_complete"] and payload["campaign_sha256"] is None:
        raise PromotionBundleError("campaign gate has no campaign identity")
    if payload["gates"]["rollback_rehearsal"] and payload["rehearsal_sha256"] is None:
        raise PromotionBundleError("rollback gate has no rehearsal identity")
    if payload["gates"]["quiescent"] and payload["switch_preflight_sha256"] is None:
        raise PromotionBundleError("quiescence gate has no preflight identity")
    if payload["gates"]["deletion_inventory"] is not (deletion_digest is not None):
        raise PromotionBundleError("deletion inventory gate and identity are inconsistent")
    if payload["promotion_eligible"] is not (not blockers):
        raise PromotionBundleError("promotion decision eligibility is inconsistent")
    return payload


__all__ = [
    "CAMPAIGN_SCHEMA",
    "DECISION_SCHEMA",
    "DELETION_SCHEMA",
    "REHEARSAL_SCHEMA",
    "SCHEMA",
    "PromotionBundleError",
    "build_deletion_inventory",
    "build_quality_measurements",
    "build_rollback_rehearsal",
    "compare_quality",
    "validate_bundle",
    "validate_deletion_inventory",
    "validate_promotion_decision",
    "validate_quality_campaign",
    "validate_rollback_pair",
    "validate_rollback_rehearsal",
]
