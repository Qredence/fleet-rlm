from __future__ import annotations

import argparse
from pathlib import Path

from fleet_rlm.analytics.mlflow_optimization import optimize_program_with_mipro


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Optimize a DSPy program with MIPROv2 using an MLflow trace dataset."
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        required=True,
        help="Path to an exported annotated trace dataset JSON file.",
    )
    parser.add_argument(
        "--program",
        required=True,
        help="DSPy program symbol in the form module:attr.",
    )
    parser.add_argument(
        "--input-key",
        action="append",
        default=[],
        help="Input field to keep from the dataset rows. Repeatable. Defaults to all row input keys.",
    )
    parser.add_argument(
        "--output-key",
        default="answer",
        help="Expected output field name used when constructing DSPy examples.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/mlflow/optimized-program.json"),
        help="Where to save the optimized DSPy program.",
    )
    parser.add_argument(
        "--train-ratio",
        type=float,
        default=0.8,
        help="Fraction of examples to keep in the training split.",
    )
    parser.add_argument(
        "--auto",
        default="light",
        help="MIPROv2 auto mode (for example: light or medium).",
    )
    parser.add_argument(
        "--run-name",
        default=None,
        help="Optional MLflow run name for the optimization session.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
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


if __name__ == "__main__":
    raise SystemExit(main())
