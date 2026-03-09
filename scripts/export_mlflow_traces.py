from __future__ import annotations

import argparse
from pathlib import Path

from fleet_rlm.analytics.mlflow_evaluation import export_annotated_trace_rows


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export annotated MLflow traces into a JSON dataset."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/mlflow/annotated-traces.json"),
        help="Path to the JSON file to write.",
    )
    parser.add_argument(
        "--max-results",
        type=int,
        default=5000,
        help="Maximum number of traces to search in the configured experiment.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    rows = export_annotated_trace_rows(
        output_path=args.output,
        max_results=args.max_results,
    )
    print(f"exported_rows={len(rows)}")
    print(f"output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
