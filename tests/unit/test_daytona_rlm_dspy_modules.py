from __future__ import annotations

from types import SimpleNamespace

import pytest

from fleet_rlm.daytona_rlm.dspy_modules import (
    ChildResultSynthesisModule,
    RecursiveSpawnPolicyModule,
    RecursiveTaskDecompositionModule,
    RecursiveSpawnPolicyDecision,
    SynthesizedChildResult,
    parse_recursive_task_specs,
)


def test_parse_recursive_task_specs_dedupes_by_task_and_source() -> None:
    tasks = parse_recursive_task_specs(
        [
            {
                "task": "Inspect README",
                "source": {
                    "kind": "file_slice",
                    "path": "README.md",
                    "start_line": 1,
                    "end_line": 2,
                },
            },
            {
                "task": "Inspect README",
                "source": {
                    "kind": "file_slice",
                    "path": "README.md",
                    "start_line": 1,
                    "end_line": 2,
                },
            },
            {
                "task": "Inspect pyproject",
                "source": {
                    "kind": "file_slice",
                    "path": "pyproject.toml",
                    "start_line": 1,
                    "end_line": 3,
                },
            },
        ]
    )

    assert [task.task for task in tasks] == ["Inspect README", "Inspect pyproject"]


def test_parse_recursive_task_specs_returns_empty_for_invalid_json() -> None:
    assert parse_recursive_task_specs("not json") == []
    assert parse_recursive_task_specs({"unexpected": "shape"}) == []


def test_recursive_task_decomposition_module_degrades_to_empty_tasks_on_bad_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakePredict:
        def __init__(self, signature):
            self.signature = signature

        def __call__(self, **kwargs):
            _ = kwargs
            return SimpleNamespace(
                child_tasks_json="not valid json",
                decision_summary="No safe decomposition.",
            )

    monkeypatch.setattr(
        "fleet_rlm.daytona_rlm.dspy_modules.dspy.Predict",
        _FakePredict,
    )

    module = RecursiveTaskDecompositionModule()
    decision = module(
        parent_task="Inspect repo",
        latest_observation="Need more detail.",
        workspace_context_summary="Repository: example",
        existing_child_tasks_json="[]",
        budget_json='{"remaining_sandboxes": 2}',
    )

    assert decision.tasks == []
    assert decision.decision_summary == "No safe decomposition."


def test_recursive_spawn_policy_module_parses_spawn_decision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakePredict:
        def __init__(self, signature):
            self.signature = signature

        def __call__(self, **kwargs):
            _ = kwargs
            return SimpleNamespace(
                should_spawn="true",
                recommended_fanout="3",
                rationale="The observation surfaced three distinct workstreams.",
            )

    monkeypatch.setattr(
        "fleet_rlm.daytona_rlm.dspy_modules.dspy.Predict",
        _FakePredict,
    )

    module = RecursiveSpawnPolicyModule()
    decision = module(
        parent_task="Inspect repo",
        latest_observation="Need deeper evidence.",
        workspace_context_summary="Repository: example",
        existing_child_tasks_json="[]",
        budget_json='{"remaining_sandboxes": 4}',
    )

    assert isinstance(decision, RecursiveSpawnPolicyDecision)
    assert decision.should_spawn is True
    assert decision.recommended_fanout == 3
    assert decision.rationale == "The observation surfaced three distinct workstreams."


def test_recursive_spawn_policy_module_degrades_to_no_spawn_on_bad_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakePredict:
        def __init__(self, signature):
            self.signature = signature

        def __call__(self, **kwargs):
            _ = kwargs
            return SimpleNamespace(
                should_spawn="definitely",
                recommended_fanout="not a number",
                rationale="Malformed output.",
            )

    monkeypatch.setattr(
        "fleet_rlm.daytona_rlm.dspy_modules.dspy.Predict",
        _FakePredict,
    )

    module = RecursiveSpawnPolicyModule()
    decision = module(
        parent_task="Inspect repo",
        latest_observation="Need deeper evidence.",
        workspace_context_summary="Repository: example",
        existing_child_tasks_json="[]",
        budget_json='{"remaining_sandboxes": 4}',
    )

    assert decision.should_spawn is False
    assert decision.recommended_fanout == 0
    assert decision.rationale == "Malformed output."


def test_child_result_synthesis_module_uses_preview_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakePredict:
        def __init__(self, signature):
            self.signature = signature

        def __call__(self, **kwargs):
            _ = kwargs
            return SimpleNamespace(
                answer_markdown="Synthesized child answer.",
                result_preview="",
                evidence_json='[{"path":"README.md","start_line":1,"end_line":2}]',
                follow_up_needed="false",
                confidence="0.65",
            )

    monkeypatch.setattr(
        "fleet_rlm.daytona_rlm.dspy_modules.dspy.Predict",
        _FakePredict,
    )

    module = ChildResultSynthesisModule()
    result = module(
        parent_task="Inspect repo",
        child_task_json='{"task":"Inspect README"}',
        child_result_text="Raw child result.",
        child_evidence_json='[{"path":"README.md"}]',
        child_status="completed",
        child_summary_json='{"termination_reason":"completed"}',
    )

    assert isinstance(result, SynthesizedChildResult)
    assert result.answer_markdown == "Synthesized child answer."
    assert result.result_preview == "Synthesized child answer."
    assert result.evidence == [{"path": "README.md", "start_line": 1, "end_line": 2}]
    assert result.follow_up_needed is False
    assert result.confidence == pytest.approx(0.65)
