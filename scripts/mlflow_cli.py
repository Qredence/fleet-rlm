#!/usr/bin/env python3
"""Unified CLI for MLflow analytics and evaluation workflows."""

from __future__ import annotations

import argparse
from pathlib import Path

from fleet_rlm.features.analytics.mlflow_evaluation import (
    evaluate_trace_rows,
    export_annotated_trace_rows,
    load_trace_rows,
    save_evaluation_result,
)
from fleet_rlm.features.analytics.mlflow_optimization import optimize_program_with_mipro


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
    summary = optimize_program_with_mipro(
        dataset_path=args.dataset,
        program_spec=args.program,
        output_path=args.output,
        input_keys=args.input_key or None,
        output_key=args.output_key,
        train_ratio=args.train_ratio,
        auto=args.auto,
        run_name=args.run_name,
    )
    for key, value in summary.items():
        print(f"{key}={value}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Fleet RLM MLflow Analytics CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Export
    px = subparsers.add_parser(
        "export", help="Export annotated MLflow traces into a JSON dataset"
    )
    px.add_argument(
        "--output", type=Path, default=Path("artifacts/mlflow/annotated-traces.json")
    )
    px.add_argument("--max-results", type=int, default=5000)
    px.set_defaults(func=do_export)

    # Evaluate
    pe = subparsers.add_parser(
        "evaluate", help="Evaluate MLflow traces with MLflow GenAI scorers"
    )
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
    po = subparsers.add_parser("optimize", help="Optimize a DSPy program with MIPROv2")
    po.add_argument("--dataset", type=Path, required=True)
    po.add_argument("--program", required=True)
    po.add_argument("--input-key", action="append", default=[])
    po.add_argument("--output-key", default="answer")
    po.add_argument(
        "--output", type=Path, default=Path("artifacts/mlflow/optimized-program.json")
    )
    po.add_argument("--train-ratio", type=float, default=0.8)
    po.add_argument("--auto", default="light")
    po.add_argument("--run-name", default=None)
    po.set_defaults(func=do_optimize)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    import sys

    sys.exit(main())
