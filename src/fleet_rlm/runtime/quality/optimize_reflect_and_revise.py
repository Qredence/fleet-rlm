"""Offline GEPA optimization entrypoint for the recursive reflection module."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Literal

import dspy

from fleet_rlm.runtime.agent.recursive_reflection import (
    ReflectAndReviseWorkspaceStepModule,
)

from .artifacts import resolve_artifact_path
from .datasets import load_dataset_rows, validate_required_keys
from .module_registry import ModuleOptimizationSpec, register_module
from .optimization_runner import OptimizationResult, run_module_optimization
from .scoring_helpers import (
    ScoreFeedbackBuilder,
    action_match_score,
    text_presence_score,
)

# -- Module-specific constants -----------------------------------------------

ReflectionOptimizationRow = dict[str, Any]
_INPUT_KEYS = [
    "user_request",
    "working_memory_summary",
    "current_plan",
    "latest_sandbox_evidence",
    "latest_tool_or_code_result",
    "loop_state",
]
_REQUIRED_DATASET_KEYS = [*_INPUT_KEYS, "next_action"]
_MODULE_SLUG = "reflect-and-revise"
_ARTIFACT_FILENAME = "reflect_and_revise_workspace_step.json"
_PROGRAM_SPEC = (
    "fleet_rlm.runtime.agent.recursive_reflection:ReflectAndReviseWorkspaceStepModule"
)


# -- Dataset conversion (module-specific) ------------------------------------


def load_reflection_rows(dataset_path: Path) -> list[ReflectionOptimizationRow]:
    """Load a JSON or JSONL dataset of representative recursive reflection traces."""
    return load_dataset_rows(dataset_path)


def rows_to_reflection_examples(
    rows: list[ReflectionOptimizationRow],
) -> list[dspy.Example]:
    """Convert representative reflection traces into DSPy examples."""
    valid = validate_required_keys(rows, _REQUIRED_DATASET_KEYS, "Reflection")
    examples: list[dspy.Example] = []
    for row in valid:
        example = dspy.Example(
            **{key: str(row.get(key, "") or "") for key in _INPUT_KEYS},
            next_action=str(row.get("next_action", "finalize") or "finalize"),
            revised_plan=str(row.get("revised_plan", "") or ""),
            rationale=str(row.get("rationale", "") or ""),
            confidence=float(row.get("confidence", 0.0) or 0.0),
        ).with_inputs(*_INPUT_KEYS)
        examples.append(example)
    if not examples:
        raise ValueError("No valid reflection examples were found in the dataset.")
    return examples


# -- Metric (module-specific) ------------------------------------------------


def build_reflection_feedback_metric() -> Any:
    """Build a GEPA metric for recursive revise/recurse/finalize decisions."""
    from dspy.teleprompt.gepa.gepa_utils import ScoreWithFeedback

    def metric(
        gold: Any,
        pred: Any,
        trace: Any = None,
        pred_name: str | None = None,
        pred_trace: Any = None,
    ) -> ScoreWithFeedback:
        _ = trace, pred_name, pred_trace
        expected_action = str(getattr(gold, "next_action", "finalize") or "finalize")
        actual_action = str(getattr(pred, "next_action", "finalize") or "finalize")
        expected_plan = str(getattr(gold, "revised_plan", "") or "").strip().lower()
        actual_plan = str(getattr(pred, "revised_plan", "") or "").strip().lower()
        rationale = str(getattr(pred, "rationale", "") or "").strip()

        builder = ScoreFeedbackBuilder()

        # Action match (0.7 weight)
        builder.add(
            0.7,
            action_match_score(expected_action, actual_action),
            "Next action matches the representative recursive trace."
            if actual_action == expected_action
            else f"Expected next_action={expected_action!r} but received {actual_action!r}.",
        )

        # Plan overlap (0.2 weight)
        if expected_plan:
            overlap = sum(token in actual_plan for token in expected_plan.split())
            plan_score = overlap / max(1, len(expected_plan.split()))
            builder.add(
                0.2,
                min(1.0, plan_score),
                "Revised plan preserves key repair/recurse guidance."
                if plan_score >= 0.5
                else "Revised plan misses important guidance from the trace.",
            )
        elif actual_plan:
            builder.add(0.1, 1.0, "Revised plan is present for an open-ended trace.")

        # Rationale presence (0.1 weight)
        builder.add(
            0.1,
            text_presence_score(rationale),
            "Rationale is present for operator review."
            if rationale
            else "Rationale is empty; explain why the worker should branch.",
        )

        return builder.build()

    return metric


# -- Artifact path (backward compat) ----------------------------------------


def resolve_reflection_output_path(output_path: Path | None = None) -> Path:
    """Resolve the default artifact path for optimized recursive reflection prompts."""
    return resolve_artifact_path(_MODULE_SLUG, _ARTIFACT_FILENAME, output_path)


# -- Optimization entrypoint -------------------------------------------------


def optimize_reflect_and_revise_module(
    *,
    dataset_path: Path,
    output_path: Path | None = None,
    train_ratio: float = 0.8,
    auto: Literal["light", "medium", "heavy"] | None = "light",
) -> OptimizationResult:
    """Run GEPA offline against the recursive reflection module."""
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
        description="Optimize the recursive reflection DSPy module offline with GEPA."
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
    result = optimize_reflect_and_revise_module(
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
    label="Reflect & Revise",
    program_spec=_PROGRAM_SPEC,
    artifact_filename=_ARTIFACT_FILENAME,
    input_keys=list(_INPUT_KEYS),
    required_dataset_keys=list(_REQUIRED_DATASET_KEYS),
    module_factory=ReflectAndReviseWorkspaceStepModule,
    row_converter=rows_to_reflection_examples,
    metric_builder=build_reflection_feedback_metric,
    metric_name="reflection_decision_quality",
)

register_module(_MODULE_SPEC)
