from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import dspy

from fleet_rlm.runtime.quality.optimize_reflect_and_revise import (
    optimize_reflect_and_revise_module,
    rows_to_reflection_examples,
)


def _dataset_row() -> dict[str, Any]:
    return {
        "user_request": "Investigate the failing workspace step",
        "working_memory_summary": "volume_name=tenant-a",
        "current_plan": "Inspect the latest failing command.",
        "latest_sandbox_evidence": "pytest reported one syntax error.",
        "latest_tool_or_code_result": "SyntaxError: invalid syntax",
        "loop_state": "recursion_depth=1; max_depth=3",
        "next_action": "repair_and_retry",
        "revised_plan": "Fix the syntax error and rerun the narrow failing step.",
        "rationale": "The sandbox evidence points to a single recoverable defect.",
        "confidence": 0.9,
    }


def test_rows_to_reflection_examples_preserves_typed_inputs() -> None:
    row = _dataset_row()
    examples = rows_to_reflection_examples([row])

    assert len(examples) == 1
    assert examples[0].next_action == "repair_and_retry"
    assert examples[0].inputs().toDict() == {
        "user_request": row["user_request"],
        "working_memory_summary": row["working_memory_summary"],
        "current_plan": row["current_plan"],
        "latest_sandbox_evidence": row["latest_sandbox_evidence"],
        "latest_tool_or_code_result": row["latest_tool_or_code_result"],
        "loop_state": row["loop_state"],
    }


def test_optimize_reflect_and_revise_module_runs_gepa_and_persists_artifacts(
    monkeypatch,
    tmp_path: Path,
) -> None:
    dataset_path = tmp_path / "reflect.json"
    dataset_path.write_text(
        json.dumps([_dataset_row(), _dataset_row()]), encoding="utf-8"
    )
    output_path = tmp_path / "optimized" / "reflect.json"

    captured: dict[str, Any] = {}

    class _FakeOptimizedProgram(dspy.Module):
        def save(self, path: str) -> None:
            Path(path).write_text('{"optimized": true}\n', encoding="utf-8")

    class _FakeGEPA:
        def __init__(self, *, metric: Any, auto: str | None) -> None:
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
            return 88.0

    monkeypatch.setattr(
        "fleet_rlm.runtime.quality.optimize_reflect_and_revise.GEPA",
        _FakeGEPA,
    )
    monkeypatch.setattr(
        "fleet_rlm.runtime.quality.optimize_reflect_and_revise.dspy.Evaluate",
        _FakeEvaluate,
    )

    result = optimize_reflect_and_revise_module(
        dataset_path=dataset_path,
        output_path=output_path,
        auto="medium",
    )

    assert output_path.exists()
    manifest_path = output_path.with_suffix(".manifest.json")
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["dataset_path"] == str(dataset_path)
    assert manifest["optimizer"] == "GEPA"
    assert result["output_path"] == str(output_path)
    assert result["validation_score"] == 88.0
    assert result["program_spec"].endswith("ReflectAndReviseWorkspaceStepModule")
    assert captured["auto"] == "medium"
    assert captured["trainset"]
    assert captured["valset"]
