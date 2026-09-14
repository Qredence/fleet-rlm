"""Capture explicitly selected local traces and seal agent-reviewed expectations.

Read-only toward MLflow. Never execute trace content, import model answers as
ground truth, or emit raw content to stdout. Export remains non-promotable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.benchmarks.campaign import write_receipt_once

MAX_BYTES = 2 * 1024 * 1024
SNAPSHOT_SCHEMA = "fleet.phase6-curation-source/v1"
HUMAN_REVIEW_SCHEMA = "fleet.phase6-curation-review/v2"
_SAFE_REVIEWER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def digest(value: Any) -> str:
    """Return a canonical SHA-256 digest for a JSON-serializable value."""
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    """Read a size-bounded JSON object from ``path``."""
    with path.open("rb") as handle:
        raw = handle.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("curation input exceeds bound")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("curation input must be an object")
    return value


def capture_record(trace: Any) -> dict[str, Any]:
    """Extract a complete root request and opaque grouping provenance.

    Reject traces without a unique Fleet root, a complete sanitizer-approved
    request, or Session grouping metadata.
    """
    from fleet_rlm.rlm.result import sanitize_trace_text

    roots = [span for span in trace.data.spans if span.parent_id is None]
    if len(roots) != 1 or roots[0].name != "fleet_turn":
        raise ValueError("missing unique Fleet root")
    inputs = roots[0].inputs
    query = inputs.get("request") if isinstance(inputs, dict) else None
    if not isinstance(query, str) or not query.strip() or len(query) > 50_000 or query.rstrip().endswith(("...", "…")):
        raise ValueError("missing or oversized complete request")
    if sanitize_trace_text(query, max_len=50_001) != query or any(
        marker in query.lower()
        for marker in ("[redacted", "[content suppressed]", "[path]", "passphrase", "password", "api_key")
    ):
        raise ValueError("request needs manual redaction and re-review")
    metadata = trace.info.trace_metadata
    session = metadata.get("mlflow.trace.session")
    if not isinstance(session, str) or not session:
        raise ValueError("source Session grouping is missing")
    record = {
        "record_id": "trace-" + digest(trace.info.trace_id)[:24],
        "source_trace_sha256": digest(trace.info.trace_id),
        "source_query_sha256": digest(query),
        "query": query,
        "session_id": "session-" + digest(session)[:24],
    }
    project = metadata.get("fleet.project_id")
    if project:
        if not isinstance(project, str):
            raise ValueError("source project grouping is malformed")
        record["project_id"] = "project-" + digest(project)[:24]
    return record


def build_export(snapshot: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    """Match reviewed expectations to exact source queries and seal a split."""
    from fleet_rlm.optimization.dataset import EXPORT_SCHEMA, load_export, split_records

    body = {key: value for key, value in snapshot.items() if key != "snapshot_sha256"}
    if snapshot.get("schema") != SNAPSHOT_SCHEMA or snapshot.get("snapshot_sha256") != digest(body):
        raise ValueError("curation source seal is invalid")
    if review.get("schema") != "fleet.phase6-curation-review/v1" or review.get("reviewer") != "agent":
        raise ValueError("explicit agent review is required")
    if review.get("source_snapshot_sha256") != snapshot["snapshot_sha256"]:
        raise ValueError("review does not match source snapshot")
    sources = {row["record_id"]: row for row in snapshot["records"]}
    if len(sources) != len(snapshot["records"]):
        raise ValueError("duplicate source identities")
    records = []
    seen_queries = set()
    for approved in review["records"]:
        source = sources[approved["record_id"]]
        if source["query"].rstrip().endswith(("...", "…")):
            raise ValueError("truncated source cannot be reviewed as complete")
        if (
            approved["source_query_sha256"] != source["source_query_sha256"]
            or digest(source["query"]) != source["source_query_sha256"]
        ):
            raise ValueError("reviewed query identity changed")
        if source["source_query_sha256"] in seen_queries:
            raise ValueError("duplicate tasks cannot inflate the curated dataset")
        seen_queries.add(source["source_query_sha256"])
        if approved.get("review_status") != "agent_reviewed" or approved.get("self_contained") is not True:
            raise ValueError("task must be reviewed with all required context")
        expectations = approved["expectations"]
        criteria = expectations.get("criteria") if isinstance(expectations, dict) else None
        if (
            not isinstance(criteria, list)
            or not criteria
            or any(not isinstance(item, str) or not item.strip() for item in criteria)
        ):
            raise ValueError("reviewed criteria are required")
        family = approved["task_family"]
        if not isinstance(family, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", family):
            raise ValueError("reviewed task family must be a safe group")
        provenance = {
            "source": "local-mlflow",
            "reviewer": "agent",
            "review_status": "agent_reviewed",
            "redaction_version": "phase6-curation-v1",
            "source_trace_sha256": source["source_trace_sha256"],
            "source_query_sha256": source["source_query_sha256"],
            "session_id": source["session_id"],
            "task_family": family,
            "task_origin": approved["task_origin"],
        }
        if "project_id" in source:
            provenance["project_id"] = source["project_id"]
        records.append(
            {
                "record_id": source["record_id"],
                "task": {"query": source["query"]},
                "expectations": expectations,
                "output_contract": approved["output_contract"],
                "execution_requirements": approved["execution_requirements"],
                "provenance": provenance,
            }
        )
    export = {"schema": EXPORT_SCHEMA, "records": records}
    split = split_records(load_export(export), seed=42)
    export["curation"] = {
        "source_snapshot_sha256": snapshot["snapshot_sha256"],
        "review_sha256": digest(review),
        "expectation_origin": "agent-reviewed-draft",
        "promotion_eligible": False,
        "remaining_requirements": ["trusted_scorer_validation", "strict_evaluator_proof", "held_out_evaluation"],
    }
    export["split"] = split.public_manifest
    export["export_sha256"] = digest(export)
    return export


def build_human_aligned_export(snapshot: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    """Seal a complete human-aligned review without importing observed answers.

    The existing agent-reviewed export remains useful for drafting and pipeline
    tests, but it cannot authorize a quality gate.  This stricter path requires
    an opaque reviewer identity, a decision for every captured source record,
    and an explicit approved/corrected status for each expectation.
    """
    from fleet_rlm.optimization.dataset import EXPORT_SCHEMA, load_export, split_records

    body = {key: value for key, value in snapshot.items() if key != "snapshot_sha256"}
    if snapshot.get("schema") != SNAPSHOT_SCHEMA or snapshot.get("snapshot_sha256") != digest(body):
        raise ValueError("curation source seal is invalid")
    if review.get("schema") != HUMAN_REVIEW_SCHEMA or review.get("reviewer") != "human":
        raise ValueError("human alignment requires the v2 human review schema")
    reviewer_id = review.get("reviewer_id")
    if not isinstance(reviewer_id, str) or not _SAFE_REVIEWER_ID.fullmatch(reviewer_id):
        raise ValueError("human review requires a bounded opaque reviewer_id")
    if review.get("source_snapshot_sha256") != snapshot["snapshot_sha256"]:
        raise ValueError("review does not match source snapshot")
    source_rows = {row["record_id"]: row for row in snapshot["records"]}
    reviewed_rows = review.get("records")
    if not isinstance(reviewed_rows, list) or len(reviewed_rows) != len(source_rows):
        raise ValueError("human review must cover every captured source record")
    records: list[dict[str, Any]] = []
    seen_queries: set[str] = set()
    for approved in reviewed_rows:
        if not isinstance(approved, dict):
            raise ValueError("human review records must be objects")
        record_id = approved.get("record_id")
        source = source_rows.get(record_id)
        if source is None:
            raise ValueError("human review references an unknown source record")
        if record_id in {row["record_id"] for row in records}:
            raise ValueError("duplicate human review record")
        if approved.get("source_query_sha256") != source["source_query_sha256"]:
            raise ValueError("reviewed query identity changed")
        if source["source_query_sha256"] in seen_queries:
            raise ValueError("duplicate tasks cannot inflate the curated dataset")
        seen_queries.add(source["source_query_sha256"])
        if approved.get("review_status") not in {"human_approved", "human_corrected"}:
            raise ValueError("every task must be human-approved or human-corrected")
        if approved.get("self_contained") is not True:
            raise ValueError("human review must confirm every task is self-contained")
        expectations = approved.get("expectations")
        criteria = expectations.get("criteria") if isinstance(expectations, dict) else None
        if (
            not isinstance(criteria, list)
            or not criteria
            or any(not isinstance(item, str) or not item.strip() for item in criteria)
        ):
            raise ValueError("human-reviewed criteria are required")
        family = approved.get("task_family")
        if not isinstance(family, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", family):
            raise ValueError("human-reviewed task family must be a safe group")
        output_contract = approved.get("output_contract")
        execution_requirements = approved.get("execution_requirements")
        if not isinstance(output_contract, dict) or not isinstance(execution_requirements, dict):
            raise ValueError("human review must provide output and execution contracts")
        provenance = {
            "source": "local-mlflow",
            "reviewer": "human",
            "reviewer_id": reviewer_id,
            "review_status": approved["review_status"],
            "redaction_version": "phase6-curation-v2",
            "source_trace_sha256": source["source_trace_sha256"],
            "source_query_sha256": source["source_query_sha256"],
            "session_id": source["session_id"],
            "task_family": family,
            "task_origin": approved.get("task_origin", "local-mlflow"),
        }
        if "project_id" in source:
            provenance["project_id"] = source["project_id"]
        records.append(
            {
                "record_id": record_id,
                "task": {"query": source["query"]},
                "expectations": expectations,
                "output_contract": output_contract,
                "execution_requirements": execution_requirements,
                "provenance": provenance,
            }
        )
    if {row["record_id"] for row in records} != set(source_rows):
        raise ValueError("human review must cover every captured source record exactly once")
    export: dict[str, Any] = {"schema": EXPORT_SCHEMA, "records": records}
    split = split_records(load_export(export), seed=42)
    export["curation"] = {
        "source_snapshot_sha256": snapshot["snapshot_sha256"],
        "review_sha256": digest(review),
        "expectation_origin": "human-aligned",
        "reviewer_id": reviewer_id,
        "promotion_eligible": False,
        "remaining_requirements": ["trusted_scorer_validation", "strict_evaluator_proof", "held_out_evaluation"],
    }
    export["split"] = split.public_manifest
    export["export_sha256"] = digest(export)
    return export


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    capture = commands.add_parser("capture")
    capture.add_argument("--tracking-uri", default="http://127.0.0.1:5001")
    capture.add_argument("--trace-id", action="append", required=True)
    capture.add_argument("--base-snapshot", type=Path)
    capture.add_argument("--output", type=Path, required=True)
    export = commands.add_parser("export")
    export.add_argument("--snapshot", type=Path, required=True)
    export.add_argument("--review", type=Path, required=True)
    export.add_argument("--output", type=Path, required=True)
    human = commands.add_parser("export-human")
    human.add_argument("--snapshot", type=Path, required=True)
    human.add_argument("--review", type=Path, required=True)
    human.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "capture":
            parsed = urlsplit(args.tracking_uri)
            if (
                parsed.scheme != "http"
                or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
                or parsed.username
                or parsed.password
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("capture requires a credential-free local MLflow origin")
            if len(args.trace_id) > 100 or len(args.trace_id) != len(set(args.trace_id)):
                raise ValueError("select at most 100 distinct traces")
            from mlflow import MlflowClient

            client = MlflowClient(tracking_uri=args.tracking_uri)
            rows = []
            if args.base_snapshot:
                base = read_json(args.base_snapshot)
                unsigned = {key: value for key, value in base.items() if key != "snapshot_sha256"}
                if base.get("schema") != SNAPSHOT_SCHEMA or base.get("snapshot_sha256") != digest(unsigned):
                    raise ValueError("base snapshot seal is invalid")
                rows.extend(base["records"])
            rows.extend(capture_record(client.get_trace(trace_id, display=False)) for trace_id in args.trace_id)
            if len(rows) > 100 or len({row["record_id"] for row in rows}) != len(rows):
                raise ValueError("combined source inventory is duplicate or oversized")
            payload = {"schema": SNAPSHOT_SCHEMA, "records": rows}
            payload["snapshot_sha256"] = digest(payload)
        elif args.command == "export-human":
            payload = build_human_aligned_export(read_json(args.snapshot), read_json(args.review))
        else:
            payload = build_export(read_json(args.snapshot), read_json(args.review))
        write_receipt_once(args.output, payload, max_bytes=MAX_BYTES)
    except Exception:
        print("ERROR: curation failed; no raw trace or infrastructure details emitted", file=sys.stderr)
        return 1
    print(json.dumps({"schema": payload["schema"], "records": len(payload["records"]), "promotion_eligible": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
