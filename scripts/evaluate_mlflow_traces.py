from __future__ import annotations

import argparse
from pathlib import Path

from fleet_rlm.analytics.mlflow_evaluation import (
    evaluate_trace_rows,
    export_annotated_trace_rows,
    load_trace_rows,
    save_evaluation_result,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate annotated MLflow traces with MLflow GenAI scorers."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help="Optional JSON trace dataset. When omitted, live annotated traces are exported first.",
    )
    parser.add_argument(
        "--export-output",
        type=Path,
        default=Path("artifacts/mlflow/annotated-traces.json"),
        help="Where to export live annotated traces when --input is omitted.",
    )
    parser.add_argument(
        "--results-output",
        type=Path,
        default=Path("artifacts/mlflow/evaluation-results.json"),
        help="Where to write serialized evaluation results.",
    )
    parser.add_argument(
        "--max-results",
        type=int,
        default=5000,
        help="Maximum traces to search when exporting live data.",
    )
    parser.add_argument(
        "--include-safety",
        action="store_true",
        help="Add MLflow's built-in Safety scorer in addition to Correctness.",
    )
    parser.add_argument(
        "--guideline",
        action="append",
        default=[],
        help="Additional guideline text to score with MLflow Guidelines(). Repeatable.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
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
    payload = save_evaluation_result(
        result=result,
        output_path=args.results_output,
    )
    print(f"evaluated_rows={len(rows)}")
    print(f"results_output={args.results_output}")
    print(f"metrics={sorted(payload['metrics'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
