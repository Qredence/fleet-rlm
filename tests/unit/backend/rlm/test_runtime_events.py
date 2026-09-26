"""Closed Runtime Event delivery contract."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from uuid import uuid4

import pytest


def test_runtime_detail_union_has_the_exact_v1_discriminators() -> None:
    from fleet_rlm.rlm.events import RUNTIME_DETAIL_TYPES

    assert {detail_type.kind for detail_type in RUNTIME_DETAIL_TYPES} == {
        "run.started",
        "status",
        "child.progress",
        "step.started",
        "step.finished",
        "rlm.reasoning",
        "rlm.code",
        "rlm.output",
        "tool.started",
        "tool.completed",
        "tool.failed",
        "skill.activated",
        "skill.loaded",
        "attachment.read",
        "warning",
        "artifact.created",
        "usage",
        "structured.result",
        "text.delta",
        "text.completed",
        "run.completed",
        "run.failed",
        "run.cancelled",
        "run.timed_out",
    }


def test_child_progress_is_a_bounded_snapshot() -> None:
    from fleet_rlm.rlm.events import ChildProgress

    detail = ChildProgress("run:call-2", "Search official docs", "completed", 1200, "Found two sources", "complete")
    assert detail.kind == "child.progress"
    assert detail.child_id == "run:call-2"
    with pytest.raises(ValueError, match="elapsed_ms"):
        ChildProgress("c1", "Task", "failed", -1)
    with pytest.raises(ValueError, match="task_label"):
        ChildProgress("c1", " ", "failed", 1)


def test_event_recorder_wraps_typed_details_in_an_immutable_ordered_envelope() -> None:
    from fleet_rlm.rlm.events import EventRecorder, RunStarted, TextDelta

    run_id = uuid4()
    session_id = uuid4()
    recorder = EventRecorder(run_id=run_id, session_id=session_id)

    first = recorder.record(RunStarted(delivery="live"))
    second = recorder.record(TextDelta(text="hello"))

    assert first.kind == "run.started"
    assert first.sequence == 1
    assert second.sequence == 2
    assert second.detail == TextDelta(text="hello")
    assert first.run_id == second.run_id == run_id
    assert first.session_id == second.session_id == session_id
    with pytest.raises(FrozenInstanceError):
        second.sequence = 3  # type: ignore[misc]


def test_event_recorder_rejects_second_or_post_terminal_details() -> None:
    from fleet_rlm.rlm.events import EventRecorder, EventSequenceError, RunCompleted, TextDelta

    recorder = EventRecorder(run_id=uuid4(), session_id=uuid4())
    recorder.record(RunCompleted(checkpoint_version=3, delivery="live"))

    with pytest.raises(EventSequenceError):
        recorder.record(TextDelta(text="late"))


def test_usage_event_rejects_noncanonical_usage() -> None:
    from fleet_rlm.rlm.events import Usage

    with pytest.raises(TypeError):
        Usage({"iterations": 1, "observed_lm_usage": {}, "duration_ms": 1, "llm_calls": 2})
