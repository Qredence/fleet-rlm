"""Align Fleet evaluation judges with domain-expert feedback via MLflow MemAlign.

Pipeline: evaluate static records with the base judges, tag successful traces,
collect SME pass/fail labels in a labeling session whose schema names match the
judge names exactly, then distill guidelines with ``Judge.align`` and persist
the aligned judges back to the experiment registry. Aligned judges may score
lower — that is the alignment working, not a regression. All commands require
``FLEET_LIVE=1`` and write bounded JSON receipts.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.benchmarks.judges import (
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_JUDGE_MODEL,
    DEFAULT_REFLECTION_MODEL,
    JUDGE_NAMES,
    ensure_registered,
)
from scripts.benchmarks.run_rlm_latency import DATASET_NAME, DEFAULT_API_URL, QUALITY_RECORDS, run_turn

RECEIPT_SCHEMA = "fleet.judge-alignment/v1"
DEFAULT_MLFLOW_URL = "databricks"


def _experiment_name_default() -> str:
    return os.environ.get("FLEET_MLFLOW_EXPERIMENT_NAME", "fleet-rlm")


ALIGNMENT_DATASET_NAME = "fleet-rlm-judge-alignment-v1"
EVAL_TAG = "eval"
EVAL_TAG_VALUE = "complete"
_LIVE_VALUES = frozenset({"1", "true", "yes"})


def _alignment_dataset_name_default() -> str:
    """UC-compliant default labeling dataset (catalog.schema.fleet_rlm_judge_alignment_v1)."""
    catalog = os.environ.get("FLEET_MLFLOW_TRACE_CATALOG", "ml")
    schema = os.environ.get("FLEET_MLFLOW_TRACE_SCHEMA", "genai")
    return f"{catalog}.{schema}.fleet_rlm_judge_alignment_v1"


class AlignmentError(RuntimeError):
    """An alignment precondition or MLflow contract failed."""


def _sampling_details(scorer: Any) -> tuple[float | None, str | None]:
    """Read the scorer's current sampling configuration without changing it."""
    sampling = getattr(scorer, "sampling_config", None)
    sample_rate = getattr(sampling, "sample_rate", None)
    filter_string = getattr(sampling, "filter_string", None)
    if sample_rate is None:
        sample_rate = getattr(scorer, "sample_rate", None)
    if filter_string is None:
        filter_string = getattr(scorer, "filter_string", None)
    return sample_rate, filter_string


def _monitoring_state(sample_rate: float | None) -> str:
    """Classify the existing operator monitoring intent for the receipt."""
    if sample_rate is None:
        return "unconfigured"
    return "paused" if sample_rate == 0 else "active"


def _require_live() -> None:
    """
    Enforce the explicit live opt-in for credentialed judge/SME operations.

    Raises:
        AlignmentError: If ``FLEET_LIVE`` is not enabled.
    """
    if os.environ.get("FLEET_LIVE", "").lower() not in _LIVE_VALUES:
        raise AlignmentError("FLEET_LIVE=1 is required for judge alignment")


def _configure_mlflow(args: argparse.Namespace) -> str:
    """
    Resolve the target experiment id from an explicit id or the experiment name.

    Parameters:
        args (argparse.Namespace): Parsed CLI arguments.

    Returns:
        str: The resolved experiment id.

    Raises:
        AlignmentError: If the experiment name does not resolve.
    """
    import mlflow

    mlflow.set_tracking_uri(args.mlflow_url)
    if args.experiment_id:
        mlflow.set_experiment(experiment_id=args.experiment_id)
        return str(args.experiment_id)
    experiment = mlflow.get_experiment_by_name(args.experiment_name)
    if experiment is None:
        raise AlignmentError(f"MLflow experiment not found: {args.experiment_name!r}")
    mlflow.set_experiment(experiment_id=experiment.experiment_id)
    return str(experiment.experiment_id)


def _label_schema_definition(name: str) -> dict[str, Any]:
    from mlflow.genai import label_schemas

    return {
        "name": name,
        "type": "feedback",
        "title": name,
        "input": label_schemas.InputPassFail(positive_label="pass", negative_label="fail"),
        "instruction": (
            f"Judge the agent response exactly as the {name!r} judge does. Mark 'pass' only when the "
            "response satisfies every stated criterion; otherwise mark 'fail' and add a short rationale."
        ),
        "enable_comment": True,
        "overwrite": True,
    }


def prepare_labeling(args: argparse.Namespace) -> dict[str, Any]:
    """
    Register base judges, evaluate static records, tag traces, and open an SME labeling session.

    Parameters:
        args (argparse.Namespace): Connection, judge-model, and labeling session options.

    Returns:
        dict[str, Any]: Receipt with registered judges, tagged trace count, dataset, and session URL.

    Raises:
        AlignmentError: If no SME users were assigned or no traces scored OK.
    """
    _require_live()
    if not args.assigned_users:
        raise AlignmentError("prepare-labeling requires --assigned-users with at least one Databricks user email")
    experiment_id = _configure_mlflow(args)
    import mlflow
    from mlflow.genai import create_labeling_session, label_schemas
    from mlflow.genai.datasets import create_dataset, get_dataset

    registered = []
    for name in JUDGE_NAMES:
        if ensure_registered(name, args.judge_model, experiment_id=experiment_id):
            registered.append(name)

    from mlflow.genai.scorers import get_scorer

    def predict_fn(query: str) -> str:
        with httpx.Client(base_url=args.api_url.rstrip("/"), timeout=httpx.Timeout(args.timeout)) as client:
            return str(run_turn(client, query, nonce=f"align-{uuid4()}")["answer"])

    results = mlflow.genai.evaluate(
        data=list(QUALITY_RECORDS),
        predict_fn=predict_fn,
        scorers=[get_scorer(name=name, experiment_id=experiment_id) for name in JUDGE_NAMES],
    )
    ok_trace_ids = list(results.result_df.loc[results.result_df["state"] == "OK", "trace_id"])
    if not ok_trace_ids:
        raise AlignmentError("no traces scored OK; cannot build a labeling dataset")
    for trace_id in ok_trace_ids:
        mlflow.set_trace_tag(trace_id=trace_id, key=EVAL_TAG, value=EVAL_TAG_VALUE)

    try:
        dataset = get_dataset(name=args.dataset_name)
    except Exception:
        dataset = create_dataset(name=args.dataset_name, experiment_id=experiment_id)
    tagged = mlflow.search_traces(
        locations=[experiment_id],
        filter_string=f"tag.{EVAL_TAG} = '{EVAL_TAG_VALUE}'",
        return_type="pandas",
    )
    if "inputs" not in tagged.columns and "request" in tagged.columns:
        tagged = tagged.rename(columns={"request": "inputs"})
    if "outputs" not in tagged.columns and "response" in tagged.columns:
        tagged = tagged.rename(columns={"response": "outputs"})
    dataset.merge_records(tagged)

    for name in JUDGE_NAMES:
        label_schemas.create_label_schema(**_label_schema_definition(name))

    session = create_labeling_session(
        name=args.session_name,
        assigned_users=list(args.assigned_users),
        label_schemas=list(JUDGE_NAMES),
    )
    session = session.add_dataset(dataset_name=args.dataset_name)
    return {
        "command": "prepare-labeling",
        "experiment_id": experiment_id,
        "judges_registered": registered,
        "tagged_traces": len(ok_trace_ids),
        "dataset_name": args.dataset_name,
        "label_schemas": list(JUDGE_NAMES),
        "session_name": session.name,
        "session_url": session.url,
    }


def align(args: argparse.Namespace) -> dict[str, Any]:
    """
    Distill SME feedback into judge guidelines with MemAlign and persist the aligned judges.

    Parameters:
        args (argparse.Namespace): Connection and optimizer model options.

    Returns:
        dict[str, Any]: Receipt with per-judge alignment status and trace counts.

    Raises:
        AlignmentError: If no tagged traces are available for alignment.
    """
    _require_live()
    experiment_id = _configure_mlflow(args)
    import mlflow
    from mlflow.genai.judges.optimizers import MemAlignOptimizer
    from mlflow.genai.scorers import ScorerSamplingConfig, get_scorer

    traces = mlflow.search_traces(
        locations=[experiment_id],
        filter_string=f"tag.{EVAL_TAG} = '{EVAL_TAG_VALUE}'",
        return_type="list",
    )
    if not traces:
        raise AlignmentError(f"no traces tagged {EVAL_TAG}={EVAL_TAG_VALUE}; run prepare-labeling first")

    optimizer = MemAlignOptimizer(
        reflection_lm=args.reflection_model,
        retrieval_k=args.retrieval_k,
        embedding_model=args.embedding_model,
    )
    aligned_rows = []
    for name in JUDGE_NAMES:
        current = get_scorer(name=name, experiment_id=experiment_id)
        prior_sample_rate, prior_filter_string = _sampling_details(current)
        aligned = current.align(traces=traces, optimizer=optimizer)
        updated = aligned.update(
            experiment_id=experiment_id,
            sampling_config=ScorerSamplingConfig(
                sample_rate=prior_sample_rate,
                filter_string=prior_filter_string,
            ),
        )
        persisted = updated if updated is not None else aligned
        aligned_version = getattr(persisted, "version", None)
        if not isinstance(aligned_version, (str, int)):
            aligned_version = None
        guidelines = getattr(aligned, "_semantic_memory", None)
        aligned_rows.append(
            {
                "name": name,
                "status": "aligned",
                "guideline_count": len(guidelines) if isinstance(guidelines, (list, tuple)) else None,
                "traces": len(traces),
                "prior_sample_rate": prior_sample_rate,
                "resulting_sample_rate": prior_sample_rate,
                "prior_filter_string": prior_filter_string,
                "resulting_filter_string": prior_filter_string,
                "aligned_version": aligned_version,
                "monitoring_state": _monitoring_state(prior_sample_rate),
            }
        )
    return {"command": "align", "experiment_id": experiment_id, "judges": aligned_rows}


def reeval_baseline(args: argparse.Namespace) -> dict[str, Any]:
    """
    Re-evaluate the quality dataset with the currently registered (aligned) judges in a named run.

    When --prior-metrics is supplied, compare aligned metrics against the stored
    pre-alignment receipt; lower aligned scores are expected and indicate a more
    accurate judge, not a regression.

    Parameters:
        args (argparse.Namespace): Connection, run-name, and comparison options.

    Returns:
        dict[str, Any]: Receipt with run identity, aligned metrics, and optional deltas.
    """
    _require_live()
    experiment_id = _configure_mlflow(args)
    import mlflow
    from mlflow.genai import datasets
    from mlflow.genai.scorers import get_scorer

    dataset = datasets.get_dataset(name=args.quality_dataset_name)
    frame = dataset.to_df()

    def predict_fn(query: str) -> str:
        with httpx.Client(base_url=args.api_url.rstrip("/"), timeout=httpx.Timeout(args.timeout)) as client:
            return str(run_turn(client, query, nonce=f"align-baseline-{uuid4()}")["answer"])

    with mlflow.start_run(run_name=args.run_name) as run:
        result = mlflow.genai.evaluate(
            data=frame,
            predict_fn=predict_fn,
            scorers=[get_scorer(name=name, experiment_id=experiment_id) for name in JUDGE_NAMES],
        )
        run_id = run.info.run_id

    receipt: dict[str, Any] = {
        "command": "reeval-baseline",
        "experiment_id": experiment_id,
        "dataset_name": args.quality_dataset_name,
        "run_id": run_id,
        "run_name": args.run_name,
        "records": len(frame),
        "metrics": {str(key): value for key, value in result.metrics.items()},
    }
    if args.prior_metrics is not None:
        try:
            prior = json.loads(args.prior_metrics.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AlignmentError(f"could not read prior metrics: {args.prior_metrics}") from exc
        prior_metrics = prior.get("metrics", prior)
        receipt["metric_deltas"] = {
            key: receipt["metrics"].get(key, 0.0) - float(prior_metrics.get(key, 0.0))
            for key in sorted(set(prior_metrics) | set(receipt["metrics"]))
        }
    return receipt


def build_parser() -> argparse.ArgumentParser:
    """
    Create the command-line argument parser for the judge-alignment workflow.

    Returns:
        argparse.ArgumentParser: Parser configured with command, connection,
        labeling, and optimizer options.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare-labeling", "align", "reeval-baseline"))
    parser.add_argument("--mlflow-url", default=DEFAULT_MLFLOW_URL)
    parser.add_argument("--experiment-id", default="")
    parser.add_argument("--experiment-name", default=_experiment_name_default())
    parser.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL)
    parser.add_argument("--api-url", default=DEFAULT_API_URL)
    parser.add_argument("--timeout", type=float, default=2_000.0)
    parser.add_argument("--dataset-name", default=_alignment_dataset_name_default(), help=ALIGNMENT_DATASET_NAME)
    parser.add_argument("--quality-dataset-name", default=DATASET_NAME)
    parser.add_argument(
        "--session-name",
        default=f"{Path.cwd().name}-judge-alignment",
        help="Labeling session display name",
    )
    parser.add_argument(
        "--assigned-users",
        nargs="*",
        default=[],
        help="Databricks SME user emails assigned to the labeling session",
    )
    parser.add_argument("--reflection-model", default=DEFAULT_REFLECTION_MODEL)
    parser.add_argument("--embedding-model", default=DEFAULT_EMBEDDING_MODEL)
    parser.add_argument("--retrieval-k", type=int, default=5)
    parser.add_argument("--run-name", default="aligned_judge_baseline")
    parser.add_argument("--prior-metrics", type=Path, default=None)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """
    Run the selected alignment command and write its result as a JSON receipt.

    Parameters:
        argv (Sequence[str] | None): Optional command-line arguments; uses the
            process arguments when omitted.

    Returns:
        int: `0` when the command succeeds, `1` when it fails.
    """
    load_dotenv(_REPO_ROOT / ".env", override=False)
    args = build_parser().parse_args(argv)
    try:
        if args.command == "prepare-labeling":
            receipt = prepare_labeling(args)
        elif args.command == "align":
            receipt = align(args)
        else:
            receipt = reeval_baseline(args)
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
