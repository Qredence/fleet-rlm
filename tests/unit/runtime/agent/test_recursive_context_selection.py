from __future__ import annotations

from typing import Any

from fleet_rlm.runtime.agent.recursive_context_selection import (
    AssembleRecursiveWorkspaceContextModule,
    build_recursive_context_selection_inputs,
    coerce_recursive_context_selection_decision,
    materialize_recursive_context,
)


def test_context_selection_module_coerces_bounded_outputs() -> None:
    class _FakePredictor:
        def __call__(self, **_kwargs: Any) -> Any:
            return {
                "selected_memory_handles": [
                    "memory_handle=meta/workspaces/ws-1/users/u/session.json",
                    "missing=handle",
                ],
                "selected_evidence_ids": "trajectory,missing",
                "assembled_context_summary": "x" * 2000,
                "omission_rationale": "",
            }

    module = AssembleRecursiveWorkspaceContextModule(predictor=_FakePredictor())

    prediction = module(
        user_request="repair the failing workspace step",
        current_plan="Inspect the latest failure and retry narrowly.",
        loop_state="recursion_depth=1",
        working_memory_catalog=[
            "memory_handle=meta/workspaces/ws-1/users/u/session.json | Durable memory handle.",
            "workspace_path=/workspace/repo | Active workspace.",
        ],
        recent_sandbox_evidence_catalog=[
            "trajectory | {'steps': [{'thought': 'inspect'}]}",
            "final_reasoning | A repairable syntax error was found.",
        ],
        latest_tool_or_code_result="SyntaxError: invalid syntax",
        context_budget=600,
    )

    assert prediction.selected_memory_handles == [
        "memory_handle=meta/workspaces/ws-1/users/u/session.json"
    ]
    assert prediction.selected_evidence_ids == ["trajectory"]
    assert len(prediction.assembled_context_summary) == 600
    assert prediction.omission_rationale


def test_context_selection_module_leaves_invalid_selections_empty() -> None:
    class _FakePredictor:
        def __call__(self, **_kwargs: Any) -> Any:
            return {
                "selected_memory_handles": ["missing=handle"],
                "selected_evidence_ids": "missing",
                "assembled_context_summary": "",
                "omission_rationale": "",
            }

    module = AssembleRecursiveWorkspaceContextModule(predictor=_FakePredictor())

    prediction = module(
        user_request="repair the failing workspace step",
        current_plan="Inspect the latest failure and retry narrowly.",
        loop_state="recursion_depth=1",
        working_memory_catalog=[
            "memory_handle=meta/workspaces/ws-1/users/u/session.json | Durable memory handle.",
            "workspace_path=/workspace/repo | Active workspace.",
        ],
        recent_sandbox_evidence_catalog=[
            "trajectory | {'steps': [{'thought': 'inspect'}]}",
            "final_reasoning | A repairable syntax error was found.",
        ],
        latest_tool_or_code_result="SyntaxError: invalid syntax",
        context_budget=600,
    )

    assert prediction.selected_memory_handles == []
    assert prediction.selected_evidence_ids == []
    assert (
        "Latest result: SyntaxError: invalid syntax"
        in prediction.assembled_context_summary
    )


def test_build_recursive_context_selection_inputs_stays_summary_only() -> None:
    inputs = build_recursive_context_selection_inputs(
        user_request="Investigate the workspace",
        current_plan="Inspect the failing command and repair it.",
        latest_result={
            "answer": "x" * 1200,
            "trajectory": {"trajectory": [{"thought": "inspect"}]},
            "final_reasoning": "Found a likely syntax error in the generated code.",
        },
        runtime_metadata={
            "volume_name": "tenant-a",
            "workspace_path": "/workspace/repo",
            "sandbox_id": "sbx-123",
            "memory_handle": "meta/workspaces/tenant-a/users/u/react-session.json",
            "memory_blob": "SECRET" * 500,
        },
        recursion_depth=1,
        max_depth=3,
        reflection_passes=0,
        fallback_used=False,
        context_budget=700,
        interpreter_context_paths=["src/fleet_rlm/runtime/agent"],
    )

    assert "volume_name=tenant-a" in inputs.working_memory_catalog[0]
    assert any(
        "memory_handle=meta/workspaces/tenant-a/users/u/react-session.json" in entry
        for entry in inputs.working_memory_catalog
    )
    assert all("SECRET" not in entry for entry in inputs.working_memory_catalog)
    assert inputs.latest_tool_or_code_result.endswith("...")
    assert inputs.context_budget == 700


def test_materialize_recursive_context_uses_selected_items_only() -> None:
    inputs = build_recursive_context_selection_inputs(
        user_request="Investigate the workspace",
        current_plan="Inspect the failing command and repair it.",
        latest_result={
            "answer": "pytest failed",
            "final_reasoning": "The traceback points at one file.",
            "trajectory": {"trajectory": [{"thought": "inspect"}]},
        },
        runtime_metadata={
            "volume_name": "tenant-a",
            "workspace_path": "/workspace/repo",
            "memory_handle": "meta/workspaces/tenant-a/users/u/react-session.json",
        },
        recursion_depth=1,
        max_depth=3,
        reflection_passes=0,
        fallback_used=False,
        context_budget=900,
        interpreter_context_paths=["src/fleet_rlm/runtime/agent"],
    )

    selected = materialize_recursive_context(
        inputs=inputs,
        decision=coerce_recursive_context_selection_decision(
            {
                "selected_memory_handles": [
                    "memory_handle=meta/workspaces/tenant-a/users/u/react-session.json"
                ],
                "selected_evidence_ids": ["final_reasoning"],
                "assembled_context_summary": "Use the bounded memory handle and the traceback summary only.",
                "omission_rationale": "Skip unrelated workspace handles and verbose traces.",
            },
            working_memory_catalog=inputs.working_memory_catalog,
            recent_sandbox_evidence_catalog=inputs.recent_sandbox_evidence_catalog,
            latest_tool_or_code_result=inputs.latest_tool_or_code_result,
            context_budget=inputs.context_budget,
        ),
    )

    assert "memory_handle=meta/workspaces/tenant-a/users/u/react-session.json" in (
        selected.working_memory_summary
    )
    assert "workspace_path=/workspace/repo" not in selected.retry_context
    assert (
        "final_reasoning | The traceback points at one file." in selected.retry_context
    )
    assert "trajectory |" not in selected.retry_context
