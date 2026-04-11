"""Offline GEPA optimization entrypoint for recursive decomposition."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Literal

import dspy
from dspy.teleprompt import GEPA
from dspy.teleprompt.gepa.gepa import ScoreWithFeedback

from fleet_rlm.runtime.agent.recursive_decomposition import PlanRecursiveSubqueriesModule
from fleet_rlm.runtime.agent.signatures import PlanRecursiveSubqueries

from .mlflow_optimization import split_examples

RecursiveDecompositionRow = dict[str, Any]
_INPUT_KEYS = [
    "user_request",
    "assembled_recursive_context",
    "current_plan",
    "loop_state",
    "latest_sandbox_evidence",
    "subquery_budget",
]
_DEFAULT_ARTIFACT_ROOT = Path(".data/quality-artifacts/recursive-decomposition")
_DAYTONA_ARTIFACT_ROOT = Path(
    "/home/daytona/memory/artifacts/quality/recursive-decomposition"
)


def load_recursive_decomposition_rows(
    dataset_path: Path,
) -> list[RecursiveDecompositionRow]:
    """Load a JSON or JSONL dataset of representative recursive decomposition traces."""

    text = dataset_path.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError("Recursive decomposition dataset is empty.")
    if dataset_path.suffix == ".jsonl":
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    payload = json.loads(text)
    if isinstance(payload, list):
        return payload
    raise ValueError(
        "Expected a JSON array or JSONL file of recursive decomposition examples."
    )


def rows_to_recursive_decomposition_examples(
    rows: list[RecursiveDecompositionRow],
) -> list[dspy.Example]:
    """Convert representative recursive decomposition traces into DSPy examples."""

    examples: list[dspy.Example] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if any(
            key not in row
            for key in (*_INPUT_KEYS, "decomposition_mode", "subqueries")
        ):
            continue
        example = dspy.Example(
            user_request=str(row.get("user_request", "") or ""),
            assembled_recursive_context=str(
                row.get("assembled_recursive_context", "") or ""
            ),
            current_plan=str(row.get("current_plan", "") or ""),
            loop_state=str(row.get("loop_state", "") or ""),
            latest_sandbox_evidence=str(row.get("latest_sandbox_evidence", "") or ""),
            subquery_budget=int(row.get("subquery_budget", 0) or 0),
            decomposition_mode=str(row.get("decomposition_mode", "") or ""),
            subqueries=[str(item or "") for item in row.get("subqueries", []) or []],
            batching_strategy=str(row.get("batching_strategy", "") or ""),
            aggregation_plan=str(row.get("aggregation_plan", "") or ""),
            decomposition_rationale=str(row.get("decomposition_rationale", "") or ""),
        ).with_inputs(*_INPUT_KEYS)
        examples.append(example)
    if not examples:
        raise ValueError(
            "No valid recursive decomposition examples were found in the dataset."
        )
    return examples


def build_recursive_decomposition_feedback_metric() -> Any:
    """Build a GEPA metric for decomposition quality, boundedness, and usefulness."""

    def metric(
        gold: Any,
        pred: Any,
        trace: Any = None,
        pred_name: str | None = None,
        pred_trace: Any = None,
    ) -> ScoreWithFeedback:
        _ = trace, pred_name, pred_trace
        expected_mode = str(getattr(gold, "decomposition_mode", "single_pass") or "")
        actual_mode = str(getattr(pred, "decomposition_mode", "single_pass") or "")
        expected_subqueries = [
            str(item or "") for item in (getattr(gold, "subqueries", []) or [])
        ]
        actual_subqueries = [
            str(item or "") for item in (getattr(pred, "subqueries", []) or [])
        ]
        subquery_budget = int(getattr(gold, "subquery_budget", 0) or 0)
        aggregation_plan = str(getattr(pred, "aggregation_plan", "") or "").strip()
        rationale = str(getattr(pred, "decomposition_rationale", "") or "").strip()

        score = 0.0
        feedback: list[str] = []

        if actual_mode == expected_mode:
            score += 0.3
            feedback.append("Decomposition mode matches the representative trace.")
        else:
            feedback.append(
                f"Expected decomposition_mode={expected_mode!r} but received {actual_mode!r}."
            )

        if expected_subqueries:
            overlap = len(set(expected_subqueries) & set(actual_subqueries)) / len(
                expected_subqueries
            )
            score += 0.35 * overlap
            if overlap >= 0.75:
                feedback.append("Subqueries preserve the representative decomposition.")
            else:
                feedback.append(
                    "Subqueries missed relevant bounded subproblems from the trace."
                )

        if actual_subqueries:
            score += 0.15
            feedback.append("At least one bounded subquery is present.")
            if subquery_budget > 0 and len(actual_subqueries) <= subquery_budget:
                score += 0.1
                feedback.append("Subqueries stay within the requested bounded budget.")
            elif subquery_budget > 0:
                feedback.append(
                    f"Subqueries exceeded the requested budget of {subquery_budget}."
                )
        else:
            feedback.append("Subqueries are empty.")

        if aggregation_plan:
            score += 0.05
            feedback.append("Aggregation plan is present for Python/runtime execution.")
        else:
            feedback.append("Aggregation plan is empty.")

        if rationale:
            score += 0.05
            feedback.append("Decomposition rationale explains the semantic split.")
        else:
            feedback.append("Decomposition rationale is empty.")

        return ScoreWithFeedback(
            score=max(0.0, min(1.0, score)),
            feedback=" ".join(feedback),
        )

    return metric


def resolve_recursive_decomposition_output_path(
    output_path: Path | None = None,
) -> Path:
    """Resolve the default artifact path for optimized recursive decomposition prompts."""

    if output_path is not None:
        return output_path
    root = (
        _DAYTONA_ARTIFACT_ROOT
        if _DAYTONA_ARTIFACT_ROOT.exists()
        else _DEFAULT_ARTIFACT_ROOT
    )
    return root / "plan_recursive_subqueries.json"


def optimize_recursive_decomposition_module(
    *,
    dataset_path: Path,
    output_path: Path | None = None,
    train_ratio: float = 0.8,
    auto: Literal["light", "medium", "heavy"] | None = "light",
) -> dict[str, Any]:
    """Run GEPA offline against the recursive decomposition module."""

    examples = rows_to_recursive_decomposition_examples(
        load_recursive_decomposition_rows(dataset_path)
    )
    trainset, valset = split_examples(examples, train_ratio=train_ratio)
    metric = build_recursive_decomposition_feedback_metric()
    program = PlanRecursiveSubqueriesModule()
    optimizer = GEPA(metric=metric, auto=auto)
    optimized = optimizer.compile(program, trainset=trainset, valset=valset or None)

    validation_score = None
    if valset:
        validation_score = float(dspy.Evaluate(devset=valset, metric=metric)(optimized))

    resolved_output_path = resolve_recursive_decomposition_output_path(output_path)
    resolved_output_path.parent.mkdir(parents=True, exist_ok=True)
    optimized.save(str(resolved_output_path))

    manifest_path = resolved_output_path.with_suffix(".manifest.json")
    manifest_path.write_text(
        json.dumps(
            {
                "dataset_path": str(dataset_path),
                "module": f"{PlanRecursiveSubqueries.__module__}:"
                f"{PlanRecursiveSubqueries.__name__}",
                "train_examples": len(trainset),
                "validation_examples": len(valset),
                "validation_score": validation_score,
                "optimizer": "GEPA",
                "metric": "recursive_decomposition_quality_and_boundedness",
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
            "fleet_rlm.runtime.agent.recursive_decomposition:"
            "PlanRecursiveSubqueriesModule"
        ),
    }


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
