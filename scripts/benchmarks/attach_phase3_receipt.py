"""Attach a bounded Daytona Phase 3 feasibility receipt to an MLflow run.

The native feasibility lane deliberately writes its receipt locally first.  This
operator-only command is the explicit bridge into a benchmark/campaign run: it
validates and projects the non-secret capability and timing fields, uploads one
canonical JSON artifact, and records only bounded tags/metrics.  It never
changes Fleet runtime policy and never creates a warm pool or a Turn.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

RECEIPT_SCHEMA = "fleet.phase3-mlflow-attachment/v1"
NATIVE_RECEIPT_SCHEMA = "fleet.phase3-daytona-native-feasibility/v1"
NATIVE_RECEIPT_SCHEMA_V2 = "fleet.phase3-daytona-native-feasibility/v2"
DEFAULT_MLFLOW_URL = "databricks"
DEFAULT_ARTIFACT_PATH = "phase3/daytona-native"
_LIVE_VALUES = frozenset({"1", "true", "yes"})
_MAX_RECEIPT_BYTES = 256 * 1024
_MAX_TEXT_CHARS = 256
_ARTIFACT_PATH = re.compile(r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")
_REQUIRED_ASSERTIONS = frozenset(
    {
        "native_nested_root_child_resume",
        "native_typed_submit",
        "native_preview_transport",
        "native_context_is_explicit",
        "broker_typed_submit",
        "native_broker_output_contract_parity",
        "native_host_callback_authority_loss_blocks_publication",
        "detached_process_probe_recorded",
        "uncertain_native_process_is_quarantined",
        "different_sessions_have_distinct_sandboxes",
        "different_sessions_execute_concurrently",
        "all_disposable_sandboxes_absent",
    }
)
_REQUIRED_CONTAINMENT = frozenset(
    {"detached_process_contained", "quarantined_when_uncertain", "all_disposable_sandboxes_absent"}
)
_REQUIRED_V2_CONTAINMENT = frozenset(
    {
        "context_deleted",
        "sandbox_fenced",
        "sandbox_absent_confirmed",
        "replacement_generation",
        "volume_scope_preserved",
        "quarantined_when_uncertain",
        "all_disposable_sandboxes_absent",
    }
)
_REQUIRED_V2_CONTINUITY = frozenset(
    {
        "same_process_fresh_context",
        "process_restart",
        "sandbox_stop_start",
        "full_replacement",
        "durable_file_readable",
        "python_only_state_absent",
        "checksum_preserved",
    }
)
_ALLOWED_TIMING_NAMES = frozenset(
    {
        "native_sandbox_acquisition_ms",
        "nested_child_sandbox_acquisition_ms",
        "native_context_creation_ms",
        "native_bootstrap_ms",
        "native_first_action_ms",
        "native_host_tool_round_trip_ms",
        "native_subsequent_action_ms",
        "broker_sandbox_acquisition_ms",
        "broker_first_action_ms",
        "broker_subsequent_action_ms",
        "cancel_sandbox_acquisition_ms",
        "native_host_callback_cancellation_ms",
        "detached_sandbox_acquisition_ms",
        "isolation_a_sandbox_acquisition_ms",
        "isolation_b_sandbox_acquisition_ms",
        "different_session_concurrency_ms",
        "cleanup_ms",
    }
)


class Phase3AttachmentError(RuntimeError):
    """A Phase 3 receipt or MLflow attachment contract failed."""


def _require_live() -> None:
    """Require explicit operator opt-in before contacting MLflow."""
    if os.environ.get("FLEET_LIVE", "").strip().lower() not in _LIVE_VALUES:
        raise Phase3AttachmentError("FLEET_LIVE=1 is required for MLflow receipt attachment")


def _bounded_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise Phase3AttachmentError(f"receipt field {field} must be a non-empty string")
    text = value.strip()
    if len(text) > _MAX_TEXT_CHARS:
        raise Phase3AttachmentError(f"receipt field {field} exceeds its bound")
    return text


def _bool_map(value: object, *, field: str) -> dict[str, bool]:
    if not isinstance(value, Mapping):
        raise Phase3AttachmentError(f"receipt field {field} must be an object")
    result: dict[str, bool] = {}
    for key, item in value.items():
        name = _bounded_text(str(key), field=f"{field}.key")
        if not isinstance(item, bool):
            raise Phase3AttachmentError(f"receipt field {field}.{name} must be boolean")
        result[name] = item
    return dict(sorted(result.items()))


def _required_bool(value: object, *, field: str) -> bool:
    if not isinstance(value, bool):
        raise Phase3AttachmentError(f"receipt field {field} must be boolean")
    return value


def _timing_map(value: object) -> dict[str, float]:
    if not isinstance(value, Mapping):
        raise Phase3AttachmentError("receipt field timings_ms must be an object")
    result: dict[str, float] = {}
    for key, item in value.items():
        name = _bounded_text(str(key), field="timings_ms.key")
        if name not in _ALLOWED_TIMING_NAMES:
            raise Phase3AttachmentError("receipt timing name is not an approved Phase 3 metric")
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise Phase3AttachmentError(f"receipt timing {name} must be numeric")
        numeric = float(item)
        if not math.isfinite(numeric) or numeric < 0 or numeric > 1_000_000_000:
            raise Phase3AttachmentError(f"receipt timing {name} is outside its bound")
        result[name] = numeric
    return dict(sorted(result.items()))


def _exact_bool_map(value: object, *, field: str, required: frozenset[str]) -> dict[str, bool]:
    result = _bool_map(value, field=field)
    if set(result) != required:
        raise Phase3AttachmentError(f"receipt field {field} must contain the exact Phase 3 assertion set")
    return result


def load_receipt(path: Path) -> dict[str, Any]:
    """Load and project one native feasibility receipt without content fields."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise Phase3AttachmentError("Phase 3 receipt could not be read") from exc
    if len(raw) > _MAX_RECEIPT_BYTES:
        raise Phase3AttachmentError("Phase 3 receipt exceeds its size bound")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise Phase3AttachmentError("Phase 3 receipt is not valid JSON") from exc
    if not isinstance(payload, Mapping) or payload.get("schema") not in {
        NATIVE_RECEIPT_SCHEMA,
        NATIVE_RECEIPT_SCHEMA_V2,
    }:
        raise Phase3AttachmentError("receipt schema is not the Phase 3 native feasibility schema")
    schema = str(payload["schema"])

    versions = payload.get("versions")
    transport = payload.get("transport")
    assertions = payload.get("assertions")
    containment = payload.get("containment")
    go_no_go = payload.get("go_no_go")
    if not isinstance(versions, Mapping) or not isinstance(transport, Mapping) or not isinstance(go_no_go, Mapping):
        raise Phase3AttachmentError("receipt is missing bounded capability sections")
    projected_versions = {
        key: _bounded_text(versions.get(key), field=f"versions.{key}") for key in ("python", "dspy", "daytona")
    }
    if any(not _VERSION.fullmatch(value) for value in projected_versions.values()):
        raise Phase3AttachmentError("receipt versions must use bounded version identifiers")
    projected_transport: dict[str, object] = {
        "native_context": _required_bool(transport.get("native_context"), field="transport.native_context"),
        "composition_bridge": _required_bool(transport.get("composition_bridge"), field="transport.composition_bridge"),
        "loopback_host_assumption": _required_bool(
            transport.get("loopback_host_assumption"), field="transport.loopback_host_assumption"
        ),
        "host_callback": _bounded_text(transport.get("host_callback"), field="transport.host_callback"),
    }
    projected_go_no_go = {
        "native_production": _required_bool(go_no_go.get("native_production"), field="go_no_go.native_production"),
        "retained_broker_compatibility": _required_bool(
            go_no_go.get("retained_broker_compatibility"), field="go_no_go.retained_broker_compatibility"
        ),
        "reason": _bounded_text(go_no_go.get("reason"), field="go_no_go.reason"),
    }
    if projected_transport["host_callback"] != "daytona_preview_http_poll":
        raise Phase3AttachmentError("receipt host callback transport is not the approved Daytona transport")
    allowed_reasons = (
        {"detached_process_containment_uncertified"}
        if schema == NATIVE_RECEIPT_SCHEMA
        else {"whole_sandbox_fencing_certified"}
    )
    if projected_go_no_go["reason"] not in allowed_reasons:
        raise Phase3AttachmentError("receipt go/no-go reason is not an approved bounded reason")
    if not isinstance(payload.get("passed"), bool):
        raise Phase3AttachmentError("receipt field passed must be boolean")
    projected_containment = (
        _exact_bool_map(containment, field="containment", required=_REQUIRED_CONTAINMENT)
        if schema == NATIVE_RECEIPT_SCHEMA
        else {}
    )
    projected = {
        "schema": schema,
        "versions": projected_versions,
        "transport": projected_transport,
        "timings_ms": _timing_map(payload.get("timings_ms")),
        "assertions": _exact_bool_map(assertions, field="assertions", required=_REQUIRED_ASSERTIONS),
        "containment": projected_containment,
        "go_no_go": projected_go_no_go,
        "passed": payload["passed"],
    }
    if not projected["passed"] or not all(projected["assertions"].values()):
        raise Phase3AttachmentError("only a complete passing Phase 3 feasibility receipt may be attached")
    if not projected_go_no_go["retained_broker_compatibility"]:
        raise Phase3AttachmentError("Phase 3 receipt must retain the broker compatibility path")
    if (
        schema == NATIVE_RECEIPT_SCHEMA
        and projected_go_no_go["native_production"]
        and not projected["containment"].get("detached_process_contained", False)
    ):
        raise Phase3AttachmentError("native_production cannot be true without detached-process containment")
    if schema == NATIVE_RECEIPT_SCHEMA and (
        not projected["containment"]["detached_process_contained"]
        and not projected["containment"]["quarantined_when_uncertain"]
    ):
        raise Phase3AttachmentError("an uncontained detached process requires confirmed quarantine")
    if schema == NATIVE_RECEIPT_SCHEMA_V2:
        v2_containment = payload.get("containment")
        continuity = payload.get("continuity")
        if not isinstance(v2_containment, Mapping) or not isinstance(continuity, Mapping):
            raise Phase3AttachmentError("v2 receipt requires containment and continuity evidence")
        strategy = _bounded_text(v2_containment.get("strategy"), field="containment.strategy")
        probe = _bounded_text(v2_containment.get("detached_process_probe"), field="containment.detached_process_probe")
        if strategy != "sandbox_delete" or probe not in {"contained", "blocked_after_fence"}:
            raise Phase3AttachmentError("v2 receipt does not identify confirmed sandbox fencing")
        if not all(
            _required_bool(v2_containment.get(name), field=f"containment.{name}") for name in _REQUIRED_V2_CONTAINMENT
        ):
            raise Phase3AttachmentError("v2 containment evidence is incomplete")
        projected["containment"] = {
            "strategy": strategy,
            "detached_process_probe": probe,
            **{
                name: _required_bool(v2_containment.get(name), field=f"containment.{name}")
                for name in sorted(_REQUIRED_V2_CONTAINMENT)
            },
        }
        projected["continuity"] = {
            name: _required_bool(continuity.get(name), field=f"continuity.{name}")
            for name in sorted(_REQUIRED_V2_CONTINUITY)
        }
        fenced_values = (
            value
            for key, value in projected["containment"].items()
            if key not in {"strategy", "detached_process_probe"}
        )
        if not all(projected["continuity"].values()) or not all(value is True for value in fenced_values):
            raise Phase3AttachmentError("v2 receipt requires complete fencing and continuity proof")
        if projected_go_no_go["native_production"]:
            raise Phase3AttachmentError("v2 capability receipts cannot promote native production")
    return projected


def _write_once(path: Path, payload: Mapping[str, object]) -> None:
    """Write one operator receipt without replacing a sealed result."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise Phase3AttachmentError("attachment receipt already exists") from exc
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _artifact_path(value: str) -> str:
    if not _ARTIFACT_PATH.fullmatch(value) or any(part in {".", ".."} for part in value.split("/")):
        raise Phase3AttachmentError("artifact path must contain only bounded path segments")
    return value


def attach(args: argparse.Namespace) -> dict[str, Any]:
    """Validate, upload and tag one Phase 3 receipt on an existing MLflow run."""
    _require_live()
    run_id = args.run_id.strip()
    if not run_id:
        raise Phase3AttachmentError("--run-id is required")
    receipt = load_receipt(args.receipt)
    canonical = (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode("utf-8")
    digest = hashlib.sha256(canonical).hexdigest()
    artifact_path = _artifact_path(args.artifact_path)

    import mlflow
    from mlflow.tracking.client import MlflowClient

    mlflow.set_tracking_uri(args.mlflow_url)
    client = MlflowClient()
    run = client.get_run(run_id)
    if getattr(getattr(run, "info", None), "run_id", run_id) != run_id:
        raise Phase3AttachmentError("MLflow returned a different run identity")
    if str(getattr(getattr(run, "info", None), "status", "")).upper() == "DELETED":
        raise Phase3AttachmentError("cannot attach a receipt to a deleted MLflow run")
    existing_tags = getattr(getattr(run, "data", None), "tags", {}) or {}
    existing_digest = existing_tags.get("fleet.phase3.receipt_sha256")
    if existing_digest and existing_digest != digest:
        raise Phase3AttachmentError("MLflow run already has a different sealed Phase 3 receipt")
    timestamp_ms = int(digest[:12], 16) % 1_000_000_000_000
    tags = {
        "fleet.phase3.receipt_schema": str(receipt["schema"]),
        "fleet.phase3.receipt_sha256": digest,
        "fleet.phase3.passed": str(receipt["passed"]).lower(),
        "fleet.phase3.native_production": str(receipt["go_no_go"]["native_production"]).lower(),
        "fleet.phase3.retained_broker_compatibility": str(receipt["go_no_go"]["retained_broker_compatibility"]).lower(),
        "fleet.phase3.detached_process_contained": str(
            receipt["containment"].get("detached_process_contained", False)
        ).lower(),
        "fleet.phase3.cleanup_confirmed": str(
            receipt["containment"].get("all_disposable_sandboxes_absent", False)
        ).lower(),
    }
    if receipt["schema"] == NATIVE_RECEIPT_SCHEMA_V2:
        tags.update(
            {
                "fleet.phase3.containment_strategy": str(receipt["containment"]["strategy"]),
                "fleet.phase3.sandbox_fence_confirmed": str(receipt["containment"]["sandbox_fenced"]).lower(),
                "fleet.phase3.continuity_verified": str(all(receipt["continuity"].values())).lower(),
            }
        )
    with TemporaryDirectory(prefix="fleet-phase3-mlflow-") as directory:
        artifact = Path(directory) / "daytona-native-feasibility.json"
        artifact.write_bytes(canonical)
        client.log_artifact(run_id, str(artifact), artifact_path=artifact_path)
    # Seal only after the canonical artifact exists.  Replays use the digest
    # and deterministic metric timestamp/step, so a completed attachment is
    # a no-op and an interrupted one cannot silently replace another receipt.
    metrics = 0
    for name, value in receipt["timings_ms"].items():
        client.log_metric(run_id, f"fleet.phase3.{name}", float(value), timestamp=timestamp_ms, step=0)
        metrics += 1
    # Write all metadata except the digest first.  The digest is the commit
    # marker and is sealed last so a retry cannot leave a convincing digest
    # pointing at missing or incomplete metadata.
    for key, value in tags.items():
        if key == "fleet.phase3.receipt_sha256":
            continue
        client.set_tag(run_id, key, value)
    client.set_tag(run_id, "fleet.phase3.receipt_sha256", digest)
    verified = client.get_run(run_id)
    verified_tags = getattr(getattr(verified, "data", None), "tags", {}) or {}
    if any(verified_tags.get(key) != value for key, value in tags.items()):
        raise Phase3AttachmentError("MLflow receipt metadata could not be verified after sealing")

    return {
        "schema": RECEIPT_SCHEMA,
        "run_id": run_id,
        "artifact": f"{artifact_path}/daytona-native-feasibility.json",
        "receipt_sha256": digest,
        "tags_written": len(tags),
        "metrics_written": metrics,
        "already_attached": existing_digest == digest,
        "native_production": receipt["go_no_go"]["native_production"],
        "retained_broker_compatibility": receipt["go_no_go"]["retained_broker_compatibility"],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mlflow-url", default=DEFAULT_MLFLOW_URL)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--artifact-path", default=DEFAULT_ARTIFACT_PATH)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    load_dotenv(_REPO_ROOT / ".env", override=False)
    args = build_parser().parse_args(argv)
    try:
        receipt = attach(args)
    except Exception as exc:
        result = {
            "schema": RECEIPT_SCHEMA,
            "generated_at": datetime.now(UTC).isoformat(),
            "status": "failed",
            "error_category": type(exc).__name__,
        }
        exit_code = 1
    else:
        result = {
            "generated_at": datetime.now(UTC).isoformat(),
            "status": "ok",
            **receipt,
        }
        exit_code = 0
    _write_once(args.output, result)
    print(json.dumps(result, indent=2, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
