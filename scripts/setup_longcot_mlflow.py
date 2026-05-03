#!/usr/bin/env python3
"""Set up MLflow datasets and evaluation runs for Fleet-RLM LongCoT benchmark.

Usage:
    uv run python scripts/setup_longcot_mlflow.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import mlflow
import mlflow.data
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
MLFLOW_TRACKING_URI = "http://127.0.0.1:5001"
EXPERIMENT_ID = "4"

# Source artifact paths from existing runs
DIRECT_SUMMARY_PATH = (
    ROOT
    / "mlartifacts"
    / "4"
    / "8d35d20bb017451c8879097e09ffa56f"
    / "artifacts"
    / "longcot-summary.json"
)
DIRECT_JSONL_PATH = (
    ROOT
    / "mlartifacts"
    / "4"
    / "8d35d20bb017451c8879097e09ffa56f"
    / "artifacts"
    / "longcot_or_deepseek_v4_flash_all_longcot-mini_MERGED_100.jsonl"
)
RLM_SUMMARY_PATH = (
    ROOT
    / "mlartifacts"
    / "4"
    / "8b345cf31cbc4373a3f3a3a38cc0578e"
    / "artifacts"
    / "longcot-summary.json"
)
RLM_JSONL_PATH = (
    ROOT
    / "mlartifacts"
    / "4"
    / "8b345cf31cbc4373a3f3a3a38cc0578e"
    / "artifacts"
    / "longcot_rlm_all_longcot-mini_MERGED_100.jsonl"
)
STRATIFIED_PATH = ROOT / "scripts" / "benchmarks" / "longcot_mini_stratified_100.json"


def _create_dataset() -> mlflow.data.Dataset:
    """Create an MLflow dataset from the stratified slice file."""
    with open(STRATIFIED_PATH, "r") as f:
        slice_data = json.load(f)

    rows = []
    for domain, question_ids in slice_data["domains"].items():
        for qid in question_ids:
            rows.append({"question_id": qid, "domain": domain})

    df = pd.DataFrame(rows)
    source_path = str(STRATIFIED_PATH.resolve())

    dataset = mlflow.data.from_pandas(
        df,
        source=source_path,
        name="longcot-mini-100",
    )
    return dataset


def _log_eval_run(
    mode: str,
    summary_path: Path,
    jsonl_path: Path,
    dataset: mlflow.data.Dataset,
) -> str:
    """Create an MLflow run for a benchmark mode and return the run ID."""
    with open(summary_path, "r") as f:
        summary = json.load(f)

    by_domain = summary.get("by_domain", {})

    if mode == "direct":
        tags = {
            "mode": "direct",
            "model": "deepseek-v4-flash",
            "provider": "openrouter",
            "tasks": "100",
        }
        params = {
            "config": "or_deepseek_v4_flash",
            "difficulty": "longcot-mini",
            "num_workers": "8",
        }
    elif mode == "rlm":
        tags = {
            "mode": "rlm",
            "model": "deepseek-v4-flash",
            "provider": "openrouter",
            "tasks": "100",
        }
        params = {
            "config": "or_deepseek_v4_flash",
            "difficulty": "longcot-mini",
            "num_workers": "4",
            "rlm_max_passes": "1",
            "rlm_max_repair_attempts": "0",
        }
    else:
        raise ValueError(f"Unknown mode: {mode}")

    with mlflow.start_run(
        experiment_id=EXPERIMENT_ID,
        run_name=f"longcot-{mode}-deepseek-v4-flash-longcot-mini-100",
    ) as run:
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
        mlflow.log_artifact(str(summary_path), artifact_path="eval")
        mlflow.log_artifact(str(jsonl_path), artifact_path="results")

        # Dataset association
        mlflow.log_input(dataset, context="evaluation")

        print(f"Created {mode} run: {run_id}")
        return run_id


def _verify_run(run_id: str) -> dict:
    """Query MLflow to verify a run has correct metrics and artifacts."""
    from mlflow.tracking import MlflowClient

    client = MlflowClient()
    run = mlflow.get_run(run_id)
    artifacts = client.list_artifacts(run_id)
    artifact_names = [a.path for a in artifacts]

    return {
        "run_id": run_id,
        "run_name": run.data.tags.get("mlflow.runName", run.info.run_name),
        "metrics": dict(run.data.metrics),
        "params": dict(run.data.params),
        "tags": {k: v for k, v in run.data.tags.items() if not k.startswith("mlflow.")},
        "artifacts": artifact_names,
        "dataset_inputs": [
            {"dataset_name": inp.dataset.name}
            for inp in (run.inputs.dataset_inputs or [])
        ],
    }


def main() -> int:
    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    experiment = mlflow.get_experiment(EXPERIMENT_ID)
    print(f"Experiment: {experiment.name} (id={experiment.experiment_id})")

    # Create dataset
    dataset = _create_dataset()
    print(f"Created dataset: {dataset.name} (digest={dataset.digest})")

    # Create Direct run
    direct_run_id = _log_eval_run(
        "direct", DIRECT_SUMMARY_PATH, DIRECT_JSONL_PATH, dataset
    )

    # Create RLM run
    rlm_run_id = _log_eval_run("rlm", RLM_SUMMARY_PATH, RLM_JSONL_PATH, dataset)

    # Verify
    print("\n--- Verification ---")
    for run_id in (direct_run_id, rlm_run_id):
        info = _verify_run(run_id)
        print(f"\nRun: {info['run_name']} ({info['run_id']})")
        print(f"  Tags: {info['tags']}")
        print(f"  Params: {info['params']}")
        print(
            f"  Metrics: accuracy={info['metrics'].get('accuracy')}, correct={info['metrics'].get('correct')}, incorrect={info['metrics'].get('incorrect')}, failed={info['metrics'].get('failed')}"
        )
        print(f"  Artifacts: {info['artifacts']}")
        print(f"  Dataset inputs: {info['dataset_inputs']}")

    print("\n✅ Setup complete.")
    print(f"Direct run: {direct_run_id}")
    print(f"RLM run:    {rlm_run_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
