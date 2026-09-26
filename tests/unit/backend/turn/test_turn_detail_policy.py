"""Sole successful RLM outcome to Committed Turn policy."""

from __future__ import annotations

from uuid import uuid4

import pytest


def test_commit_success_normalizes_details_and_appends_the_canonical_suffix() -> None:
    from fleet_rlm.artifacts.models import ArtifactRef
    from fleet_rlm.rlm.events import RLMReasoning, StepFinished, StepStarted, ToolCompleted, ToolStarted
    from fleet_rlm.rlm.result import PredictionResult, RLMOutcome
    from fleet_rlm.sessions.turn_detail_policy import commit_success

    artifact = ArtifactRef(
        uuid4(),
        uuid4(),
        uuid4(),
        "json",
        "result",
        "application/json",
        2,
        "a" * 64,
    )
    outcome = RLMOutcome(
        terminal_status="completed",
        prediction=PredictionResult("42", {"answer": "42", "total": 42}, "analysis", "1"),
        usage={"iterations": 1, "observed_lm_usage": {}, "duration_ms": 3},
        execution_details=(
            StepStarted(step=1),
            RLMReasoning(text="bounded", step=1),
            ToolStarted(tool_call_id="call-1", tool_name="lookup", input={"q": "x"}),
            ToolCompleted(tool_call_id="call-1", tool_name="lookup", output={"ok": True}),
            StepFinished(step=1, duration_ms=3),
        ),
    )

    committed = commit_success(outcome, (artifact,))

    assert [part.type for part in committed.parts] == [
        "step",
        "reasoning",
        "tool_call",
        "step",
        "artifact",
        "usage",
        "structured_result",
        "text",
    ]
    assert committed.text == "42"
    assert committed.structured_result == {"answer": "42", "total": 42}


def test_commit_success_coalesces_incremental_output_before_durable_commit() -> None:
    from fleet_rlm.rlm.events import RLMOutput
    from fleet_rlm.rlm.result import PredictionResult, RLMOutcome
    from fleet_rlm.sessions.committed_turn import OutputPart
    from fleet_rlm.sessions.turn_detail_policy import commit_success

    committed = commit_success(
        RLMOutcome(
            terminal_status="completed",
            prediction=PredictionResult("done", {"answer": "done"}, "default", "1"),
            execution_details=(
                RLMOutput("first", 1, "output-1", True, False),
                RLMOutput(" second", 1, "output-1", True, False),
                RLMOutput("first second", 1, "output-1", False, True),
            ),
        ),
        (),
    )

    outputs = [part for part in committed.parts if isinstance(part, OutputPart)]
    assert outputs == [OutputPart(output="first second", step=1)]


def test_commit_persists_only_the_latest_terminal_child_progress() -> None:
    from fleet_rlm.rlm.events import ChildProgress
    from fleet_rlm.rlm.result import PredictionResult, RLMOutcome
    from fleet_rlm.sessions.committed_turn import ChildProgressPart
    from fleet_rlm.sessions.turn_detail_policy import commit_success

    committed = commit_success(
        RLMOutcome(
            terminal_status="completed",
            prediction=PredictionResult("done", {"answer": "done"}, "default", "1"),
            execution_details=(
                ChildProgress("root:call-1", "Inspect code", "running", 10),
                ChildProgress(
                    "root:call-1",
                    "Inspect code",
                    "completed",
                    42,
                    "Reviewed two files",
                    "complete",
                    "run-9",
                    evidence=("src/api.py:42",),
                    gaps=("Caller not checked",),
                    result_file_count=2,
                ),
            ),
        ),
        (),
    )

    children = [part for part in committed.parts if isinstance(part, ChildProgressPart)]
    assert children == [
        ChildProgressPart(
            "root:call-1",
            "Inspect code",
            "completed",
            42,
            "Reviewed two files",
            "complete",
            "run-9",
            evidence=("src/api.py:42",),
            gaps=("Caller not checked",),
            result_file_count=2,
        )
    ]


def test_commit_persists_not_started_child_admission_refusal() -> None:
    from fleet_rlm.rlm.events import ChildProgress
    from fleet_rlm.rlm.result import PredictionResult, RLMOutcome
    from fleet_rlm.sessions.committed_turn import ChildProgressPart
    from fleet_rlm.sessions.turn_detail_policy import commit_success

    committed = commit_success(
        RLMOutcome(
            terminal_status="completed",
            prediction=PredictionResult("done", {"answer": "done"}, "default", "1"),
            execution_details=(
                ChildProgress(
                    "root:call-2",
                    "Inspect large input",
                    "not_started",
                    0,
                    "Child admission budget exhausted",
                    "not_required",
                ),
            ),
        ),
        (),
    )

    assert (
        ChildProgressPart(
            "root:call-2",
            "Inspect large input",
            "not_started",
            0,
            "Child admission budget exhausted",
            "not_required",
        )
        in committed.parts
    )


def test_commit_omits_structured_duplicate_for_single_output_prediction() -> None:
    from fleet_rlm.rlm.result import PredictionResult, RLMOutcome
    from fleet_rlm.sessions.turn_detail_policy import commit_success

    committed = commit_success(
        RLMOutcome(
            terminal_status="completed",
            prediction=PredictionResult("done", {"answer": "done"}, "default", "1"),
        ),
        (),
    )

    assert [part.type for part in committed.parts] == ["usage", "text"]
    assert committed.structured_result is None


def test_commit_success_rejects_failed_outcomes_or_unmatched_tool_calls() -> None:
    from fleet_rlm.rlm.events import ToolStarted
    from fleet_rlm.rlm.result import PredictionResult, RLMOutcome
    from fleet_rlm.sessions.turn_detail_policy import TurnDetailPolicyError, commit_success

    with pytest.raises(TurnDetailPolicyError):
        commit_success(RLMOutcome(terminal_status="failed"), ())
    with pytest.raises(TurnDetailPolicyError, match="tool call start has no terminal observation"):
        commit_success(
            RLMOutcome(
                terminal_status="completed",
                prediction=PredictionResult("done", {"answer": "done"}, "default", "1"),
                execution_details=(ToolStarted(tool_call_id="call-1", tool_name="lookup", input={}),),
            ),
            (),
        )


def test_commit_success_normalizes_guard_closed_no_progress_tool_call() -> None:
    """RC-2: ToolStarted closed by the guard's ToolFailed commits as failed."""
    import dspy

    from fleet_rlm.rlm.events import (
        ToolCompleted,
        ToolEventView,
        ToolFailed,
        ToolStarted,
        observe_tool,
    )
    from fleet_rlm.rlm.execution import RunToolGuards
    from fleet_rlm.rlm.result import PredictionResult, RLMOutcome, RunNoProgressError
    from fleet_rlm.sessions.committed_turn import ToolCallPart
    from fleet_rlm.sessions.turn_detail_policy import commit_success

    observed: list[object] = []
    wrapped = observe_tool(
        dspy.Tool(lambda query: f"result for {query}", name="lookup"),
        observed.append,
        ToolEventView.metadata_only(),
        guards=RunToolGuards(),
    )
    assert wrapped(query="repeat") == "result for repeat"
    with pytest.raises(RunNoProgressError):
        wrapped(query="repeat")

    execution_details = tuple(item for item in observed if isinstance(item, (ToolStarted, ToolCompleted, ToolFailed)))
    committed = commit_success(
        RLMOutcome(
            terminal_status="completed",
            prediction=PredictionResult("done", {"answer": "done"}, "default", "1"),
            execution_details=execution_details,
        ),
        (),
    )

    tools = [part for part in committed.parts if isinstance(part, ToolCallPart)]
    assert [part.state for part in tools] == ["completed", "failed"]
    assert tools[1].tool_name == "lookup"
    assert tools[1].tool_call_id != tools[0].tool_call_id
    assert tools[1].error == "repeated tool call produced no progress"
