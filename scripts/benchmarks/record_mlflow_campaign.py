"""Record a sealed benchmark campaign receipt as an explicit MLflow tracking run.

The runtime benchmark and adapter replay lanes seal content-free receipts
locally first.  This operator-only command is the explicit bridge that turns
one sealed campaign receipt into an MLflow tracking run with an explicit
purpose and configuration identity.  It records identity fields as
parameters/tags, measured numeric outcomes as metrics derived from the
receipt's full-run samples (failures stay in the denominators), the sealed
receipt as one canonical artifact, and evidence-lane/promotion-eligibility
tags.  Scripted echo/keyword receipts can never be marked promotion-eligible.
It never changes Fleet runtime policy and never creates a Turn or sandbox.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import tempfile
from collections.abc import Mapping, Sequence
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

RECEIPT_SCHEMA = "fleet.benchmark-mlflow-campaign/v1"
RUNTIME_SCHEMA = "fleet.runtime-benchmark/v2"
ADAPTER_SCHEMA = "fleet.runtime-adapter-comparison/v2"
SUPPORTED_RECEIPT_SCHEMAS = frozenset({RUNTIME_SCHEMA, ADAPTER_SCHEMA})
DEFAULT_ARTIFACT_PATH = "fleet-benchmark-campaign"
_LIVE_VALUES = frozenset({"1", "true", "yes"})
_MAX_PARAM_CHARS = 500
_METRIC_PREFIX = "fleet.benchmark."


class CampaignRecordError(RuntimeError):
    """A benchmark receipt or MLflow campaign contract failed."""


def _require_live() -> None:
    """Require explicit operator opt-in before contacting MLflow."""
    if os.environ.get("FLEET_LIVE", "").strip().lower() not in _LIVE_VALUES:
        raise CampaignRecordError("FLEET_LIVE=1 is required for MLflow campaign recording")


def load_receipt(path: Path) -> dict[str, Any]:
    """Load and verify one sealed, supported benchmark campaign receipt."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CampaignRecordError("benchmark receipt could not be read as sealed JSON") from exc
    if not isinstance(payload, Mapping) or payload.get("schema") not in SUPPORTED_RECEIPT_SCHEMAS:
        raise CampaignRecordError("receipt schema is not a supported benchmark campaign schema")
    receipt = dict(payload)
    recorded_digest = receipt.get("receipt_digest")
    from scripts.benchmarks.runtime_v2 import digest

    body = {key: value for key, value in receipt.items() if key != "receipt_digest"}
    if not isinstance(recorded_digest, str) or recorded_digest != digest(body):
        raise CampaignRecordError("benchmark receipt digest does not match its sealed contents")
    for field in ("source_revision", "runtime_variant"):
        if not isinstance(receipt.get(field), str) or not receipt[field]:
            raise CampaignRecordError(f"benchmark receipt field {field} is missing")
    if not isinstance(receipt.get("passed"), bool):
        raise CampaignRecordError("receipt field passed must be boolean")
    return receipt


def _bounded_param(key: str, value: object) -> str:
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True, separators=(",", ":"))
    if not text or len(text) > _MAX_PARAM_CHARS:
        raise CampaignRecordError(f"campaign identity {key} exceeds its bounded parameter size")
    return text


def _finite(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    return numeric if math.isfinite(numeric) else None


def _campaign_tags(receipt: Mapping[str, Any], *, purpose: str) -> dict[str, str]:
    semantic_gate = receipt.get("live_semantic_gate", receipt.get("semantic_gate", "unknown"))
    return {
        "fleet.campaign.purpose": purpose,
        "fleet.campaign.schema": str(receipt["schema"]),
        "fleet.campaign.receipt_sha256": str(receipt["receipt_digest"]),
        "fleet.campaign.evidence_lane": str(receipt.get("scope", "scripted-lifecycle-only")),
        "fleet.campaign.semantic_gate": str(semantic_gate),
        # Scripted echo/keyword receipts are lifecycle smoke evidence; they can
        # never satisfy a live semantic-quality gate, so this is fail-closed.
        "fleet.campaign.promotion_eligible": "false",
        "fleet.campaign.source_revision": str(receipt["source_revision"]),
        "fleet.campaign.source_dirty": str(bool(receipt["source_dirty"])).lower(),
    }


def _runtime_metrics(receipt: Mapping[str, Any]) -> tuple[dict[str, float], tuple[str, ...]]:
    """Project full-run numeric outcomes; failures stay in the denominators."""
    samples = receipt.get("samples")
    if not isinstance(samples, list) or not samples:
        raise CampaignRecordError("benchmark receipt has no recorded samples")
    metrics = {
        f"{_METRIC_PREFIX}samples": float(len(samples)),
        f"{_METRIC_PREFIX}passed": 1.0 if receipt["passed"] else 0.0,
    }
    failed = sum(
        1
        for sample in samples
        if isinstance(sample, Mapping)
        and (not all(sample.get("scores", {}).values()) or not all(sample.get("semantic_scores", {}).values()))
    )
    metrics[f"{_METRIC_PREFIX}failed_samples"] = float(failed)
    latency = receipt.get("latency_seconds")
    latency_names: list[str] = []
    if isinstance(latency, Mapping):
        for bound in ("p50", "p95"):
            value = _finite(latency.get(bound))
            if value is not None:
                metrics[f"{_METRIC_PREFIX}latency.{bound}_seconds"] = value
                latency_names.append(f"latency.{bound}_seconds")
    scorer_ids = {str(name) for name in receipt.get("scorer_ids", [])}
    scorer_ids.update(str(name) for name in receipt.get("semantic_scorer_ids", []))
    for scorer in sorted(scorer_ids):
        metrics[f"{_METRIC_PREFIX}score_passes.{scorer}"] = float(
            sum(1 for sample in samples if isinstance(sample, Mapping) and sample.get("scores", {}).get(scorer))
            + sum(
                1 for sample in samples if isinstance(sample, Mapping) and sample.get("semantic_scores", {}).get(scorer)
            )
        )
    unknown = ("latency.p50_seconds", "latency.p95_seconds") if len(latency_names) != 2 else ()
    return metrics, unknown


def _adapter_metrics(receipt: Mapping[str, Any]) -> tuple[dict[str, float], tuple[str, ...]]:
    """Project per-variant and gate outcomes for the adapter comparison lane."""
    metrics = {
        f"{_METRIC_PREFIX}passed": 1.0 if receipt["passed"] else 0.0,
    }
    gates = receipt.get("gates")
    if isinstance(gates, Mapping):
        for name in sorted(gates):
            value = gates[name]
            if isinstance(value, bool):
                metrics[f"{_METRIC_PREFIX}gate.{name}"] = 1.0 if value else 0.0
    summary = receipt.get("summary")
    variant_names: list[str] = []
    if isinstance(summary, Mapping):
        for variant in sorted(summary):
            entry = summary[variant]
            if not isinstance(entry, Mapping):
                continue
            variant_names.append(str(variant))
            correct = _finite(entry.get("correct"))
            attempts = _finite(entry.get("provider_attempts"))
            if correct is not None:
                metrics[f"{_METRIC_PREFIX}variant.{variant}.correct"] = correct
            if attempts is not None:
                metrics[f"{_METRIC_PREFIX}variant.{variant}.provider_attempts"] = attempts
            latency = entry.get("latency_seconds")
            if isinstance(latency, Mapping):
                for bound in ("p50", "p95"):
                    value = _finite(latency.get(bound))
                    if value is not None:
                        metrics[f"{_METRIC_PREFIX}variant.{variant}.latency.{bound}_seconds"] = value
    unknown = ("summary",) if not variant_names else ()
    return metrics, unknown


def _campaign_params(receipt: Mapping[str, Any]) -> dict[str, str]:
    params: dict[str, str] = {}
    for key in ("runtime_variant", "dataset_digest", "scorer_digest", "repetitions"):
        if key in receipt:
            params[f"fleet.campaign.{key}"] = _bounded_param(key, receipt[key])
    identities = receipt.get("identities")
    if isinstance(identities, Mapping):
        for key in sorted(identities):
            params[f"fleet.campaign.identity.{key}"] = _bounded_param(key, identities[key])
    for key in ("dspy_version", "implementation_digest", "scope"):
        if key in receipt:
            params[f"fleet.campaign.{key}"] = _bounded_param(key, receipt[key])
    return params


def record(args: argparse.Namespace) -> dict[str, Any]:
    """Create one MLflow tracking run for a sealed benchmark campaign receipt."""
    _require_live()
    receipt = load_receipt(args.receipt)
    purpose = args.purpose.strip()
    if not purpose or len(purpose) > 128 or any(character in purpose for character in "/\\ \t"):
        raise CampaignRecordError("--purpose must be a bounded, path-free identifier")
    import mlflow
    from mlflow.tracking.client import MlflowClient

    mlflow.set_tracking_uri(args.mlflow_url)
    client = MlflowClient()
    tags = _campaign_tags(receipt, purpose=purpose)
    params = _campaign_params(receipt)
    if receipt["schema"] == RUNTIME_SCHEMA:
        metrics, unknown = _runtime_metrics(receipt)
    else:
        metrics, unknown = _adapter_metrics(receipt)
    tags["fleet.campaign.metrics_unknown"] = ",".join(unknown) if unknown else "none"
    run_name = f"fleet-campaign-{purpose}-{str(receipt['receipt_digest'])[:12]}"

    run = client.create_run(experiment_id=args.experiment_id, tags=tags, run_name=run_name)
    run_id = run.info.run_id
    try:
        for key, value in params.items():
            client.log_param(run_id, key, value)
        for key, value in sorted(metrics.items()):
            client.log_metric(run_id, key, value)
        canonical = (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode("utf-8")
        with tempfile.TemporaryDirectory(prefix="fleet-campaign-mlflow-") as directory:
            artifact = Path(directory) / "benchmark-receipt.json"
            artifact.write_bytes(canonical)
            client.log_artifact(run_id, str(artifact), artifact_path=args.artifact_path)
    except BaseException:
        with suppress(Exception):
            client.set_terminated(run_id, status="FAILED")
        raise
    client.set_terminated(run_id, status="FINISHED")
    return {
        "schema": RECEIPT_SCHEMA,
        "run_id": run_id,
        "run_name": run_name,
        "experiment_id": args.experiment_id,
        "artifact": f"{args.artifact_path}/benchmark-receipt.json",
        "receipt_sha256": str(receipt["receipt_digest"]),
        "purpose": purpose,
        "promotion_eligible": False,
        "params_written": len(params),
        "metrics_written": len(metrics),
        "metrics_unknown": list(unknown),
    }


def _write_once(path: Path, payload: Mapping[str, object]) -> None:
    """Write one operator result receipt without replacing a sealed result."""
    import os

    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise CampaignRecordError("campaign result receipt already exists") from exc
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mlflow-url", required=True)
    parser.add_argument("--experiment-id", required=True, help="Explicit MLflow experiment ID for the campaign run")
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--purpose", required=True, help="Explicit bounded campaign purpose, e.g. runtime-baseline")
    parser.add_argument("--artifact-path", default=DEFAULT_ARTIFACT_PATH)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    load_dotenv(_REPO_ROOT / ".env", override=False)
    args = build_parser().parse_args(argv)
    try:
        result = record(args)
    except Exception as exc:
        result = {
            "schema": RECEIPT_SCHEMA,
            "generated_at": datetime.now(UTC).isoformat(),
            "status": "failed",
            "error_category": type(exc).__name__,
        }
        exit_code = 1
    else:
        result = {"generated_at": datetime.now(UTC).isoformat(), "status": "ok", **result}
        exit_code = 0
    _write_once(args.output, result)
    print(json.dumps(result, indent=2, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
