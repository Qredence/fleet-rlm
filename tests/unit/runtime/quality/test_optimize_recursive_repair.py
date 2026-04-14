from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import dspy

from fleet_rlm.runtime.quality.optimize_recursive_repair import (
    build_recursive_repair_feedback_metric,
    optimize_recursive_repair_module,
    rows_to_recursive_repair_examples,
)


def _dataset_row() -> dict[str, Any]:
    return {
        "user_request": "Repair the recursive workspace step",
        "assembled_recursive_context": "Only the bounded traceback summary is available.",
        "verification_summary": "Verification says the import path repair is plausible but unconfirmed.",
        "latest_sandbox_evidence": "workspace_path=/workspace/repo",
        "latest_failure_signals": "missing_evidence=Run one bounded verification step.",
        "repair_budget": 2,
        "repair_mode": "bounded_repair_loop",
        "repair_target": "Repair the failing import path.",
        "repair_steps": [
            "Inspect the failing import path.",
            "Patch only the broken import.",
            "Run one bounded verification rerun.",
        ],
        "repair_subqueries": [
            "Inspect the failing import path",
            "Run one bounded verification rerun",
        ],
        "repair_rationale": "Use the narrow traceback evidence before broader recursion.",
    }


def test_rows_to_recursive_repair_examples_preserves_typed_inputs() -> None:
    row = _dataset_row()
    examples = rows_to_recursive_repair_examples([row])

    assert len(examples) == 1
    assert examples[0].repair_mode == row["repair_mode"]
    assert dict(examples[0].inputs()) == {
        "user_request": row["user_request"],
        "assembled_recursive_context": row["assembled_recursive_context"],
        "verification_summary": row["verification_summary"],
        "latest_sandbox_evidence": row["latest_sandbox_evidence"],
        "latest_failure_signals": row["latest_failure_signals"],
        "repair_budget": row["repair_budget"],
    }


def test_optimize_recursive_repair_module_runs_gepa_and_persists_artifacts(
    monkeypatch,
    tmp_path: Path,
) -> None:
    dataset_path = tmp_path / "recursive-repair.json"
    dataset_path.write_text(
        json.dumps([_dataset_row(), _dataset_row()]), encoding="utf-8"
    )
    output_path = tmp_path / "optimized" / "recursive-repair.json"

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

    class _FakeEvaluate:
        def __init__(self, *, devset: list[Any], metric: Any) -> None:
            captured["devset"] = devset
            captured["eval_metric"] = metric

        def __call__(self, program: dspy.Module) -> float:
            captured["evaluated_program"] = program
            return 86.0

    monkeypatch.setattr(
        "dspy.teleprompt.GEPA",
        _FakeGEPA,
    )
    monkeypatch.setattr(
        "dspy.Evaluate",
        _FakeEvaluate,
    )
    monkeypatch.setattr(
        "fleet_rlm.runtime.quality.optimization_runner._resolve_reflection_lm",
        lambda: MagicMock(),
    )
    monkeypatch.setattr(
        "fleet_rlm.runtime.quality.optimization_runner._ensure_dspy_configured",
        lambda: None,
    )
    monkeypatch.setattr(
        "fleet_rlm.runtime.quality.optimization_runner._evaluate_per_example",
        lambda _module, valset, _metric: [
            {
                "example_index": i,
                "input_data": "{}",
                "expected_output": "",
                "predicted_output": "",
                "score": 1.0,
            }
            for i in range(len(valset))
        ],
    )

    result = optimize_recursive_repair_module(
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
    assert manifest["metric"] == "recursive_repair_usefulness_and_boundedness"
    assert result["output_path"] == str(output_path)
    assert result["validation_score"] == 1.0
    assert result["program_spec"].endswith("PlanRecursiveRepairModule")
    assert captured["auto"] == "medium"
    assert captured["trainset"]
    assert captured["valset"]


def test_recursive_repair_feedback_metric_scores_usefulness_and_boundedness() -> None:
    metric = build_recursive_repair_feedback_metric()
    gold = dspy.Example(**_dataset_row())
    pred = dspy.Example(
        repair_mode=gold.repair_mode,
        repair_target=gold.repair_target,
        repair_steps=gold.repair_steps,
        repair_subqueries=gold.repair_subqueries,
        repair_rationale=gold.repair_rationale,
    )

    result = metric(gold, pred)

    assert result.score == 1.0
    assert "stays within the requested bounded budget" in result.feedback
