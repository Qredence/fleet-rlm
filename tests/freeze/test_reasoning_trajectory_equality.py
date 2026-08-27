"""P41 proof that public reasoning deltas equal native trajectory reasoning."""

from __future__ import annotations

from fleet_rlm.rlm.events import RLMReasoning, StepFinished, StepStarted, reconcile_trajectory
from fleet_rlm.rlm.result import TrajectoryStep


def test_reasoning_deltas_equal_native_trajectory_after_correction() -> None:
    trajectory = (
        TrajectoryStep(1, "inspect the workspace", "print(1)", "1"),
        TrajectoryStep(2, "correct canonical reasoning", "print(2)", "2"),
        TrajectoryStep(3, "submit the answer", "SUBMIT(answer='ok')", "FINAL: {'answer': 'ok'}"),
    )
    details = [
        StepStarted(1),
        RLMReasoning("inspect ", 1, "reasoning-1", True, False),
        RLMReasoning("the workspace", 1, "reasoning-1", True, True),
        StepFinished(1),
        StepStarted(2),
        RLMReasoning("stale reasoning", 2, "reasoning-2", True, True),
        StepFinished(2),
        StepStarted(3),
        StepFinished(3),
    ]

    emissions = reconcile_trajectory(details, trajectory, max_chars=200)

    assert [event.text for event in emissions if isinstance(event, RLMReasoning)] == [
        "correct canonical reasoning",
        "submit the answer",
    ]
    by_step = {
        event.step: event.text for event in details if isinstance(event, RLMReasoning) and event.step is not None
    }
    assert by_step == {step.index: step.reasoning for step in trajectory}


def test_reasoning_trajectory_equality_respects_the_public_output_bound() -> None:
    trajectory = (TrajectoryStep(1, "x" * 40, "", ""),)
    details = [StepStarted(1), StepFinished(1)]

    reconcile_trajectory(details, trajectory, max_chars=12)

    reasoning = next(event for event in details if isinstance(event, RLMReasoning))
    assert reasoning.text == "x" * 9 + "..."
