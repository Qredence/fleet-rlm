"""Offline GEPA optimization entrypoint for recursive aggregation verification."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Literal

import dspy

from fleet_rlm.runtime.agent.recursive_verification import (
    VerifyRecursiveAggregationModule,
)

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

RecursiveVerificationRow = dict[str, Any]
_INPUT_KEYS = [
    "user_request",
    "assembled_recursive_context",
    "decomposition_plan_summary",
    "collected_subquery_outputs",
    "latest_sandbox_evidence",
]
_REQUIRED_DATASET_KEYS = [*_INPUT_KEYS, "verification_status"]
_MODULE_SLUG = "verification"
_ARTIFACT_FILENAME = "verify_recursive_aggregation.json"
_PROGRAM_SPEC = (
    "fleet_rlm.runtime.agent.recursive_verification:VerifyRecursiveAggregationModule"
)


# -- Dataset conversion (module-specific) ------------------------------------


def load_recursive_verification_rows(
    dataset_path: Path,
) -> list[RecursiveVerificationRow]:
    """Load a JSON or JSONL dataset of representative recursive verification traces."""
    return load_dataset_rows(dataset_path)


def rows_to_recursive_verification_examples(
    rows: list[RecursiveVerificationRow],
) -> list[dspy.Example]:
    """Convert representative verification traces into DSPy examples."""
    valid = validate_required_keys(rows, _REQUIRED_DATASET_KEYS, "Verification")
    examples: list[dspy.Example] = []
    for row in valid:
        example = dspy.Example(
            user_request=str(row.get("user_request", "") or ""),
            assembled_recursive_context=str(
                row.get("assembled_recursive_context", "") or ""
            ),
            decomposition_plan_summary=str(
                row.get("decomposition_plan_summary", "") or ""
            ),
            collected_subquery_outputs=[
                str(item or "")
                for item in row.get("collected_subquery_outputs", []) or []
            ],
            latest_sandbox_evidence=str(row.get("latest_sandbox_evidence", "") or ""),
            verification_status=str(
                row.get("verification_status", "sufficient") or "sufficient"
            ),
            missing_evidence=[
                str(item or "") for item in row.get("missing_evidence", []) or []
            ],
            contradictions=[
                str(item or "") for item in row.get("contradictions", []) or []
            ],
            verified_summary=str(row.get("verified_summary", "") or ""),
            verification_rationale=str(row.get("verification_rationale", "") or ""),
        ).with_inputs(*_INPUT_KEYS)
        examples.append(example)
    if not examples:
        raise ValueError(
            "No valid recursive verification examples were found in the dataset."
        )
    return examples


# -- Metric (module-specific) ------------------------------------------------


def build_recursive_verification_feedback_metric() -> Any:
    """Build a GEPA metric for verification quality, boundedness, and usefulness."""
    from dspy.teleprompt.gepa.gepa_utils import ScoreWithFeedback

    def metric(
        gold: Any,
        pred: Any,
        trace: Any = None,
        pred_name: str | None = None,
        pred_trace: Any = None,
    ) -> ScoreWithFeedback:
        _ = trace, pred_name, pred_trace
        expected_status = str(
            getattr(gold, "verification_status", "sufficient") or "sufficient"
        )
        actual_status = str(
            getattr(pred, "verification_status", "sufficient") or "sufficient"
        )
        expected_missing = set(getattr(gold, "missing_evidence", []) or [])
        actual_missing = set(getattr(pred, "missing_evidence", []) or [])
        expected_contradictions = set(getattr(gold, "contradictions", []) or [])
        actual_contradictions = set(getattr(pred, "contradictions", []) or [])
        verified_summary = str(getattr(pred, "verified_summary", "") or "").strip()
        verification_rationale = str(
            getattr(pred, "verification_rationale", "") or ""
        ).strip()
        bounded_outputs = list(getattr(gold, "collected_subquery_outputs", []) or [])

        builder = ScoreFeedbackBuilder()

        # Status match (0.35 weight)
        builder.add(
            0.35,
            action_match_score(expected_status, actual_status),
            "Verification status matches the representative trace."
            if actual_status == expected_status
            else f"Expected verification_status={expected_status!r} but received {actual_status!r}.",
        )

        # Missing evidence overlap (0.2 weight)
        if expected_missing:
            missing_overlap = set_overlap_score(expected_missing, actual_missing)
            builder.add(
                0.2,
                missing_overlap,
                "Missing-evidence selection preserves the representative gaps."
                if missing_overlap >= 0.75
                else "Missing-evidence selection missed important representative gaps.",
            )
        elif not actual_missing:
            builder.add(0.2, 1.0, "No missing evidence was expected or introduced.")

        # Contradiction overlap (0.2 weight)
        if expected_contradictions:
            contradiction_overlap = set_overlap_score(
                expected_contradictions, actual_contradictions
            )
            builder.add(
                0.2,
                contradiction_overlap,
                "Contradictions match the representative verification trace."
                if contradiction_overlap >= 0.75
                else "Contradictions missed important representative conflicts.",
            )
        elif not actual_contradictions:
            builder.add(0.2, 1.0, "No contradictions were expected or introduced.")

        # Verified summary presence + boundedness (0.2 weight)
        if verified_summary:
            builder.add(0.15, 1.0, "Verified summary is present.")
            longest_input = max(
                (len(str(item or "")) for item in bounded_outputs), default=0
            )
            if longest_input <= 0 or len(verified_summary) <= max(
                240, longest_input * 2
            ):
                builder.add(
                    0.05,
                    1.0,
                    "Verified summary stays bounded relative to the inputs.",
                )
            else:
                builder.add(
                    0.05,
                    0.0,
                    "Verified summary is too verbose for bounded recursive handoff.",
                )
        else:
            builder.add(0.15, 0.0, "Verified summary is empty.")

        # Rationale (0.05 weight)
        builder.add(
            0.05,
            text_presence_score(verification_rationale),
            "Verification rationale explains the recursive decision signal."
            if verification_rationale
            else "Verification rationale is empty; explain why the aggregate is or is not sufficient.",
        )

        return builder.build()

    return metric


# -- Artifact path (backward compat) ----------------------------------------


def resolve_recursive_verification_output_path(output_path: Path | None = None) -> Path:
    """Resolve the default artifact path for optimized recursive verification prompts."""
    return resolve_artifact_path(_MODULE_SLUG, _ARTIFACT_FILENAME, output_path)


# -- Optimization entrypoint -------------------------------------------------


def optimize_recursive_verification_module(
    *,
    dataset_path: Path,
    output_path: Path | None = None,
    train_ratio: float = 0.8,
    auto: Literal["light", "medium", "heavy"] | None = "light",
) -> OptimizationResult:
    """Run GEPA offline against the recursive verification module."""
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
        description="Optimize the recursive verification DSPy module offline with GEPA."
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
    result = optimize_recursive_verification_module(
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
    label="Verification",
    program_spec=_PROGRAM_SPEC,
    artifact_filename=_ARTIFACT_FILENAME,
    input_keys=list(_INPUT_KEYS),
    required_dataset_keys=list(_REQUIRED_DATASET_KEYS),
    module_factory=VerifyRecursiveAggregationModule,
    row_converter=rows_to_recursive_verification_examples,
    metric_builder=build_recursive_verification_feedback_metric,
    metric_name="recursive_verification_quality_and_boundedness",
    description="Improves output verification by evolving instructions for checking task completion criteria and detecting partial or incorrect results before finalizing.",
)

register_module(_MODULE_SPEC)
