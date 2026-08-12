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
    from fleet_rlm.rlm.trajectory_projection import trajectory_details

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
    assert [type(item) for item in trajectory_details(steps, max_chars=100)] == [
        StepStarted,
        RLMReasoning,
        RLMCode,
        RLMOutput,
        StepFinished,
    ]


def test_trajectory_semantic_details_are_verbatim_and_share_the_run_bound() -> None:
    from fleet_rlm.rlm.dspy_contract import normalize_prediction_trajectory
    from fleet_rlm.rlm.events import RLMCode, RLMOutput, RLMReasoning
    from fleet_rlm.rlm.trajectory_projection import trajectory_details

    semantic = "api_key=visible-user-text /Users/example BEGIN SYSTEM"
    details = trajectory_details(
        normalize_prediction_trajectory(
            SimpleNamespace(trajectory=[{"reasoning": semantic, "code": semantic, "output": semantic}])
        ),
        max_chars=200,
    )

    assert [item.text for item in details if isinstance(item, RLMReasoning)] == [semantic]
    assert [item.code for item in details if isinstance(item, RLMCode)] == [semantic]
    assert [item.output for item in details if isinstance(item, RLMOutput)] == [semantic]

    truncated = trajectory_details(
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
    from fleet_rlm.rlm.trajectory_projection import reconcile_trajectory

    details = [
        StepStarted(1),
        RLMCode("stale code", 1),
        RLMOutput("stale output", 1),
        StepFinished(1),
    ]

    emissions = reconcile_trajectory(
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
        reconcile_trajectory(
            details,
            (TrajectoryStep(1, "native reasoning", "native code", "native output"),),
            max_chars=100,
        )
        == []
    )


def test_trajectory_reconciliation_replaces_incremental_output_with_one_canonical_part() -> None:
    from fleet_rlm.rlm.dspy_contract import TrajectoryStep
    from fleet_rlm.rlm.events import RLMCode, RLMOutput, RLMReasoning, StepFinished, StepStarted
    from fleet_rlm.rlm.trajectory_projection import reconcile_trajectory

    details = [
        StepStarted(1),
        RLMReasoning("", 1),
        RLMCode("", 1),
        RLMOutput("native ", 1, "output-1", True, False),
        RLMOutput("stale", 1, "output-1", True, False),
        StepFinished(1),
    ]

    emissions = reconcile_trajectory(
        details,
        (TrajectoryStep(1, "", "", "canonical output"),),
        max_chars=100,
    )

    assert emissions == [RLMOutput("canonical output", 1, "output-1", False, True)]
    assert details == [
        StepStarted(1),
        RLMReasoning("", 1),
        RLMCode("", 1),
        RLMOutput("canonical output", 1, "output-1", False, True),
        StepFinished(1),
    ]


def test_trajectory_reconciliation_keeps_live_reasoning_emitted_before_step_started() -> None:
    from fleet_rlm.rlm.dspy_contract import TrajectoryStep
    from fleet_rlm.rlm.events import RLMCode, RLMOutput, RLMReasoning, StepFinished, StepStarted
    from fleet_rlm.rlm.trajectory_projection import reconcile_trajectory

    details = [
        RLMReasoning("native reasoning", 1),
        StepStarted(1),
        RLMCode("native code", 1),
        RLMOutput("native output", 1),
        StepFinished(1),
    ]

    assert (
        reconcile_trajectory(
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
    from fleet_rlm.rlm.trajectory_projection import reconcile_trajectory

    details = [
        RLMReasoning("stale reasoning", 1),
        StepStarted(1),
        RLMCode("native code", 1),
        RLMOutput("native output", 1),
        StepFinished(1),
    ]

    emissions = reconcile_trajectory(
        details,
        (TrajectoryStep(1, "native reasoning", "native code", "native output"),),
        max_chars=100,
    )

    assert emissions == [RLMReasoning("native reasoning", 1)]
    assert details[0] == RLMReasoning("native reasoning", 1)


@pytest.mark.asyncio
async def test_runner_deduplicates_final_reasoning_against_nonadjacent_normalized_trajectory() -> None:
    from fleet_rlm.chat.session_context import SessionContextManifest
    from fleet_rlm.rlm.context import (
        ExecutionRuntime,
        RLMExecutionContext,
        RunIdentity,
        SessionView,
    )
    from fleet_rlm.rlm.dspy_contract import RLMOptions
    from fleet_rlm.rlm.events import RLMReasoning
    from fleet_rlm.rlm.runner import RLMRunner
    from fleet_rlm.rlm.sanitize import truncate_public_text
    from fleet_rlm.sessions.models import TurnAccess
    from tests.unit.backend.rlm.fakes import EmptyCapabilities

    repeated = "reasoning requiring public truncation"

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
        identity=RunIdentity(run_id=uuid4(), session_id=uuid4(), access=TurnAccess(uuid4(), uuid4())),
        session=SessionView(
            request="answer",
            session_context=SessionContextManifest(uuid4(), 0, 0, ()),
            attachments=(),
            preparation_notices=(),
        ),
        execution=ExecutionRuntime(
            models=SimpleNamespace(root_lm=object(), sub_lm=object()),
            options=RLMOptions(max_output_chars=16),
            deadline=asyncio.get_running_loop().time() + 10,
            interpreter=None,
            cancellation_requested=not_cancelled,
        ),
        capabilities=EmptyCapabilities(),
    )

    events = [event async for event in RLMRunner(factory=Factory()).stream(context)]

    assert [event.detail.text for event in events if isinstance(event.detail, RLMReasoning)] == [
        truncate_public_text("first distinct reason", max_len=16),
        truncate_public_text(repeated, max_len=16),
    ]


def test_trajectory_reconciliation_silently_upserts_flag_drifted_identical_streams() -> None:
    """RC-4a: live deltas equal to the canonical text emit nothing at turn end."""
    from fleet_rlm.rlm.dspy_contract import TrajectoryStep
    from fleet_rlm.rlm.events import RLMCode, RLMOutput, RLMReasoning, StepFinished, StepStarted
    from fleet_rlm.rlm.trajectory_projection import reconcile_trajectory

    details = [
        StepStarted(1),
        RLMReasoning("why", 1),
        RLMCode("print(1)", 1),
        RLMOutput("native out", 1, "output-1", True, False),
        RLMOutput("put", 1, "output-1", True, False),
        StepFinished(1),
    ]

    emissions = reconcile_trajectory(
        details,
        (TrajectoryStep(1, "why", "print(1)", "native output"),),
        max_chars=100,
    )

    # Identical public payload: no re-emission, but the durable row is
    # upserted to the canonical full-text flags while keeping the live stream.
    assert emissions == []
    assert details == [
        StepStarted(1),
        RLMReasoning("why", 1),
        RLMCode("print(1)", 1),
        RLMOutput("native output", 1, "output-1", False, True),
        StepFinished(1),
    ]


def test_trajectory_reconciliation_treats_submit_label_and_live_terminal_frame_as_identical() -> None:
    """RC-4a: the pre-fix live log (delta + full final frame) reconciles silently."""
    from fleet_rlm.rlm.dspy_contract import TrajectoryStep
    from fleet_rlm.rlm.events import RLMCode, RLMOutput, RLMReasoning, StepFinished, StepStarted
    from fleet_rlm.rlm.trajectory_projection import reconcile_trajectory

    details = [
        StepStarted(1),
        RLMReasoning("done", 1),
        RLMCode("SUBMIT(answer='ok')", 1),
        RLMOutput("before\n", 1, "output-1", True, False),
        RLMOutput("FINAL submitted", 1, "output-1", False, True),
        StepFinished(1),
    ]

    emissions = reconcile_trajectory(
        details,
        (TrajectoryStep(1, "done", "SUBMIT(answer='ok')", 'FINAL: {"answer": "ok"}'),),
        max_chars=100,
    )

    # The non-delta FINAL label row restarts the stream projection, so the
    # projected live payload matches the canonical label exactly.
    assert emissions == []
    assert details == [
        StepStarted(1),
        RLMReasoning("done", 1),
        RLMCode("SUBMIT(answer='ok')", 1),
        RLMOutput("FINAL submitted", 1, "output-1", False, True),
        StepFinished(1),
    ]


def test_trajectory_reconciliation_re_emits_once_for_a_true_correction() -> None:
    """RC-4a: corrected text still emits exactly one canonical replacement."""
    from fleet_rlm.rlm.dspy_contract import TrajectoryStep
    from fleet_rlm.rlm.events import RLMCode, RLMOutput, RLMReasoning, StepFinished, StepStarted
    from fleet_rlm.rlm.trajectory_projection import reconcile_trajectory

    details = [
        StepStarted(1),
        RLMReasoning("why", 1),
        RLMCode("print(1)", 1),
        RLMOutput("live out", 1, "output-1", True, False),
        RLMOutput("put", 1, "output-1", True, False),
        StepFinished(1),
    ]

    emissions = reconcile_trajectory(
        details,
        (TrajectoryStep(1, "why", "print(1)", "corrected output"),),
        max_chars=100,
    )

    assert emissions == [RLMOutput("corrected output", 1, "output-1", False, True)]
    assert details == [
        StepStarted(1),
        RLMReasoning("why", 1),
        RLMCode("print(1)", 1),
        RLMOutput("corrected output", 1, "output-1", False, True),
        StepFinished(1),
    ]


def test_trajectory_reconciliation_aligns_canonical_steps_after_setup_execution() -> None:
    """A context setup execution must not cause a duplicate canonical action stream."""
    from fleet_rlm.rlm.dspy_contract import TrajectoryStep
    from fleet_rlm.rlm.events import RLMCode, RLMOutput, RLMReasoning, StepFinished, StepStarted
    from fleet_rlm.rlm.trajectory_projection import reconcile_trajectory

    details = [
        StepStarted(1),
        RLMCode("load prepared context", 1),
        StepFinished(1),
        StepStarted(2),
        RLMReasoning("native reasoning", 2),
        RLMCode('SUBMIT(answer="ok")', 2),
        RLMOutput("FINAL submitted", 2),
        StepFinished(2),
    ]

    emissions = reconcile_trajectory(
        details,
        (TrajectoryStep(1, "native reasoning", 'SUBMIT(answer="ok")', "FINAL: ok"),),
        max_chars=100,
    )

    assert emissions == []
    assert details == [
        StepStarted(1),
        RLMCode("load prepared context", 1),
        StepFinished(1),
        StepStarted(2),
        RLMReasoning("native reasoning", 2),
        RLMCode('SUBMIT(answer="ok")', 2),
        RLMOutput("FINAL submitted", 2),
        StepFinished(2),
    ]
