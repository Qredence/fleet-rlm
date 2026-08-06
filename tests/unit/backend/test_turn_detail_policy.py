"""Sole successful RLM outcome to Committed Turn policy."""

from __future__ import annotations

from uuid import uuid4

import pytest


def test_commit_success_normalizes_details_and_appends_the_canonical_suffix() -> None:
    from fleet_rlm.artifacts.models import ArtifactRef
    from fleet_rlm.chat.turn_detail_policy import commit_success
    from fleet_rlm.rlm.dspy_contract import PredictionResult
    from fleet_rlm.rlm.events import RLMReasoning, StepFinished, StepStarted, ToolCompleted, ToolStarted
    from fleet_rlm.rlm.outcome import RLMOutcome

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
    from fleet_rlm.chat.turn_detail_policy import commit_success
    from fleet_rlm.rlm.dspy_contract import PredictionResult
    from fleet_rlm.rlm.events import RLMOutput
    from fleet_rlm.rlm.outcome import RLMOutcome
    from fleet_rlm.sessions.committed_turn import OutputPart

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


def test_commit_omits_structured_duplicate_for_single_output_prediction() -> None:
    from fleet_rlm.chat.turn_detail_policy import commit_success
    from fleet_rlm.rlm.dspy_contract import PredictionResult
    from fleet_rlm.rlm.outcome import RLMOutcome

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
    from fleet_rlm.chat.turn_detail_policy import TurnDetailPolicyError, commit_success
    from fleet_rlm.rlm.dspy_contract import PredictionResult
    from fleet_rlm.rlm.events import ToolStarted
    from fleet_rlm.rlm.outcome import RLMOutcome

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
