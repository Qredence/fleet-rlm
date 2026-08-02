"""Manage the UC-backed Fleet RLM evaluation dataset (v2: inputs plus expectations).

The v2 dataset pairs every ``inputs.query`` with explicit expectations so offline
evaluation, judge alignment, and GEPA Anything optimization consume the same
records. All commands touch MLflow/Databricks and therefore require
``FLEET_LIVE=1``; receipts contain bounded aggregates only.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.benchmarks.run_rlm_latency import QUALITY_RECORDS

RECEIPT_SCHEMA = "fleet.eval-dataset/v1"
DATASET_NAME = "fleet-rlm-quality-v2"
DEFAULT_MLFLOW_URL = "databricks"
DEFAULT_EVAL_TAG = "fleet_eval_candidate"
_LIVE_VALUES = frozenset({"1", "true", "yes"})


def _dataset_name_default() -> str:
    """UC-compliant default dataset table (catalog.schema.fleet_rlm_quality_v2)."""
    catalog = os.environ.get("FLEET_MLFLOW_TRACE_CATALOG", "ml")
    schema = os.environ.get("FLEET_MLFLOW_TRACE_SCHEMA", "genai")
    return f"{catalog}.{schema}.fleet_rlm_quality_v2"


class DatasetError(RuntimeError):
    """A dataset precondition or MLflow contract failed."""


def _require_live() -> None:
    """
    Enforce the explicit live opt-in for credentialed MLflow access.

    Raises:
        DatasetError: If ``FLEET_LIVE`` is not enabled.
    """
    if os.environ.get("FLEET_LIVE", "").lower() not in _LIVE_VALUES:
        raise DatasetError("FLEET_LIVE=1 is required for dataset operations")


def dataset_examples(
    records: Sequence[Mapping[str, Any]],
    *,
    val_fraction: float = 0.0,
    seed: int = 0,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Split expectation-bearing dataset records into optimizer-ready train/val examples.

    Parameters:
        records (Sequence[Mapping[str, Any]]): Dataset records shaped
            ``{"inputs": {"query": ...}, "expectations": {...}}``.
        val_fraction (float): Fraction of records reserved for validation, in [0, 1).
        seed (int): Deterministic shuffle seed so optimizer runs are comparable.

    Returns:
        tuple[list[dict[str, Any]], list[dict[str, Any]]]: ``(train, val)`` example
            lists shaped ``{"query": str, "expectations": dict}``.

    Raises:
        DatasetError: If ``val_fraction`` is outside [0, 1).
    """
    if not 0.0 <= val_fraction < 1.0:
        raise DatasetError("val_fraction must be in [0, 1)")
    examples = [
        {
            "query": str(record["inputs"]["query"]),
            "expectations": dict(record.get("expectations") or {}),
        }
        for record in records
    ]
    shuffled = list(examples)
    random.Random(seed).shuffle(shuffled)
    val_count = min(len(shuffled), round(len(shuffled) * val_fraction))
    return shuffled[val_count:], shuffled[:val_count]


def _configure_mlflow(args: argparse.Namespace) -> Any:
    import mlflow

    mlflow.set_tracking_uri(args.mlflow_url)
    if args.experiment_id:
        mlflow.set_experiment(experiment_id=args.experiment_id)
    return mlflow


def _get_dataset(args: argparse.Namespace) -> Any:
    from mlflow.genai import datasets

    return datasets.get_dataset(name=args.dataset_name)


def _existing_source_trace_ids(dataset: Any) -> set[str]:
    records = dataset.to_df().to_dict("records")
    trace_ids: set[str] = set()
    for record in records:
        expectations = record.get("expectations") or {}
        trace_id = expectations.get("source_trace_id") if isinstance(expectations, Mapping) else None
        if isinstance(trace_id, str) and trace_id:
            trace_ids.add(trace_id)
    return trace_ids


def show(args: argparse.Namespace) -> dict[str, Any]:
    """
    Summarize the managed dataset without mutating it.

    Parameters:
        args (argparse.Namespace): MLflow connection options.

    Returns:
        dict[str, Any]: Dataset id, name, record count, and source breakdown.
    """
    _require_live()
    _configure_mlflow(args)
    dataset = _get_dataset(args)
    records = dataset.to_df().to_dict("records")
    sources = [
        "trace"
        if isinstance(record.get("expectations"), Mapping) and record["expectations"].get("source_trace_id")
        else "static"
        for record in records
    ]
    return {
        "command": "show",
        "dataset_id": dataset.dataset_id,
        "dataset_name": dataset.name,
        "records": len(records),
        "sources": {source: sources.count(source) for source in sorted(set(sources))},
    }


def _static_records(args: argparse.Namespace) -> list[dict[str, Any]]:
    if args.records_json is None:
        return list(QUALITY_RECORDS)
    try:
        records = json.loads(args.records_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DatasetError(f"could not read static records: {args.records_json}") from exc
    if not isinstance(records, list) or not all("inputs" in record for record in records):
        raise DatasetError("records-json must be a list of {'inputs': {...}, 'expectations': {...}} records")
    return records


def ingest_static(args: argparse.Namespace) -> dict[str, Any]:
    """
    Create the dataset when missing and merge the static expectation records.

    Parameters:
        args (argparse.Namespace): MLflow connection options plus --force and
            optional --records-json overriding the built-in static records.

    Returns:
        dict[str, Any]: Merge receipt with created/merged/total counts.
    """
    _require_live()
    _configure_mlflow(args)
    from mlflow.genai import datasets

    existing = [item for item in datasets.search_datasets([args.experiment_id]) if item.name == args.dataset_name]
    created = False
    if existing:
        dataset = existing[0]
    else:
        dataset = datasets.create_dataset(name=args.dataset_name, experiment_id=args.experiment_id)
        created = True
    current = len(dataset.to_df())
    merged = 0
    records = _static_records(args)
    if current == 0 or args.force:
        dataset.merge_records(records)
        merged = len(records)
    return {
        "command": "ingest-static",
        "dataset_id": dataset.dataset_id,
        "dataset_name": dataset.name,
        "created": created,
        "merged": merged,
        "records": len(dataset.to_df()),
    }


def ingest_traces(args: argparse.Namespace) -> dict[str, Any]:
    """
    Merge tagged production traces into the dataset with explicit expectations.

    Only traces whose id appears in the --expectations-json mapping are merged;
    records without expectations cannot train judges or optimizers. Trace ids
    already present in the dataset are skipped so the command is idempotent.

    Parameters:
        args (argparse.Namespace): MLflow connection options plus --tag and
            --expectations-json.

    Returns:
        dict[str, Any]: Merge receipt with merged/skipped/total counts.

    Raises:
        DatasetError: If --expectations-json is missing or unreadable.
    """
    _require_live()
    if args.expectations_json is None:
        raise DatasetError("ingest-traces requires --expectations-json mapping trace_id to expectations")
    try:
        mapping = json.loads(args.expectations_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DatasetError(f"could not read expectations mapping: {args.expectations_json}") from exc
    if not isinstance(mapping, dict) or not all(isinstance(value, dict) for value in mapping.values()):
        raise DatasetError("expectations mapping must be a JSON object of trace_id -> expectations object")

    mlflow = _configure_mlflow(args)
    traces = mlflow.search_traces(
        locations=[args.experiment_id],
        filter_string=f"tag.{args.tag} = 'true'",
        return_type="pandas",
    )
    frame = traces
    if "inputs" not in frame.columns and "request" in frame.columns:
        frame = frame.rename(columns={"request": "inputs"})
    if "outputs" not in frame.columns and "response" in frame.columns:
        frame = frame.rename(columns={"response": "outputs"})

    dataset = _get_dataset(args)
    known_trace_ids = _existing_source_trace_ids(dataset)
    records: list[dict[str, Any]] = []
    skipped = 0
    for row in frame.to_dict("records"):
        trace_id = str(row.get("trace_id") or "")
        expectations = mapping.get(trace_id)
        if not trace_id or expectations is None:
            skipped += 1
            continue
        if trace_id in known_trace_ids:
            skipped += 1
            continue
        inputs = row.get("inputs")
        query = ""
        if isinstance(inputs, Mapping):
            query = str(inputs.get("query") or inputs.get("request") or "")
        if not query:
            skipped += 1
            continue
        record_expectations = {**expectations, "source_trace_id": trace_id}
        records.append(
            {
                "inputs": {"query": query},
                "expectations": record_expectations,
            }
        )
        known_trace_ids.add(trace_id)

    if records:
        dataset.merge_records(records)
    return {
        "command": "ingest-traces",
        "dataset_id": dataset.dataset_id,
        "dataset_name": dataset.name,
        "tag": args.tag,
        "merged": len(records),
        "skipped": skipped,
        "records": len(dataset.to_df()),
    }


def export_records(args: argparse.Namespace) -> dict[str, Any]:
    """
    Export the managed dataset to a records JSON file consumable by --dataset-json.

    Parameters:
        args (argparse.Namespace): MLflow connection options.

    Returns:
        dict[str, Any]: Export receipt with the record count and path.
    """
    _require_live()
    _configure_mlflow(args)
    dataset = _get_dataset(args)
    records = dataset.to_df().to_dict("records")
    args.export_out.parent.mkdir(parents=True, exist_ok=True)
    args.export_out.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
    return {
        "command": "export",
        "dataset_id": dataset.dataset_id,
        "dataset_name": dataset.name,
        "records": len(records),
        "export_out": str(args.export_out),
    }


def build_parser() -> argparse.ArgumentParser:
    """
    Create the command-line argument parser for dataset management workflows.

    Returns:
        argparse.ArgumentParser: Parser configured with command, MLflow
        connection, and ingestion options.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("show", "ingest-static", "ingest-traces", "export"))
    parser.add_argument("--mlflow-url", default=DEFAULT_MLFLOW_URL)
    parser.add_argument("--experiment-id", default="")
    parser.add_argument("--dataset-name", default=_dataset_name_default(), help=DATASET_NAME)
    parser.add_argument("--records-json", type=Path, default=None, help="Override the built-in static records")
    parser.add_argument("--tag", default=DEFAULT_EVAL_TAG, help="Trace tag selecting eval candidates")
    parser.add_argument("--expectations-json", type=Path, default=None)
    parser.add_argument("--force", action="store_true", help="Re-merge static records into a non-empty dataset")
    parser.add_argument("--export-out", type=Path, default=None, help="Export destination records JSON")
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """
    Run the selected dataset command and write its result as a JSON receipt.

    Parameters:
        argv (Sequence[str] | None): Optional command-line arguments; uses the
            process arguments when omitted.

    Returns:
        int: `0` when the command succeeds, `1` when it fails.
    """
    load_dotenv(_REPO_ROOT / ".env", override=False)
    args = build_parser().parse_args(argv)
    try:
        if args.command == "show":
            receipt = show(args)
        elif args.command == "export":
            if args.export_out is None:
                raise DatasetError("export requires --export-out")
            receipt = export_records(args)
        elif args.command == "ingest-static":
            receipt = ingest_static(args)
        else:
            receipt = ingest_traces(args)
    except Exception as exc:
        receipt = {
            "schema": RECEIPT_SCHEMA,
            "generated_at": datetime.now(UTC).isoformat(),
            "command": args.command,
            "status": "failed",
            "error_category": type(exc).__name__,
        }
        exit_code = 1
    else:
        receipt = {"schema": RECEIPT_SCHEMA, "generated_at": datetime.now(UTC).isoformat(), "status": "ok", **receipt}
        exit_code = 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
