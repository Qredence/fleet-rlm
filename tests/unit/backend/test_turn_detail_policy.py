"""Sole successful RLM outcome to Committed Turn policy."""

from __future__ import annotations

from uuid import uuid4

import pytest


def test_commit_success_normalizes_details_and_appends_the_canonical_suffix() -> None:
    from fleet_rlm.artifacts.models import ArtifactRef
    from fleet_rlm.chat.turn_detail_policy import commit_success
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
        text="42",
        usage={"iterations": 1},
        structured_output={"total": 42},
        result_schema_id="analysis",
        result_schema_version="1",
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
    assert committed.structured_result == {"total": 42}


def test_commit_success_rejects_failed_outcomes_or_unmatched_tool_calls() -> None:
    from fleet_rlm.chat.turn_detail_policy import TurnDetailPolicyError, commit_success
    from fleet_rlm.rlm.events import ToolStarted
    from fleet_rlm.rlm.outcome import RLMOutcome

    with pytest.raises(TurnDetailPolicyError):
        commit_success(RLMOutcome(terminal_status="failed"), ())
    with pytest.raises(TurnDetailPolicyError):
        commit_success(
            RLMOutcome(
                terminal_status="completed",
                execution_details=(ToolStarted(tool_call_id="call-1", tool_name="lookup", input={}),),
            ),
            (),
        )
