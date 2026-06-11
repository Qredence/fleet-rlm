#!/usr/bin/env python3
"""Unified CLI for MLflow analytics and evaluation workflows."""

from __future__ import annotations

import argparse
import inspect
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from fleet_rlm.integrations.observability.config import MlflowConfig
from fleet_rlm.quality.mlflow_evaluation import (
    evaluate_trace_rows,
    export_annotated_trace_rows,
    load_trace_rows,
    save_evaluation_result,
)
from fleet_rlm.quality.optimization_runner import (
    run_module_optimization,
    spec_for_program,
)


def _configure_mlflow_tracking() -> tuple[Any, MlflowConfig, str | None]:
    import mlflow

    config = MlflowConfig.from_env()
    mlflow.set_tracking_uri(config.tracking_uri)
    experiment_id = None
    if config.experiment:
        experiment = mlflow.set_experiment(experiment_name=config.experiment)
        experiment_id = str(getattr(experiment, "experiment_id", "") or "") or None
    return mlflow, config, experiment_id


def _scorer_attr(scorer: Any, *names: str) -> str:
    for name in names:
        if isinstance(scorer, Mapping):
            value = scorer.get(name)
        else:
            value = getattr(scorer, name, None)
        if value not in (None, ""):
            return str(value)
    return ""


def do_export(args: argparse.Namespace) -> int:
    rows = export_annotated_trace_rows(
        output_path=args.output,
        max_results=args.max_results,
    )
    print(f"exported_rows={len(rows)}")
    print(f"output={args.output}")
    return 0


def do_evaluate(args: argparse.Namespace) -> int:
    if args.input is None:
        rows = export_annotated_trace_rows(
            output_path=args.export_output,
            max_results=args.max_results,
        )
    else:
        rows = load_trace_rows(args.input)

    result = evaluate_trace_rows(
        rows,
        include_safety=args.include_safety,
        guidelines=args.guideline,
    )
    payload = save_evaluation_result(result=result, output_path=args.results_output)

    print(f"evaluated_rows={len(rows)}")
    print(f"results_output={args.results_output}")
    print(f"metrics={sorted(payload['metrics'])}")
    return 0


def do_optimize(args: argparse.Namespace) -> int:
    spec = spec_for_program(
        args.program,
        input_keys=args.input_key or None,
        output_key=args.output_key,
    )
    summary = run_module_optimization(
        spec,
        dataset_path=args.dataset,
        output_path=args.output,
        train_ratio=args.train_ratio,
        auto=args.auto,
        optimizer=args.optimizer,
    )
    for key in (
        "train_examples",
        "validation_examples",
        "validation_score",
        "output_path",
        "manifest_path",
        "program_spec",
        "optimizer",
    ):
        print(f"{key}={summary.get(key)}")
    return 0


def do_scorers_list(args: argparse.Namespace) -> int:
    mlflow, config, active_experiment_id = _configure_mlflow_tracking()
    experiment_id = args.experiment_id or active_experiment_id
    list_scorers = getattr(getattr(mlflow, "genai", None), "list_scorers", None)
    if not callable(list_scorers):
        raise RuntimeError("mlflow.genai.list_scorers is not available in this MLflow version.")

    scorers = list_scorers(experiment_id=experiment_id)
    print(f"tracking_uri={config.tracking_uri}")
    print(f"experiment={config.experiment}")
    print(f"experiment_id={experiment_id or ''}")
    print(f"scorer_count={len(scorers)}")
    for scorer in scorers:
        name = _scorer_attr(scorer, "name", "scorer_name") or "<unnamed>"
        scorer_id = _scorer_attr(scorer, "id", "scorer_id")
        version = _scorer_attr(scorer, "version", "scorer_version")
        model = _scorer_attr(scorer, "model", "model_uri")
        print(f"scorer name={name} id={scorer_id} version={version} model={model}")
    return 0


def _delete_scorer(delete_scorer: Any, *, name: str, experiment_id: str | None, version: str | None) -> None:
    parameters = inspect.signature(delete_scorer).parameters
    if "name" in parameters:
        kwargs: dict[str, Any] = {"name": name}
        if "experiment_id" in parameters:
            kwargs["experiment_id"] = experiment_id
        if "version" in parameters and version:
            kwargs["version"] = version
        delete_scorer(**kwargs)
        return
    if "name_or_id" in parameters:
        delete_scorer(name_or_id=name)
        return
    delete_scorer(name)


def _get_scorer(mlflow: Any, *, name: str, experiment_id: str | None, version: str | None = None) -> Any:
    get_scorer = getattr(getattr(mlflow, "genai", None), "get_scorer", None)
    if not callable(get_scorer):
        raise RuntimeError("mlflow.genai.get_scorer is not available in this MLflow version.")

    parameters = inspect.signature(get_scorer).parameters
    kwargs: dict[str, Any] = {"name": name}
    if "experiment_id" in parameters:
        kwargs["experiment_id"] = experiment_id
    if "version" in parameters and version:
        kwargs["version"] = int(version) if str(version).isdigit() else version
    return get_scorer(**kwargs)


def do_scorers_stop(args: argparse.Namespace) -> int:
    mlflow, config, active_experiment_id = _configure_mlflow_tracking()
    experiment_id = args.experiment_id or active_experiment_id
    scorer = _get_scorer(mlflow, name=args.name, experiment_id=experiment_id)
    stop_scorer = getattr(scorer, "stop", None)
    if not callable(stop_scorer):
        raise RuntimeError("This MLflow scorer does not expose stop().")
    stop_scorer()
    print(f"stopped_scorer={args.name}")
    print(f"experiment={config.experiment}")
    print(f"experiment_id={experiment_id or ''}")
    return 0


def do_scorers_start(args: argparse.Namespace) -> int:
    mlflow, config, active_experiment_id = _configure_mlflow_tracking()
    experiment_id = args.experiment_id or active_experiment_id
    scorer = _get_scorer(mlflow, name=args.name, experiment_id=experiment_id)
    start_scorer = getattr(scorer, "start", None)
    if not callable(start_scorer):
        raise RuntimeError("This MLflow scorer does not expose start().")

    from mlflow.genai.scorers import ScorerSamplingConfig

    start_scorer(sampling_config=ScorerSamplingConfig(sample_rate=args.sample_rate, filter_string=args.filter_string))
    print(f"started_scorer={args.name}")
    print(f"sample_rate={args.sample_rate}")
    print(f"experiment={config.experiment}")
    print(f"experiment_id={experiment_id or ''}")
    return 0


def do_scorers_delete(args: argparse.Namespace) -> int:
    if not args.yes:
        print("Refusing to delete scorer without --yes.")
        return 2
    mlflow, config, active_experiment_id = _configure_mlflow_tracking()
    experiment_id = args.experiment_id or active_experiment_id
    delete_scorer = getattr(getattr(mlflow, "genai", None), "delete_scorer", None)
    if not callable(delete_scorer):
        raise RuntimeError("mlflow.genai.delete_scorer is not available in this MLflow version.")

    _delete_scorer(
        delete_scorer,
        name=args.name,
        experiment_id=experiment_id,
        version=args.version,
    )
    print(f"deleted_scorer={args.name}")
    print(f"experiment={config.experiment}")
    print(f"experiment_id={experiment_id or ''}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Fleet RLM MLflow Analytics CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Export
    px = subparsers.add_parser("export", help="Export annotated MLflow traces into a JSON dataset")
    px.add_argument("--output", type=Path, default=Path("artifacts/mlflow/annotated-traces.json"))
    px.add_argument("--max-results", type=int, default=5000)
    px.set_defaults(func=do_export)

    # Evaluate
    pe = subparsers.add_parser("evaluate", help="Evaluate MLflow traces with MLflow GenAI scorers")
    pe.add_argument("--input", type=Path, default=None)
    pe.add_argument(
        "--export-output",
        type=Path,
        default=Path("artifacts/mlflow/annotated-traces.json"),
    )
    pe.add_argument(
        "--results-output",
        type=Path,
        default=Path("artifacts/mlflow/evaluation-results.json"),
    )
    pe.add_argument("--max-results", type=int, default=5000)
    pe.add_argument("--include-safety", action="store_true")
    pe.add_argument("--guideline", action="append", default=[])
    pe.set_defaults(func=do_evaluate)

    # Optimize
    po = subparsers.add_parser("optimize", help="Optimize a DSPy program (MIPROv2 default, GEPA optional)")
    po.add_argument("--dataset", type=Path, required=True)
    po.add_argument("--program", required=True)
    po.add_argument("--input-key", action="append", default=[])
    po.add_argument("--output-key", default="answer")
    po.add_argument("--output", type=Path, default=Path("artifacts/mlflow/optimized-program.json"))
    po.add_argument("--train-ratio", type=float, default=0.8)
    po.add_argument("--auto", default="light", choices=("light", "medium", "heavy"))
    po.add_argument("--optimizer", default="miprov2", choices=("gepa", "miprov2"))
    po.set_defaults(func=do_optimize)

    # Scorers
    ps = subparsers.add_parser("scorers", help="Inspect or remove persisted MLflow GenAI scorers")
    scorer_subparsers = ps.add_subparsers(dest="scorer_command", required=True)

    psl = scorer_subparsers.add_parser("list", help="List persisted scorers for the active MLflow experiment")
    psl.add_argument("--experiment-id", default=None)
    psl.set_defaults(func=do_scorers_list)

    pss = scorer_subparsers.add_parser("stop", help="Stop a persisted scorer schedule without deleting it")
    pss.add_argument("--name", required=True)
    pss.add_argument("--experiment-id", default=None)
    pss.set_defaults(func=do_scorers_stop)

    psr = scorer_subparsers.add_parser("start", help="Start or resume a persisted scorer schedule")
    psr.add_argument("--name", required=True)
    psr.add_argument("--experiment-id", default=None)
    psr.add_argument("--sample-rate", type=float, default=1.0)
    psr.add_argument("--filter-string", default=None)
    psr.set_defaults(func=do_scorers_start)

    psd = scorer_subparsers.add_parser("delete", help="Delete a persisted scorer by name")
    psd.add_argument("--name", required=True)
    psd.add_argument("--experiment-id", default=None)
    psd.add_argument("--version", default=None)
    psd.add_argument("--yes", action="store_true", help="Confirm scorer deletion")
    psd.set_defaults(func=do_scorers_delete)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    import sys

    sys.exit(main())
