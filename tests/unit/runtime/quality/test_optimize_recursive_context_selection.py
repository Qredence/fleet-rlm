from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import dspy

from fleet_rlm.runtime.quality.optimize_recursive_context_selection import (
    optimize_recursive_context_selection_module,
    rows_to_recursive_context_selection_examples,
)


def _dataset_row() -> dict[str, Any]:
    return {
        "user_request": "Repair the failing recursive workspace step",
        "current_plan": "Inspect the latest failing command.",
        "loop_state": "recursion_depth=1; max_depth=3",
        "working_memory_catalog": [
            "memory_handle=meta/workspaces/ws-1/users/u/react-session.json | Durable memory handle.",
            "workspace_path=/workspace/repo | Active workspace path.",
        ],
        "recent_sandbox_evidence_catalog": [
            "final_reasoning | The traceback points to one syntax error.",
            "trajectory | {'steps': [{'thought': 'inspect'}]}",
        ],
        "latest_tool_or_code_result": "SyntaxError: invalid syntax",
        "context_budget": 800,
        "selected_memory_handles": [
            "memory_handle=meta/workspaces/ws-1/users/u/react-session.json"
        ],
        "selected_evidence_ids": ["final_reasoning"],
        "assembled_context_summary": "Keep only the durable memory handle and traceback summary.",
        "omission_rationale": "Drop broader workspace state to stay bounded.",
    }


def test_rows_to_recursive_context_selection_examples_preserves_typed_inputs() -> None:
    row = _dataset_row()
    examples = rows_to_recursive_context_selection_examples([row])

    assert len(examples) == 1
    assert examples[0].selected_memory_handles == row["selected_memory_handles"]
    assert dict(examples[0].inputs()) == {
        "user_request": row["user_request"],
        "current_plan": row["current_plan"],
        "loop_state": row["loop_state"],
        "working_memory_catalog": row["working_memory_catalog"],
        "recent_sandbox_evidence_catalog": row["recent_sandbox_evidence_catalog"],
        "latest_tool_or_code_result": row["latest_tool_or_code_result"],
        "context_budget": row["context_budget"],
    }


def test_optimize_recursive_context_selection_module_runs_gepa_and_persists_artifacts(
    monkeypatch,
    tmp_path: Path,
) -> None:
    dataset_path = tmp_path / "recursive-context.json"
    dataset_path.write_text(
        json.dumps([_dataset_row(), _dataset_row()]), encoding="utf-8"
    )
    output_path = tmp_path / "optimized" / "recursive-context.json"

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
            return 91.0

    monkeypatch.setattr(
        "fleet_rlm.runtime.quality.optimize_recursive_context_selection.GEPA",
        _FakeGEPA,
    )
    monkeypatch.setattr(
        "fleet_rlm.runtime.quality.optimize_recursive_context_selection.dspy.Evaluate",
        _FakeEvaluate,
    )

    result = optimize_recursive_context_selection_module(
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
    assert manifest["metric"] == "recursive_context_relevance_and_boundedness"
    assert result["output_path"] == str(output_path)
    assert result["validation_score"] == 91.0
    assert result["program_spec"].endswith("AssembleRecursiveWorkspaceContextModule")
    assert captured["auto"] == "medium"
    assert captured["trainset"]
    assert captured["valset"]
