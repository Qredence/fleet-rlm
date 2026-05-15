#!/usr/bin/env python3
"""Log a LongCoT benchmark run to MLflow with proper tags, params, metrics, artifacts, and dataset.

Usage:
    uv run python scripts/log_benchmark_to_mlflow.py \
        --mode direct \
        --eval-json path/to/longcot-summary.json \
        --jsonl path/to/results.jsonl \
        --dataset-path scripts/benchmarks/longcot_mini_stratified_100.json

    uv run python scripts/log_benchmark_to_mlflow.py \
        --mode rlm \
        --eval-json path/to/longcot-summary.json \
        --jsonl path/to/results.jsonl \
        --dataset-path scripts/benchmarks/longcot_mini_stratified_100.json \
        --num-workers 4 \
        --rlm-max-passes 1 \
        --rlm-max-repair-attempts 0
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import mlflow
import mlflow.data
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
MLFLOW_TRACKING_URI = os.getenv("MLFLOW_TRACKING_URI", "http://127.0.0.1:5001")
EXPERIMENT_ID = os.getenv("MLFLOW_EXPERIMENT_ID", "4")


def _load_or_create_dataset(dataset_path: Path) -> mlflow.data.Dataset:
    """Create an MLflow dataset from a stratified slice file."""
    with open(dataset_path) as f:
        slice_data = json.load(f)

    rows = []
    for domain, question_ids in slice_data.get("domains", {}).items():
        for qid in question_ids:
            rows.append({"question_id": qid, "domain": domain})

    df = pd.DataFrame(rows)
    dataset = mlflow.data.from_pandas(
        df,
        source=str(dataset_path.resolve()),
        name="longcot-mini-100",
    )
    return dataset


def _log_run(
    mode: str,
    eval_json_path: Path,
    jsonl_path: Path,
    dataset: mlflow.data.Dataset,
    num_workers: int | None = None,
    rlm_max_passes: int | None = None,
    rlm_max_repair_attempts: int | None = None,
) -> str:
    """Create an MLflow run for a benchmark mode and return the run ID."""
    with open(eval_json_path) as f:
        summary = json.load(f)

    by_domain = summary.get("by_domain", {})
    model = summary.get("model", "unknown")
    provider = summary.get("provider", "unknown")
    # Normalize model name for tags
    model_tag = model.split("/")[-1] if "/" in model else model

    tags = {
        "mode": mode,
        "model": model_tag,
        "provider": provider,
        "tasks": str(summary.get("tasks_total", 0)),
    }

    params: dict[str, str] = {
        "config": "or_deepseek_v4_flash",
        "difficulty": summary.get("difficulty", "longcot-mini"),
    }

    if mode == "direct":
        params["num_workers"] = str(num_workers) if num_workers is not None else "8"
    elif mode == "rlm":
        params["num_workers"] = str(num_workers) if num_workers is not None else "4"
        params["rlm_max_passes"] = str(rlm_max_passes) if rlm_max_passes is not None else "1"
        params["rlm_max_repair_attempts"] = str(rlm_max_repair_attempts) if rlm_max_repair_attempts is not None else "0"

    run_name = f"longcot-{mode}-{model_tag}-{summary.get('difficulty', 'unknown')}-{summary.get('tasks_total', 0)}"

    with mlflow.start_run(experiment_id=EXPERIMENT_ID, run_name=run_name) as run:
        run_id = run.info.run_id

        # Tags
        for key, value in tags.items():
            mlflow.set_tag(key, value)

        # Params
        for key, value in params.items():
            mlflow.log_param(key, value)

        # Metrics
        mlflow.log_metric("accuracy", summary["accuracy"])
        mlflow.log_metric("overall_accuracy", summary["overall_accuracy"])
        mlflow.log_metric("correct", summary["correct"])
        mlflow.log_metric("incorrect", summary["incorrect"])
        mlflow.log_metric("failed", summary["failed"])
        mlflow.log_metric("tasks_total", summary["tasks_total"])
        mlflow.log_metric("tasks_successful", summary["tasks_successful"])

        # Per-domain metrics
        for domain, stats in by_domain.items():
            mlflow.log_metric(f"{domain}_correct", stats["correct"])
            mlflow.log_metric(f"{domain}_incorrect", stats["incorrect"])
            mlflow.log_metric(f"{domain}_failed", stats["failed"])

        # Artifacts
        mlflow.log_artifact(str(eval_json_path), artifact_path="eval")
        mlflow.log_artifact(str(jsonl_path), artifact_path="results")

        # Dataset association
        mlflow.log_input(dataset, context="evaluation")

        print(f"Created {mode} run: {run_id}")
        print(f"  View at: {MLFLOW_TRACKING_URI}/#/experiments/{EXPERIMENT_ID}/runs/{run_id}")
        return run_id


def main() -> int:
    parser = argparse.ArgumentParser(description="Log a LongCoT benchmark run to MLflow")
    parser.add_argument("--mode", required=True, choices=["direct", "rlm"], help="Benchmark mode")
    parser.add_argument("--eval-json", required=True, type=Path, help="Path to evaluation summary JSON")
    parser.add_argument("--jsonl", required=True, type=Path, help="Path to merged results JSONL")
    parser.add_argument(
        "--dataset-path",
        type=Path,
        default=ROOT / "scripts" / "benchmarks" / "longcot_mini_stratified_100.json",
        help="Path to stratified slice JSON used as dataset source",
    )
    parser.add_argument("--num-workers", type=int, help="Number of workers (default: 8 direct, 4 rlm)")
    parser.add_argument("--rlm-max-passes", type=int, help="RLM max passes (default: 1)")
    parser.add_argument(
        "--rlm-max-repair-attempts",
        type=int,
        help="RLM max repair attempts (default: 0)",
    )
    args = parser.parse_args()

    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    experiment = mlflow.get_experiment(EXPERIMENT_ID)
    print(f"Experiment: {experiment.name} (id={experiment.experiment_id})")

    dataset = _load_or_create_dataset(args.dataset_path)
    print(f"Dataset: {dataset.name} (digest={dataset.digest})")

    run_id = _log_run(
        mode=args.mode,
        eval_json_path=args.eval_json,
        jsonl_path=args.jsonl,
        dataset=dataset,
        num_workers=args.num_workers,
        rlm_max_passes=args.rlm_max_passes,
        rlm_max_repair_attempts=args.rlm_max_repair_attempts,
    )

    print(f"\nRun ID: {run_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
