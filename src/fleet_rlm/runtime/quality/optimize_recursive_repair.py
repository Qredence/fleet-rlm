"""Offline GEPA optimization entrypoint for recursive repair planning."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Literal

import dspy

from fleet_rlm.runtime.agent.recursive_repair import PlanRecursiveRepairModule

from .artifacts import resolve_artifact_path
from .datasets import load_dataset_rows, validate_required_keys
from .module_registry import ModuleOptimizationSpec, register_module
from .optimization_runner import OptimizationResult, run_module_optimization
from .scoring_helpers import (
    ScoreFeedbackBuilder,
    action_match_score,
    set_overlap_score,
    text_presence_score,
)

# -- Module-specific constants -----------------------------------------------

RecursiveRepairRow = dict[str, Any]
_INPUT_KEYS = [
    "user_request",
    "assembled_recursive_context",
    "verification_summary",
    "latest_sandbox_evidence",
    "latest_failure_signals",
    "repair_budget",
]
_REQUIRED_DATASET_KEYS = [*_INPUT_KEYS, "repair_mode"]
_MODULE_SLUG = "repair"
_ARTIFACT_FILENAME = "plan_recursive_repair.json"
_PROGRAM_SPEC = "fleet_rlm.runtime.agent.recursive_repair:PlanRecursiveRepairModule"


# -- Dataset conversion (module-specific) ------------------------------------


def load_recursive_repair_rows(dataset_path: Path) -> list[RecursiveRepairRow]:
    """Load a JSON or JSONL dataset of representative recursive repair traces."""
    return load_dataset_rows(dataset_path)


def rows_to_recursive_repair_examples(
    rows: list[RecursiveRepairRow],
) -> list[dspy.Example]:
    """Convert representative repair traces into DSPy examples."""
    valid = validate_required_keys(rows, _REQUIRED_DATASET_KEYS, "Repair")
    examples: list[dspy.Example] = []
    for row in valid:
        examples.append(
            dspy.Example(
                user_request=str(row.get("user_request", "") or ""),
                assembled_recursive_context=str(
                    row.get("assembled_recursive_context", "") or ""
                ),
                verification_summary=str(row.get("verification_summary", "") or ""),
                latest_sandbox_evidence=str(
                    row.get("latest_sandbox_evidence", "") or ""
                ),
                latest_failure_signals=str(row.get("latest_failure_signals", "") or ""),
                repair_budget=int(row.get("repair_budget", 0)),
                repair_mode=str(row.get("repair_mode", "no_repair") or "no_repair"),
                repair_target=str(row.get("repair_target", "") or ""),
                repair_steps=[
                    str(item or "") for item in row.get("repair_steps", []) or []
                ],
                repair_subqueries=[
                    str(item or "") for item in row.get("repair_subqueries", []) or []
                ],
                repair_rationale=str(row.get("repair_rationale", "") or ""),
            ).with_inputs(*_INPUT_KEYS)
        )
    if not examples:
        raise ValueError(
            "No valid recursive repair examples were found in the dataset."
        )
    return examples


# -- Metric (module-specific) ------------------------------------------------


def build_recursive_repair_feedback_metric() -> Any:
    """Build a GEPA metric for repair usefulness, boundedness, and success potential."""
    from dspy.teleprompt.gepa.gepa_utils import ScoreWithFeedback

    def metric(
        gold: Any,
        pred: Any,
        trace: Any = None,
        pred_name: str | None = None,
        pred_trace: Any = None,
    ) -> ScoreWithFeedback:
        _ = trace, pred_name, pred_trace
        expected_mode = str(getattr(gold, "repair_mode", "no_repair") or "no_repair")
        actual_mode = str(getattr(pred, "repair_mode", "no_repair") or "no_repair")
        expected_steps = set(getattr(gold, "repair_steps", []) or [])
        actual_steps = set(getattr(pred, "repair_steps", []) or [])
        expected_subqueries = set(getattr(gold, "repair_subqueries", []) or [])
        actual_subqueries = set(getattr(pred, "repair_subqueries", []) or [])
        repair_budget = int(getattr(gold, "repair_budget", 0))
        repair_target = str(getattr(pred, "repair_target", "") or "").strip()
        repair_rationale = str(getattr(pred, "repair_rationale", "") or "").strip()

        builder = ScoreFeedbackBuilder()

        # Mode match (0.35 weight)
        builder.add(
            0.35,
            action_match_score(expected_mode, actual_mode),
            "Repair mode matches the representative trace."
            if actual_mode == expected_mode
            else f"Expected repair_mode={expected_mode!r} but received {actual_mode!r}.",
        )

        # Step overlap (0.2 weight)
        if expected_steps:
            step_overlap = set_overlap_score(expected_steps, actual_steps)
            builder.add(
                0.2,
                step_overlap,
                "Repair steps preserve the representative bounded repair sequence."
                if step_overlap >= 0.75
                else "Repair steps missed important representative repair actions.",
            )
        elif not actual_steps:
            builder.add(0.2, 1.0, "No repair steps were expected or introduced.")

        # Subquery overlap (0.15 weight)
        if expected_subqueries:
            subquery_overlap = set_overlap_score(expected_subqueries, actual_subqueries)
            builder.add(
                0.15,
                subquery_overlap,
                "Repair subqueries preserve the representative bounded delegation plan."
                if subquery_overlap >= 0.75
                else "Repair subqueries missed important representative delegated checks.",
            )
        elif not actual_subqueries:
            builder.add(0.15, 1.0, "No repair subqueries were expected or introduced.")

        # Budget boundedness (0.15 weight)
        bounded_count = max(len(actual_steps), len(actual_subqueries))
        if bounded_count > 0:
            budget_limit = (
                max(1, repair_budget + 1) if repair_budget > 0 else bounded_count
            )
            builder.add(
                0.15,
                1.0 if bounded_count <= budget_limit else 0.0,
                "Repair plan stays within the requested bounded budget."
                if bounded_count <= budget_limit
                else "Repair plan exceeded the requested bounded budget.",
            )
        else:
            builder.add(0.0, 0.0, "Repair plan is empty.")

        # Repair target (0.1 weight)
        builder.add(
            0.1,
            text_presence_score(repair_target),
            "Repair target is present." if repair_target else "Repair target is empty.",
        )

        # Rationale (0.05 weight)
        builder.add(
            0.05,
            text_presence_score(repair_rationale),
            "Repair rationale explains why the plan should stay narrow."
            if repair_rationale
            else "Repair rationale is empty.",
        )

        return builder.build()

    return metric


# -- Artifact path (backward compat) ----------------------------------------


def resolve_recursive_repair_output_path(output_path: Path | None = None) -> Path:
    """Resolve the default artifact path for optimized recursive repair prompts."""
    return resolve_artifact_path(_MODULE_SLUG, _ARTIFACT_FILENAME, output_path)


# -- Optimization entrypoint -------------------------------------------------


def optimize_recursive_repair_module(
    *,
    dataset_path: Path,
    output_path: Path | None = None,
    train_ratio: float = 0.8,
    auto: Literal["light", "medium", "heavy"] | None = "light",
) -> OptimizationResult:
    """Run GEPA offline against the recursive repair module."""
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
        description="Optimize the recursive repair DSPy module offline with GEPA."
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
    result = optimize_recursive_repair_module(
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
    label="Repair",
    program_spec=_PROGRAM_SPEC,
    artifact_filename=_ARTIFACT_FILENAME,
    input_keys=list(_INPUT_KEYS),
    required_dataset_keys=list(_REQUIRED_DATASET_KEYS),
    module_factory=PlanRecursiveRepairModule,
    row_converter=rows_to_recursive_repair_examples,
    metric_builder=build_recursive_repair_feedback_metric,
    metric_name="recursive_repair_usefulness_and_boundedness",
    description="Improves repair plan quality by evolving instructions for diagnosing failures and generating targeted, minimal fixes that don't introduce regressions.",
)

register_module(_MODULE_SPEC)
