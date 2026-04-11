"""Offline GEPA optimization entrypoint for recursive aggregation verification."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Literal

import dspy
from dspy.teleprompt import GEPA
from dspy.teleprompt.gepa.gepa import ScoreWithFeedback

from fleet_rlm.runtime.agent.recursive_verification import (
    VerifyRecursiveAggregationModule,
)
from fleet_rlm.runtime.agent.signatures import VerifyRecursiveAggregation

from .mlflow_optimization import split_examples

RecursiveVerificationRow = dict[str, Any]
_INPUT_KEYS = [
    "user_request",
    "assembled_recursive_context",
    "decomposition_plan_summary",
    "collected_subquery_outputs",
    "latest_sandbox_evidence",
]
_DEFAULT_ARTIFACT_ROOT = Path(".data/quality-artifacts/recursive-verification")
_DAYTONA_ARTIFACT_ROOT = Path(
    "/home/daytona/memory/artifacts/quality/recursive-verification"
)


def load_recursive_verification_rows(
    dataset_path: Path,
) -> list[RecursiveVerificationRow]:
    """Load a JSON or JSONL dataset of representative recursive verification traces."""

    text = dataset_path.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError("Recursive verification dataset is empty.")
    if dataset_path.suffix == ".jsonl":
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    payload = json.loads(text)
    if isinstance(payload, list):
        return payload
    raise ValueError(
        "Expected a JSON array or JSONL file of recursive verification examples."
    )


def rows_to_recursive_verification_examples(
    rows: list[RecursiveVerificationRow],
) -> list[dspy.Example]:
    """Convert representative verification traces into DSPy examples."""

    examples: list[dspy.Example] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if any(key not in row for key in (*_INPUT_KEYS, "verification_status")):
            continue
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


def build_recursive_verification_feedback_metric() -> Any:
    """Build a GEPA metric for verification quality, boundedness, and usefulness."""

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

        score = 0.0
        feedback: list[str] = []

        if actual_status == expected_status:
            score += 0.35
            feedback.append("Verification status matches the representative trace.")
        else:
            feedback.append(
                f"Expected verification_status={expected_status!r} but received {actual_status!r}."
            )

        if expected_missing:
            missing_overlap = len(expected_missing & actual_missing) / len(
                expected_missing
            )
            score += 0.2 * missing_overlap
            feedback.append(
                "Missing-evidence selection preserves the representative gaps."
                if missing_overlap >= 0.75
                else "Missing-evidence selection missed important representative gaps."
            )
        elif not actual_missing:
            score += 0.2
            feedback.append("No missing evidence was expected or introduced.")

        if expected_contradictions:
            contradiction_overlap = len(
                expected_contradictions & actual_contradictions
            ) / len(expected_contradictions)
            score += 0.2 * contradiction_overlap
            feedback.append(
                "Contradictions match the representative verification trace."
                if contradiction_overlap >= 0.75
                else "Contradictions missed important representative conflicts."
            )
        elif not actual_contradictions:
            score += 0.2
            feedback.append("No contradictions were expected or introduced.")

        if verified_summary:
            score += 0.15
            feedback.append("Verified summary is present.")
            longest_input = max(
                (len(str(item or "")) for item in bounded_outputs), default=0
            )
            if longest_input <= 0 or len(verified_summary) <= max(
                240, longest_input * 2
            ):
                score += 0.05
                feedback.append(
                    "Verified summary stays bounded relative to the inputs."
                )
            else:
                feedback.append(
                    "Verified summary is too verbose for bounded recursive handoff."
                )
        else:
            feedback.append("Verified summary is empty.")

        if verification_rationale:
            score += 0.05
            feedback.append(
                "Verification rationale explains the recursive decision signal."
            )
        else:
            feedback.append(
                "Verification rationale is empty; explain why the aggregate is or is not sufficient."
            )

        return ScoreWithFeedback(
            score=max(0.0, min(1.0, score)),
            feedback=" ".join(feedback),
        )

    return metric


def resolve_recursive_verification_output_path(output_path: Path | None = None) -> Path:
    """Resolve the default artifact path for optimized recursive verification prompts."""

    if output_path is not None:
        return output_path
    root = (
        _DAYTONA_ARTIFACT_ROOT
        if _DAYTONA_ARTIFACT_ROOT.exists()
        else _DEFAULT_ARTIFACT_ROOT
    )
    return root / "verify_recursive_aggregation.json"


def optimize_recursive_verification_module(
    *,
    dataset_path: Path,
    output_path: Path | None = None,
    train_ratio: float = 0.8,
    auto: Literal["light", "medium", "heavy"] | None = "light",
) -> dict[str, Any]:
    """Run GEPA offline against the recursive verification module."""

    examples = rows_to_recursive_verification_examples(
        load_recursive_verification_rows(dataset_path)
    )
    trainset, valset = split_examples(examples, train_ratio=train_ratio)
    metric = build_recursive_verification_feedback_metric()
    program = VerifyRecursiveAggregationModule()
    optimizer = GEPA(metric=metric, auto=auto)
    optimized = optimizer.compile(program, trainset=trainset, valset=valset or None)

    validation_score = None
    if valset:
        validation_score = float(dspy.Evaluate(devset=valset, metric=metric)(optimized))

    resolved_output_path = resolve_recursive_verification_output_path(output_path)
    resolved_output_path.parent.mkdir(parents=True, exist_ok=True)
    optimized.save(str(resolved_output_path))

    manifest_path = resolved_output_path.with_suffix(".manifest.json")
    manifest_path.write_text(
        json.dumps(
            {
                "dataset_path": str(dataset_path),
                "module": f"{VerifyRecursiveAggregation.__module__}:"
                f"{VerifyRecursiveAggregation.__name__}",
                "train_examples": len(trainset),
                "validation_examples": len(valset),
                "validation_score": validation_score,
                "optimizer": "GEPA",
                "metric": "recursive_verification_quality_and_boundedness",
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
            "fleet_rlm.runtime.agent.recursive_verification:"
            "VerifyRecursiveAggregationModule"
        ),
    }


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
