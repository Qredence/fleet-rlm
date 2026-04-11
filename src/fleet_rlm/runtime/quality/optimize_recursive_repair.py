"""Offline GEPA optimization entrypoint for recursive repair planning."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Literal

import dspy
from dspy.teleprompt import GEPA
from dspy.teleprompt.gepa.gepa import ScoreWithFeedback

from fleet_rlm.runtime.agent.recursive_repair import PlanRecursiveRepairModule
from fleet_rlm.runtime.agent.signatures import PlanRecursiveRepair

from .mlflow_optimization import split_examples

RecursiveRepairRow = dict[str, Any]
_INPUT_KEYS = [
    "user_request",
    "assembled_recursive_context",
    "verification_summary",
    "latest_sandbox_evidence",
    "latest_failure_signals",
    "repair_budget",
]
_DEFAULT_ARTIFACT_ROOT = Path(".data/quality-artifacts/recursive-repair")
_DAYTONA_ARTIFACT_ROOT = Path("/home/daytona/memory/artifacts/quality/recursive-repair")


def load_recursive_repair_rows(dataset_path: Path) -> list[RecursiveRepairRow]:
    """Load a JSON or JSONL dataset of representative recursive repair traces."""

    text = dataset_path.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError("Recursive repair dataset is empty.")
    if dataset_path.suffix == ".jsonl":
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    payload = json.loads(text)
    if isinstance(payload, list):
        return payload
    raise ValueError(
        "Expected a JSON array or JSONL file of recursive repair examples."
    )


def rows_to_recursive_repair_examples(
    rows: list[RecursiveRepairRow],
) -> list[dspy.Example]:
    """Convert representative repair traces into DSPy examples."""

    examples: list[dspy.Example] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if any(key not in row for key in (*_INPUT_KEYS, "repair_mode")):
            continue
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


def build_recursive_repair_feedback_metric() -> Any:
    """Build a GEPA metric for repair usefulness, boundedness, and success potential."""

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

        score = 0.0
        feedback: list[str] = []

        if actual_mode == expected_mode:
            score += 0.35
            feedback.append("Repair mode matches the representative trace.")
        else:
            feedback.append(
                f"Expected repair_mode={expected_mode!r} but received {actual_mode!r}."
            )

        if expected_steps:
            step_overlap = len(expected_steps & actual_steps) / len(expected_steps)
            score += 0.2 * step_overlap
            feedback.append(
                "Repair steps preserve the representative bounded repair sequence."
                if step_overlap >= 0.75
                else "Repair steps missed important representative repair actions."
            )
        elif not actual_steps:
            score += 0.2
            feedback.append("No repair steps were expected or introduced.")

        if expected_subqueries:
            subquery_overlap = len(expected_subqueries & actual_subqueries) / len(
                expected_subqueries
            )
            score += 0.15 * subquery_overlap
            feedback.append(
                "Repair subqueries preserve the representative bounded delegation plan."
                if subquery_overlap >= 0.75
                else "Repair subqueries missed important representative delegated checks."
            )
        elif not actual_subqueries:
            score += 0.15
            feedback.append("No repair subqueries were expected or introduced.")

        bounded_count = max(len(actual_steps), len(actual_subqueries))
        if bounded_count > 0:
            if repair_budget > 0 and bounded_count <= max(1, repair_budget + 1):
                score += 0.15
                feedback.append(
                    "Repair plan stays within the requested bounded budget."
                )
            else:
                feedback.append("Repair plan exceeded the requested bounded budget.")
        else:
            feedback.append("Repair plan is empty.")

        if repair_target:
            score += 0.1
            feedback.append("Repair target is present.")
        else:
            feedback.append("Repair target is empty.")

        if repair_rationale:
            score += 0.05
            feedback.append(
                "Repair rationale explains why the plan should stay narrow."
            )
        else:
            feedback.append("Repair rationale is empty.")

        return ScoreWithFeedback(
            score=max(0.0, min(1.0, score)),
            feedback=" ".join(feedback),
        )

    return metric


def resolve_recursive_repair_output_path(output_path: Path | None = None) -> Path:
    """Resolve the default artifact path for optimized recursive repair prompts."""

    if output_path is not None:
        return output_path
    root = (
        _DAYTONA_ARTIFACT_ROOT
        if _DAYTONA_ARTIFACT_ROOT.exists()
        else _DEFAULT_ARTIFACT_ROOT
    )
    return root / "plan_recursive_repair.json"


def optimize_recursive_repair_module(
    *,
    dataset_path: Path,
    output_path: Path | None = None,
    train_ratio: float = 0.8,
    auto: Literal["light", "medium", "heavy"] | None = "light",
) -> dict[str, Any]:
    """Run GEPA offline against the recursive repair module."""

    examples = rows_to_recursive_repair_examples(
        load_recursive_repair_rows(dataset_path)
    )
    trainset, valset = split_examples(examples, train_ratio=train_ratio)
    metric = build_recursive_repair_feedback_metric()
    program = PlanRecursiveRepairModule()
    optimizer = GEPA(metric=metric, auto=auto)
    optimized = optimizer.compile(program, trainset=trainset, valset=valset or None)

    validation_score = None
    if valset:
        validation_score = float(dspy.Evaluate(devset=valset, metric=metric)(optimized))

    resolved_output_path = resolve_recursive_repair_output_path(output_path)
    resolved_output_path.parent.mkdir(parents=True, exist_ok=True)
    optimized.save(str(resolved_output_path))

    manifest_path = resolved_output_path.with_suffix(".manifest.json")
    manifest_path.write_text(
        json.dumps(
            {
                "dataset_path": str(dataset_path),
                "module": f"{PlanRecursiveRepair.__module__}:{PlanRecursiveRepair.__name__}",
                "train_examples": len(trainset),
                "validation_examples": len(valset),
                "validation_score": validation_score,
                "optimizer": "GEPA",
                "metric": "recursive_repair_usefulness_and_boundedness",
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
        "program_spec": "fleet_rlm.runtime.agent.recursive_repair:PlanRecursiveRepairModule",
    }


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
