"""Offline GEPA optimization entrypoint for recursive decomposition."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Literal

import dspy

from fleet_rlm.runtime.agent.recursive_decomposition import (
    PlanRecursiveSubqueriesModule,
)

from .artifacts import resolve_artifact_path
from .datasets import load_dataset_rows, validate_required_keys
from .module_registry import ModuleOptimizationSpec, register_module
from .optimization_runner import OptimizationResult, run_module_optimization
from .scoring_helpers import (
    ScoreFeedbackBuilder,
    action_match_score,
    boundedness_score,
    set_overlap_score,
    text_presence_score,
)

# -- Module-specific constants -----------------------------------------------

RecursiveDecompositionRow = dict[str, Any]
_DECOMPOSITION_INPUT_KEYS = [
    "user_request",
    "assembled_recursive_context",
    "current_plan",
    "loop_state",
    "latest_sandbox_evidence",
    "subquery_budget",
]
_REQUIRED_DATASET_KEYS = [
    *_DECOMPOSITION_INPUT_KEYS,
    "decomposition_mode",
    "subqueries",
]
_MODULE_SLUG = "decomposition"
_ARTIFACT_FILENAME = "plan_recursive_subqueries.json"
_PROGRAM_SPEC = (
    "fleet_rlm.runtime.agent.recursive_decomposition:PlanRecursiveSubqueriesModule"
)


# -- Dataset conversion (module-specific) ------------------------------------


def load_recursive_decomposition_rows(
    dataset_path: Path,
) -> list[RecursiveDecompositionRow]:
    """Load a JSON or JSONL dataset of representative recursive decomposition traces."""
    return load_dataset_rows(dataset_path)


def rows_to_recursive_decomposition_examples(
    rows: list[RecursiveDecompositionRow],
) -> list[dspy.Example]:
    """Convert representative recursive decomposition traces into DSPy examples."""
    valid = validate_required_keys(rows, _REQUIRED_DATASET_KEYS, "Decomposition")
    examples: list[dspy.Example] = []
    for row in valid:
        example = dspy.Example(
            user_request=str(row.get("user_request", "")),
            assembled_recursive_context=str(row.get("assembled_recursive_context", "")),
            current_plan=str(row.get("current_plan", "")),
            loop_state=str(row.get("loop_state", "")),
            latest_sandbox_evidence=str(row.get("latest_sandbox_evidence", "")),
            subquery_budget=int(row.get("subquery_budget", 0)),
            decomposition_mode=str(row.get("decomposition_mode", "")),
            subqueries=[str(item or "") for item in row.get("subqueries", [])],
            batching_strategy=str(row.get("batching_strategy", "")),
            aggregation_plan=str(row.get("aggregation_plan", "")),
            decomposition_rationale=str(row.get("decomposition_rationale", "")),
        ).with_inputs(*_DECOMPOSITION_INPUT_KEYS)
        examples.append(example)
    if not examples:
        raise ValueError(
            "No valid recursive decomposition examples were found in the dataset."
        )
    return examples


# -- Metric (module-specific) ------------------------------------------------


def build_recursive_decomposition_feedback_metric() -> Any:
    """Build a GEPA metric for decomposition quality, boundedness, and usefulness."""
    from dspy.teleprompt.gepa.gepa_utils import ScoreWithFeedback

    def metric(
        gold: Any,
        pred: Any,
        trace: Any = None,
        pred_name: str | None = None,
        pred_trace: Any = None,
    ) -> ScoreWithFeedback:
        _ = trace, pred_name, pred_trace
        expected_mode = str(getattr(gold, "decomposition_mode", "single_pass")).strip()
        actual_mode = str(getattr(pred, "decomposition_mode", "single_pass")).strip()
        expected_subqueries = [
            str(item or "") for item in getattr(gold, "subqueries", [])
        ]
        actual_subqueries = [
            str(item or "") for item in getattr(pred, "subqueries", [])
        ]
        subquery_budget = int(getattr(gold, "subquery_budget", 0))
        aggregation_plan = str(getattr(pred, "aggregation_plan", "")).strip()
        rationale = str(getattr(pred, "decomposition_rationale", "")).strip()

        builder = ScoreFeedbackBuilder()

        # Mode match (0.3 weight)
        builder.add(
            0.3,
            action_match_score(expected_mode, actual_mode),
            "Decomposition mode matches the representative trace."
            if actual_mode == expected_mode
            else f"Expected decomposition_mode={expected_mode!r} but received {actual_mode!r}.",
        )

        # Subquery overlap (0.35 weight)
        if expected_subqueries:
            overlap = set_overlap_score(
                set(expected_subqueries), set(actual_subqueries)
            )
            builder.add(
                0.35,
                overlap,
                "Subqueries preserve the representative decomposition."
                if overlap >= 0.75
                else "Subqueries missed relevant bounded subproblems from the trace.",
            )

        # Subquery presence + boundedness (0.25 weight)
        if actual_subqueries:
            builder.add(
                0.15,
                1.0,
                "At least one bounded subquery is present.",
            )
            builder.add(
                0.1,
                boundedness_score(len(actual_subqueries), subquery_budget),
                "Subqueries stay within the requested bounded budget."
                if subquery_budget <= 0 or len(actual_subqueries) <= subquery_budget
                else f"Subqueries exceeded the requested budget of {subquery_budget}.",
            )
        else:
            builder.add(0.15, 0.0, "Subqueries are empty.")

        # Aggregation plan (0.05 weight)
        builder.add(
            0.05,
            text_presence_score(aggregation_plan),
            "Aggregation plan is present for Python/runtime execution."
            if aggregation_plan
            else "Aggregation plan is empty.",
        )

        # Rationale (0.05 weight)
        builder.add(
            0.05,
            text_presence_score(rationale),
            "Decomposition rationale explains the semantic split."
            if rationale
            else "Decomposition rationale is empty.",
        )

        return builder.build()

    return metric


# -- Artifact path (backward compat) ----------------------------------------


def resolve_recursive_decomposition_output_path(
    output_path: Path | None = None,
) -> Path:
    """Resolve the default artifact path for optimized recursive decomposition prompts."""
    return resolve_artifact_path(_MODULE_SLUG, _ARTIFACT_FILENAME, output_path)


# -- Optimization entrypoint -------------------------------------------------


def optimize_recursive_decomposition_module(
    *,
    dataset_path: Path,
    output_path: Path | None = None,
    train_ratio: float = 0.8,
    auto: Literal["light", "medium", "heavy"] | None = "light",
) -> OptimizationResult:
    """Run GEPA offline against the recursive decomposition module."""
    return run_module_optimization(
        _MODULE_SPEC,
        dataset_path=dataset_path,
        output_path=output_path,
        train_ratio=train_ratio,
        auto=auto,
    )


# -- CLI ---------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Optimize the recursive decomposition DSPy module offline with GEPA."
    )
    parser.add_argument(
        "dataset_path", type=Path, help="Path to JSON or JSONL examples."
    )
    parser.add_argument(
        "--output-path",
        type=Path,
        default=None,
        help="Where to save the optimized DSPy module artifact.",
    )
    parser.add_argument(
        "--train-ratio",
        type=float,
        default=0.8,
        help="Training split ratio for GEPA compilation.",
    )
    parser.add_argument(
        "--auto",
        choices=("light", "medium", "heavy"),
        default="light",
        help="GEPA optimization intensity.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    result = optimize_recursive_decomposition_module(
        dataset_path=args.dataset_path,
        output_path=args.output_path,
        train_ratio=args.train_ratio,
        auto=args.auto,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


# -- Registry registration ---------------------------------------------------

_MODULE_SPEC = ModuleOptimizationSpec(
    module_slug=_MODULE_SLUG,
    label="Decomposition",
    program_spec=_PROGRAM_SPEC,
    artifact_filename=_ARTIFACT_FILENAME,
    input_keys=list(_DECOMPOSITION_INPUT_KEYS),
    required_dataset_keys=list(_REQUIRED_DATASET_KEYS),
    module_factory=PlanRecursiveSubqueriesModule,
    row_converter=rows_to_recursive_decomposition_examples,
    metric_builder=build_recursive_decomposition_feedback_metric,
    metric_name="recursive_decomposition_quality_and_boundedness",
)

register_module(_MODULE_SPEC)
