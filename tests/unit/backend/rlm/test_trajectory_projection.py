"""RLM trajectory normalization and event reconciliation."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import dspy
import pytest


def test_trajectory_normalization_is_strict_and_preserves_absent_fields() -> None:
    from fleet_rlm.rlm.dspy_contract import PredictionOutputError, normalize_prediction_trajectory
    from fleet_rlm.rlm.events import RLMCode, RLMOutput, RLMReasoning, StepFinished, StepStarted
    from fleet_rlm.rlm.runner import _trajectory_details

    with pytest.raises(PredictionOutputError):
        normalize_prediction_trajectory(SimpleNamespace())
    with pytest.raises(PredictionOutputError):
        normalize_prediction_trajectory(SimpleNamespace(trajectory="malformed"))
    with pytest.raises(PredictionOutputError):
        normalize_prediction_trajectory(SimpleNamespace(trajectory=[None]))
    with pytest.raises(PredictionOutputError):
        normalize_prediction_trajectory(SimpleNamespace(trajectory=[{"code": 1}]))

    steps = normalize_prediction_trajectory(SimpleNamespace(trajectory=[{"reasoning": "usable"}]))
    assert steps[0].reasoning == "usable"
    assert steps[0].code == ""
    assert steps[0].output == ""
    assert [type(item) for item in _trajectory_details(steps, max_chars=100)] == [
        StepStarted,
        RLMReasoning,
        RLMCode,
        RLMOutput,
        StepFinished,
    ]


def test_trajectory_semantic_details_are_verbatim_and_share_the_run_bound() -> None:
    from fleet_rlm.rlm.dspy_contract import normalize_prediction_trajectory
    from fleet_rlm.rlm.events import RLMCode, RLMOutput, RLMReasoning
    from fleet_rlm.rlm.runner import _trajectory_details

    semantic = "api_key=visible-user-text /Users/example BEGIN SYSTEM"
    details = _trajectory_details(
        normalize_prediction_trajectory(
            SimpleNamespace(trajectory=[{"reasoning": semantic, "code": semantic, "output": semantic}])
        ),
        max_chars=200,
    )

    assert [item.text for item in details if isinstance(item, RLMReasoning)] == [semantic]
    assert [item.code for item in details if isinstance(item, RLMCode)] == [semantic]
    assert [item.output for item in details if isinstance(item, RLMOutput)] == [semantic]

    truncated = _trajectory_details(
        normalize_prediction_trajectory(
            SimpleNamespace(trajectory=[{"reasoning": "x" * 20, "code": "y" * 20, "output": "z" * 20}])
        ),
        max_chars=12,
    )
    values = [
        item.text if isinstance(item, RLMReasoning) else item.code if isinstance(item, RLMCode) else item.output
        for item in truncated
        if isinstance(item, (RLMReasoning, RLMCode, RLMOutput))
    ]
    assert values == ["x" * 9 + "...", "y" * 9 + "...", "z" * 9 + "..."]


def test_trajectory_reconciliation_replaces_live_details_without_duplicates() -> None:
    from fleet_rlm.rlm.dspy_contract import TrajectoryStep
    from fleet_rlm.rlm.events import RLMCode, RLMOutput, RLMReasoning, StepFinished, StepStarted
    from fleet_rlm.rlm.runner import _reconcile_trajectory

    details = [
        StepStarted(1),
        RLMCode("stale code", 1),
        RLMOutput("stale output", 1),
        StepFinished(1),
    ]

    emissions = _reconcile_trajectory(
        details,
        (TrajectoryStep(1, "native reasoning", "native code", "native output"),),
        max_chars=100,
    )

    assert emissions == [
        RLMReasoning("native reasoning", 1),
        RLMCode("native code", 1),
        RLMOutput("native output", 1),
    ]
    assert details == [
        StepStarted(1),
        RLMReasoning("native reasoning", 1),
        RLMCode("native code", 1),
        RLMOutput("native output", 1),
        StepFinished(1),
    ]
    assert (
        _reconcile_trajectory(
            details,
            (TrajectoryStep(1, "native reasoning", "native code", "native output"),),
            max_chars=100,
        )
        == []
    )


def test_trajectory_reconciliation_keeps_live_reasoning_emitted_before_step_started() -> None:
    from fleet_rlm.rlm.dspy_contract import TrajectoryStep
    from fleet_rlm.rlm.events import RLMCode, RLMOutput, RLMReasoning, StepFinished, StepStarted
    from fleet_rlm.rlm.runner import _reconcile_trajectory

    details = [
        RLMReasoning("native reasoning", 1),
        StepStarted(1),
        RLMCode("native code", 1),
        RLMOutput("native output", 1),
        StepFinished(1),
    ]

    assert (
        _reconcile_trajectory(
            details,
            (TrajectoryStep(1, "native reasoning", "native code", "native output"),),
            max_chars=100,
        )
        == []
    )
    assert details == [
        RLMReasoning("native reasoning", 1),
        StepStarted(1),
        RLMCode("native code", 1),
        RLMOutput("native output", 1),
        StepFinished(1),
    ]


def test_trajectory_reconciliation_updates_pre_step_live_reasoning_when_canonical_differs() -> None:
    from fleet_rlm.rlm.dspy_contract import TrajectoryStep
    from fleet_rlm.rlm.events import RLMCode, RLMOutput, RLMReasoning, StepFinished, StepStarted
    from fleet_rlm.rlm.runner import _reconcile_trajectory

    details = [
        RLMReasoning("stale reasoning", 1),
        StepStarted(1),
        RLMCode("native code", 1),
        RLMOutput("native output", 1),
        StepFinished(1),
    ]

    emissions = _reconcile_trajectory(
        details,
        (TrajectoryStep(1, "native reasoning", "native code", "native output"),),
        max_chars=100,
    )

    assert emissions == [RLMReasoning("native reasoning", 1)]
    assert details[0] == RLMReasoning("native reasoning", 1)


@pytest.mark.asyncio
async def test_runner_deduplicates_final_reasoning_against_nonadjacent_normalized_trajectory() -> None:
    from fleet_rlm.chat.session_context import SessionContextManifest
    from fleet_rlm.rlm.context import RLMExecutionContext, RLMExecutionSpec
    from fleet_rlm.rlm.dspy_contract import RLMOptions
    from fleet_rlm.rlm.events import RLMReasoning
    from fleet_rlm.rlm.runner import RLMRunner
    from fleet_rlm.rlm.sanitize import truncate_public_text
    from fleet_rlm.sessions.models import TurnAccess

    repeated = "reasoning requiring public truncation"

    class Capabilities:
        spec = RLMExecutionSpec()

        def drain_public_details(self):
            return ()

        def drain_artifact_candidates(self):
            return ()

    class Factory:
        def create(self, **_kwargs):
            class Program:
                async def acall(self, **_call_kwargs):
                    return dspy.Prediction(
                        answer="done",
                        final_reasoning=repeated,
                        trajectory=[
                            {"reasoning": "first distinct reason", "code": "", "output": ""},
                            {"reasoning": repeated, "code": "", "output": ""},
                        ],
                    )

            return Program()

    async def not_cancelled() -> bool:
        return False

    context = RLMExecutionContext(
        uuid4(),
        uuid4(),
        TurnAccess(uuid4(), uuid4()),
        "answer",
        SessionContextManifest(uuid4(), 0, 0, ()),
        SimpleNamespace(root_lm=object(), sub_lm=object()),
        RLMOptions(max_output_chars=16),
        asyncio.get_running_loop().time() + 10,
        None,
        (),
        Capabilities(),
        not_cancelled,
        (),
    )

    events = [event async for event in RLMRunner(factory=Factory()).stream(context)]

    assert [event.detail.text for event in events if isinstance(event.detail, RLMReasoning)] == [
        truncate_public_text("first distinct reason", max_len=16),
        truncate_public_text(repeated, max_len=16),
    ]
