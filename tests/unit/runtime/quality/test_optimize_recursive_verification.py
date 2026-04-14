from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import dspy

from fleet_rlm.runtime.quality.optimize_recursive_verification import (
    build_recursive_verification_feedback_metric,
    optimize_recursive_verification_module,
    rows_to_recursive_verification_examples,
)


def _dataset_row() -> dict[str, Any]:
    return {
        "user_request": "Repair the recursive workspace step",
        "assembled_recursive_context": "Only the bounded traceback summary is available.",
        "decomposition_plan_summary": "fan_out with Python/runtime aggregation.",
        "collected_subquery_outputs": [
            "[1] Inspect traceback\nanswer=ImportError in one file",
            "[2] Repair import path\nanswer=Updated the import path",
        ],
        "latest_sandbox_evidence": "workspace_path=/workspace/repo",
        "verification_status": "needs_repair",
        "missing_evidence": ["Run one bounded verification step."],
        "contradictions": [],
        "verified_summary": "The repair path is plausible but still needs one bounded verification step.",
        "verification_rationale": "The aggregate is coherent, but one important confirmation is missing.",
    }


def test_rows_to_recursive_verification_examples_preserves_typed_inputs() -> None:
    row = _dataset_row()
    examples = rows_to_recursive_verification_examples([row])

    assert len(examples) == 1
    assert examples[0].verification_status == row["verification_status"]
    assert dict(examples[0].inputs()) == {
        "user_request": row["user_request"],
        "assembled_recursive_context": row["assembled_recursive_context"],
        "decomposition_plan_summary": row["decomposition_plan_summary"],
        "collected_subquery_outputs": row["collected_subquery_outputs"],
        "latest_sandbox_evidence": row["latest_sandbox_evidence"],
    }


def test_optimize_recursive_verification_module_runs_gepa_and_persists_artifacts(
    monkeypatch,
    tmp_path: Path,
) -> None:
    dataset_path = tmp_path / "recursive-verification.json"
    dataset_path.write_text(
        json.dumps([_dataset_row(), _dataset_row()]),
        encoding="utf-8",
    )
    output_path = tmp_path / "optimized" / "recursive-verification.json"

    captured: dict[str, Any] = {}

    class _FakeOptimizedProgram(dspy.Module):
        def save(self, path: str) -> None:
            Path(path).write_text('{"optimized": true}\n', encoding="utf-8")

        def forward(self, **kwargs: Any) -> Any:
            return MagicMock()

    class _FakeGEPA:
        def __init__(
            self,
            *,
            metric: Any,
            auto: str | None,
            reflection_lm: Any = None,
            **kwargs: Any,
        ) -> None:
            captured["metric"] = metric
            captured["auto"] = auto

        def compile(
            self,
            program: dspy.Module,
            *,
            trainset: list[Any],
            valset: list[Any] | None,
        ) -> _FakeOptimizedProgram:
            captured["program"] = program
            captured["trainset"] = trainset
            captured["valset"] = valset
            return _FakeOptimizedProgram()

    monkeypatch.setattr(
        "dspy.teleprompt.GEPA",
        _FakeGEPA,
    )
    monkeypatch.setattr(
        "fleet_rlm.runtime.quality.optimization_runner._resolve_reflection_lm",
        lambda: MagicMock(),
    )
    monkeypatch.setattr(
        "fleet_rlm.runtime.quality.optimization_runner._ensure_dspy_configured",
        lambda: None,
    )
    per_example_scores = [{"example_index": 0, "input_data": "{}", "score": 0.87}]
    monkeypatch.setattr(
        "fleet_rlm.runtime.quality.optimization_runner._evaluate_per_example",
        lambda *_args, **_kwargs: per_example_scores,
    )
    evaluate_mock = MagicMock(
        side_effect=AssertionError("Aggregate fallback should not run")
    )
    monkeypatch.setattr("dspy.Evaluate", evaluate_mock)

    result = optimize_recursive_verification_module(
        dataset_path=dataset_path,
        output_path=output_path,
        auto="medium",
    )

    manifest_path = output_path.with_suffix(".manifest.json")
    assert output_path.exists()
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["dataset_path"] == str(dataset_path)
    assert manifest["optimizer"] == "GEPA"
    assert manifest["metric"] == "recursive_verification_quality_and_boundedness"
    assert result["output_path"] == str(output_path)
    assert result["validation_score"] == 0.87
    assert result["program_spec"].endswith("VerifyRecursiveAggregationModule")
    assert captured["auto"] == "medium"
    assert captured["trainset"]
    assert captured["valset"]
    evaluate_mock.assert_not_called()


def test_recursive_verification_feedback_metric_scores_quality_and_boundedness() -> (
    None
):
    metric = build_recursive_verification_feedback_metric()
    gold = dspy.Example(**_dataset_row())
    pred = dspy.Example(
        verification_status=gold.verification_status,
        missing_evidence=gold.missing_evidence,
        contradictions=gold.contradictions,
        verified_summary=gold.verified_summary,
        verification_rationale=gold.verification_rationale,
    )

    result = metric(gold, pred)

    assert result.score == 1.0
    assert "stays bounded relative to the inputs" in result.feedback
