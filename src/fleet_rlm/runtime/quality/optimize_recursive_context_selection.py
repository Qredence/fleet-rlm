"""Offline GEPA optimization entrypoint for recursive context selection."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Literal

import dspy

from fleet_rlm.runtime.agent.recursive_context_selection import (
    AssembleRecursiveWorkspaceContextModule,
)

from .artifacts import resolve_artifact_path
from .datasets import load_dataset_rows, validate_required_keys
from .module_registry import ModuleOptimizationSpec, register_module
from .optimization_runner import OptimizationResult, run_module_optimization
from .scoring_helpers import (
    ScoreFeedbackBuilder,
    boundedness_score,
    set_overlap_score,
    text_presence_score,
)

# -- Module-specific constants -----------------------------------------------

RecursiveContextSelectionRow = dict[str, Any]
_INPUT_KEYS = [
    "user_request",
    "current_plan",
    "loop_state",
    "working_memory_catalog",
    "recent_sandbox_evidence_catalog",
    "latest_tool_or_code_result",
    "context_budget",
]
_REQUIRED_DATASET_KEYS = [
    *_INPUT_KEYS,
    "selected_memory_handles",
    "selected_evidence_ids",
]
_MODULE_SLUG = "context-selection"
_ARTIFACT_FILENAME = "assemble_recursive_workspace_context.json"
_PROGRAM_SPEC = (
    "fleet_rlm.runtime.agent.recursive_context_selection:"
    "AssembleRecursiveWorkspaceContextModule"
)


# -- Dataset conversion (module-specific) ------------------------------------


def load_recursive_context_selection_rows(
    dataset_path: Path,
) -> list[RecursiveContextSelectionRow]:
    """Load a JSON or JSONL dataset of representative recursive context traces."""
    return load_dataset_rows(dataset_path)


def rows_to_recursive_context_selection_examples(
    rows: list[RecursiveContextSelectionRow],
) -> list[dspy.Example]:
    """Convert representative recursive context traces into DSPy examples."""
    valid = validate_required_keys(rows, _REQUIRED_DATASET_KEYS, "Context selection")
    examples: list[dspy.Example] = []
    for row in valid:
        example = dspy.Example(
            user_request=str(row.get("user_request", "") or ""),
            current_plan=str(row.get("current_plan", "") or ""),
            loop_state=str(row.get("loop_state", "") or ""),
            working_memory_catalog=[
                str(item or "") for item in row.get("working_memory_catalog", []) or []
            ],
            recent_sandbox_evidence_catalog=[
                str(item or "")
                for item in row.get("recent_sandbox_evidence_catalog", []) or []
            ],
            latest_tool_or_code_result=str(
                row.get("latest_tool_or_code_result", "") or ""
            ),
            context_budget=int(row.get("context_budget", 0) or 0),
            selected_memory_handles=[
                str(item or "") for item in row.get("selected_memory_handles", []) or []
            ],
            selected_evidence_ids=[
                str(item or "") for item in row.get("selected_evidence_ids", []) or []
            ],
            assembled_context_summary=str(
                row.get("assembled_context_summary", "") or ""
            ),
            omission_rationale=str(row.get("omission_rationale", "") or ""),
        ).with_inputs(*_INPUT_KEYS)
        examples.append(example)
    if not examples:
        raise ValueError(
            "No valid recursive context selection examples were found in the dataset."
        )
    return examples


# -- Metric (module-specific) ------------------------------------------------


def build_recursive_context_selection_feedback_metric() -> Any:
    """Build a GEPA metric for recursive context relevance and boundedness."""
    from dspy.teleprompt.gepa.gepa_utils import ScoreWithFeedback

    def metric(
        gold: Any,
        pred: Any,
        trace: Any = None,
        pred_name: str | None = None,
        pred_trace: Any = None,
    ) -> ScoreWithFeedback:
        _ = trace, pred_name, pred_trace
        expected_memory = set(getattr(gold, "selected_memory_handles", []) or [])
        actual_memory = set(getattr(pred, "selected_memory_handles", []) or [])
        expected_evidence = set(getattr(gold, "selected_evidence_ids", []) or [])
        actual_evidence = set(getattr(pred, "selected_evidence_ids", []) or [])
        actual_summary = str(
            getattr(pred, "assembled_context_summary", "") or ""
        ).strip()
        expected_budget = int(getattr(gold, "context_budget", 0) or 0)
        omission_rationale = str(getattr(pred, "omission_rationale", "") or "").strip()

        builder = ScoreFeedbackBuilder()

        # Memory handle overlap (0.35 weight)
        if expected_memory:
            mem_overlap = set_overlap_score(expected_memory, actual_memory)
            builder.add(
                0.35,
                mem_overlap,
                "Selected memory handles match the representative trace."
                if mem_overlap >= 0.75
                else "Memory-handle selection missed relevant Daytona-backed state.",
            )

        # Evidence overlap (0.35 weight)
        if expected_evidence:
            ev_overlap = set_overlap_score(expected_evidence, actual_evidence)
            builder.add(
                0.35,
                ev_overlap,
                "Selected evidence ids match the representative trace."
                if ev_overlap >= 0.75
                else "Evidence selection missed relevant recent sandbox/code results.",
            )

        # Summary presence (0.15 weight)
        builder.add(
            0.15,
            text_presence_score(actual_summary),
            "Assembled context summary is present."
            if actual_summary
            else "Assembled context summary is empty.",
        )

        # Budget boundedness (0.1 weight)
        if actual_summary and expected_budget > 0:
            builder.add(
                0.1,
                boundedness_score(len(actual_summary), expected_budget),
                "Summary stays within the requested context budget."
                if len(actual_summary) <= expected_budget
                else f"Summary exceeded the requested context budget of {expected_budget} characters.",
            )

        # Omission rationale (0.05 weight)
        builder.add(
            0.05,
            text_presence_score(omission_rationale),
            "Omission rationale explains what was left out."
            if omission_rationale
            else "Omission rationale is empty; explain how context size was controlled.",
        )

        return builder.build()

    return metric


# -- Artifact path (backward compat) ----------------------------------------


def resolve_recursive_context_selection_output_path(
    output_path: Path | None = None,
) -> Path:
    """Resolve the default artifact path for optimized recursive context prompts."""
    return resolve_artifact_path(_MODULE_SLUG, _ARTIFACT_FILENAME, output_path)


# -- Optimization entrypoint -------------------------------------------------


def optimize_recursive_context_selection_module(
    *,
    dataset_path: Path,
    output_path: Path | None = None,
    train_ratio: float = 0.8,
    auto: Literal["light", "medium", "heavy"] | None = "light",
) -> OptimizationResult:
    """Run GEPA offline against the recursive context-selection module."""
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
        description="Optimize the recursive context-selection DSPy module offline with GEPA."
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
    result = optimize_recursive_context_selection_module(
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
    label="Context Selection",
    program_spec=_PROGRAM_SPEC,
    artifact_filename=_ARTIFACT_FILENAME,
    input_keys=list(_INPUT_KEYS),
    required_dataset_keys=list(_REQUIRED_DATASET_KEYS),
    module_factory=AssembleRecursiveWorkspaceContextModule,
    row_converter=rows_to_recursive_context_selection_examples,
    metric_builder=build_recursive_context_selection_feedback_metric,
    metric_name="recursive_context_relevance_and_boundedness",
)

register_module(_MODULE_SPEC)
