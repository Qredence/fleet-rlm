"""Offline GEPA optimization entrypoint for recursive context selection."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Literal

import dspy
from dspy.teleprompt import GEPA
from dspy.teleprompt.gepa.gepa import ScoreWithFeedback

from fleet_rlm.runtime.agent.recursive_context_selection import (
    AssembleRecursiveWorkspaceContextModule,
)
from fleet_rlm.runtime.agent.signatures import AssembleRecursiveWorkspaceContext
from .mlflow_optimization import split_examples

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
_DEFAULT_ARTIFACT_ROOT = Path(".data/quality-artifacts/recursive-context-selection")
_DAYTONA_ARTIFACT_ROOT = Path(
    "/home/daytona/memory/artifacts/quality/recursive-context-selection"
)


def load_recursive_context_selection_rows(
    dataset_path: Path,
) -> list[RecursiveContextSelectionRow]:
    """Load a JSON or JSONL dataset of representative recursive context traces."""

    text = dataset_path.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError("Recursive context selection dataset is empty.")
    if dataset_path.suffix == ".jsonl":
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    payload = json.loads(text)
    if isinstance(payload, list):
        return payload
    raise ValueError(
        "Expected a JSON array or JSONL file of recursive context selection examples."
    )


def rows_to_recursive_context_selection_examples(
    rows: list[RecursiveContextSelectionRow],
) -> list[dspy.Example]:
    """Convert representative recursive context traces into DSPy examples."""

    examples: list[dspy.Example] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if any(
            key not in row
            for key in (*_INPUT_KEYS, "selected_memory_handles", "selected_evidence_ids")
        ):
            continue
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


def build_recursive_context_selection_feedback_metric() -> Any:
    """Build a GEPA metric for recursive context relevance and boundedness."""

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
        actual_summary = str(getattr(pred, "assembled_context_summary", "") or "").strip()
        expected_budget = int(getattr(gold, "context_budget", 0) or 0)
        omission_rationale = str(getattr(pred, "omission_rationale", "") or "").strip()

        score = 0.0
        feedback: list[str] = []

        if expected_memory:
            memory_overlap = len(expected_memory & actual_memory) / len(expected_memory)
            score += 0.35 * memory_overlap
            if memory_overlap >= 0.75:
                feedback.append("Selected memory handles match the representative trace.")
            else:
                feedback.append("Memory-handle selection missed relevant Daytona-backed state.")

        if expected_evidence:
            evidence_overlap = len(expected_evidence & actual_evidence) / len(
                expected_evidence
            )
            score += 0.35 * evidence_overlap
            if evidence_overlap >= 0.75:
                feedback.append("Selected evidence ids match the representative trace.")
            else:
                feedback.append("Evidence selection missed relevant recent sandbox/code results.")

        if actual_summary:
            score += 0.15
            feedback.append("Assembled context summary is present.")
            if expected_budget > 0 and len(actual_summary) <= expected_budget:
                score += 0.1
                feedback.append("Summary stays within the requested context budget.")
            elif expected_budget > 0:
                feedback.append(
                    f"Summary exceeded the requested context budget of {expected_budget} characters."
                )
        else:
            feedback.append("Assembled context summary is empty.")

        if omission_rationale:
            score += 0.05
            feedback.append("Omission rationale explains what was left out.")
        else:
            feedback.append("Omission rationale is empty; explain how context size was controlled.")

        return ScoreWithFeedback(
            score=max(0.0, min(1.0, score)),
            feedback=" ".join(feedback),
        )

    return metric


def resolve_recursive_context_selection_output_path(
    output_path: Path | None = None,
) -> Path:
    """Resolve the default artifact path for optimized recursive context prompts."""

    if output_path is not None:
        return output_path
    root = (
        _DAYTONA_ARTIFACT_ROOT
        if _DAYTONA_ARTIFACT_ROOT.exists()
        else _DEFAULT_ARTIFACT_ROOT
    )
    return root / "assemble_recursive_workspace_context.json"


def optimize_recursive_context_selection_module(
    *,
    dataset_path: Path,
    output_path: Path | None = None,
    train_ratio: float = 0.8,
    auto: Literal["light", "medium", "heavy"] | None = "light",
) -> dict[str, Any]:
    """Run GEPA offline against the recursive context-selection module."""

    examples = rows_to_recursive_context_selection_examples(
        load_recursive_context_selection_rows(dataset_path)
    )
    trainset, valset = split_examples(examples, train_ratio=train_ratio)
    metric = build_recursive_context_selection_feedback_metric()
    program = AssembleRecursiveWorkspaceContextModule()
    optimizer = GEPA(metric=metric, auto=auto)
    optimized = optimizer.compile(program, trainset=trainset, valset=valset or None)

    validation_score = None
    if valset:
        validation_score = float(dspy.Evaluate(devset=valset, metric=metric)(optimized))

    resolved_output_path = resolve_recursive_context_selection_output_path(output_path)
    resolved_output_path.parent.mkdir(parents=True, exist_ok=True)
    optimized.save(str(resolved_output_path))

    manifest_path = resolved_output_path.with_suffix(".manifest.json")
    manifest_path.write_text(
        json.dumps(
            {
                "dataset_path": str(dataset_path),
                "module": f"{AssembleRecursiveWorkspaceContext.__module__}:"
                f"{AssembleRecursiveWorkspaceContext.__name__}",
                "train_examples": len(trainset),
                "validation_examples": len(valset),
                "validation_score": validation_score,
                "optimizer": "GEPA",
                "metric": "recursive_context_relevance_and_boundedness",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return {
        "train_examples": len(trainset),
        "validation_examples": len(valset),
        "validation_score": validation_score,
        "output_path": str(resolved_output_path),
        "manifest_path": str(manifest_path),
        "optimizer": "GEPA",
        "program_spec": (
            "fleet_rlm.runtime.agent.recursive_context_selection:"
            "AssembleRecursiveWorkspaceContextModule"
        ),
    }


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
