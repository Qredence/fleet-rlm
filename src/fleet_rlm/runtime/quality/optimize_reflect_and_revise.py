"""Offline GEPA optimization entrypoint for the recursive reflection module."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Literal

import dspy
from dspy.teleprompt import GEPA
from dspy.teleprompt.gepa.gepa import ScoreWithFeedback

from fleet_rlm.runtime.agent.recursive_reflection import (
    ReflectAndReviseWorkspaceStepModule,
)
from fleet_rlm.runtime.agent.signatures import ReflectAndReviseWorkspaceStep
from .mlflow_optimization import split_examples

ReflectionOptimizationRow = dict[str, Any]
_INPUT_KEYS = [
    "user_request",
    "working_memory_summary",
    "current_plan",
    "latest_sandbox_evidence",
    "latest_tool_or_code_result",
    "loop_state",
]
_DEFAULT_ARTIFACT_ROOT = Path(".data/quality-artifacts/reflect-and-revise")
_DAYTONA_ARTIFACT_ROOT = Path("/home/daytona/memory/artifacts/quality/reflect-and-revise")


def load_reflection_rows(dataset_path: Path) -> list[ReflectionOptimizationRow]:
    """Load a JSON or JSONL dataset of representative recursive reflection traces."""

    text = dataset_path.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError("Reflection dataset is empty.")
    if dataset_path.suffix == ".jsonl":
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    payload = json.loads(text)
    if isinstance(payload, list):
        return payload
    raise ValueError("Expected a JSON array or JSONL file of reflection examples.")


def rows_to_reflection_examples(
    rows: list[ReflectionOptimizationRow],
) -> list[dspy.Example]:
    """Convert representative reflection traces into DSPy examples."""

    examples: list[dspy.Example] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if any(key not in row for key in (*_INPUT_KEYS, "next_action")):
            continue
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


def build_reflection_feedback_metric() -> Any:
    """Build a GEPA metric for recursive revise/recurse/finalize decisions."""

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

        score = 0.0
        feedback: list[str] = []
        if actual_action == expected_action:
            score += 0.7
            feedback.append("Next action matches the representative recursive trace.")
        else:
            feedback.append(
                f"Expected next_action={expected_action!r} but received {actual_action!r}."
            )

        if expected_plan:
            overlap = sum(token in actual_plan for token in expected_plan.split())
            plan_score = overlap / max(1, len(expected_plan.split()))
            score += 0.2 * min(1.0, plan_score)
            if plan_score >= 0.5:
                feedback.append("Revised plan preserves key repair/recurse guidance.")
            else:
                feedback.append("Revised plan misses important guidance from the trace.")
        elif actual_plan:
            score += 0.1
            feedback.append("Revised plan is present for an open-ended trace.")

        if rationale:
            score += 0.1
            feedback.append("Rationale is present for operator review.")
        else:
            feedback.append("Rationale is empty; explain why the worker should branch.")

        return ScoreWithFeedback(
            score=max(0.0, min(1.0, score)),
            feedback=" ".join(feedback),
        )

    return metric


def resolve_reflection_output_path(output_path: Path | None = None) -> Path:
    """Resolve the default artifact path for optimized recursive reflection prompts."""

    if output_path is not None:
        return output_path
    root = _DAYTONA_ARTIFACT_ROOT if _DAYTONA_ARTIFACT_ROOT.exists() else _DEFAULT_ARTIFACT_ROOT
    return root / "reflect_and_revise_workspace_step.json"


def optimize_reflect_and_revise_module(
    *,
    dataset_path: Path,
    output_path: Path | None = None,
    train_ratio: float = 0.8,
    auto: Literal["light", "medium", "heavy"] | None = "light",
) -> dict[str, Any]:
    """Run GEPA offline against the recursive reflection module."""

    examples = rows_to_reflection_examples(load_reflection_rows(dataset_path))
    trainset, valset = split_examples(examples, train_ratio=train_ratio)
    metric = build_reflection_feedback_metric()
    program = ReflectAndReviseWorkspaceStepModule()
    optimizer = GEPA(metric=metric, auto=auto)
    optimized = optimizer.compile(program, trainset=trainset, valset=valset or None)

    validation_score = None
    if valset:
        validation_score = float(dspy.Evaluate(devset=valset, metric=metric)(optimized))

    resolved_output_path = resolve_reflection_output_path(output_path)
    resolved_output_path.parent.mkdir(parents=True, exist_ok=True)
    optimized.save(str(resolved_output_path))

    manifest_path = resolved_output_path.with_suffix(".manifest.json")
    manifest_path.write_text(
        json.dumps(
            {
                "dataset_path": str(dataset_path),
                "module": f"{ReflectAndReviseWorkspaceStep.__module__}:"
                f"{ReflectAndReviseWorkspaceStep.__name__}",
                "train_examples": len(trainset),
                "validation_examples": len(valset),
                "validation_score": validation_score,
                "optimizer": "GEPA",
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
            "fleet_rlm.runtime.agent.recursive_reflection:"
            "ReflectAndReviseWorkspaceStepModule"
        ),
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Optimize the recursive reflection DSPy module offline with GEPA."
    )
    parser.add_argument("dataset_path", type=Path, help="Path to JSON or JSONL examples.")
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
