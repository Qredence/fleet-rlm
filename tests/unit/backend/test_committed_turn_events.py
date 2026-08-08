"""Committed Turn semantic replay projection."""

from __future__ import annotations

from uuid import uuid4


def test_projector_replays_every_semantic_part_in_order() -> None:
    from fleet_rlm.chat.committed_turn_events import CommittedTurnEventProjector
    from fleet_rlm.rlm.events import EventRecorder
    from fleet_rlm.sessions.committed_turn import (
        ArtifactPart,
        AttachmentPart,
        CommittedTurn,
        ReasoningPart,
        StepPart,
        StructuredResultPart,
        TextPart,
        ToolCallPart,
        UsagePart,
    )

    turn = CommittedTurn(
        schema_version=1,
        parts=(
            StepPart(state="started", step=1),
            ReasoningPart(text="think", step=1),
            ToolCallPart("c1", "lookup", "completed", {"q": "x"}, {"ok": True}),
            AttachmentPart(uuid4(), "read", "a.txt", 3),
            ArtifactPart(uuid4(), "json", None, "application/json", 2, "a" * 64),
            UsagePart({"iterations": 1, "observed_lm_usage": {}, "duration_ms": 3}),
            StructuredResultPart("answer", "1", {"value": 42}),
            TextPart("42"),
        ),
    )
    recorder = EventRecorder(uuid4(), uuid4())

    events = CommittedTurnEventProjector().project(turn, recorder, mode="replay")

    assert [event.kind for event in events] == [
        "step.started",
        "rlm.reasoning",
        "tool.started",
        "tool.completed",
        "attachment.read",
        "artifact.created",
        "usage",
        "structured.result",
        "text.delta",
        "text.completed",
    ]
    assert [event.sequence for event in events] == list(range(1, 11))


def test_live_suffix_excludes_execution_parts() -> None:
    from fleet_rlm.chat.committed_turn_events import CommittedTurnEventProjector
    from fleet_rlm.rlm.events import EventRecorder
    from fleet_rlm.sessions.committed_turn import CommittedTurn, ReasoningPart, TextPart, UsagePart

    turn = CommittedTurn(
        schema_version=1,
        parts=(
            ReasoningPart("think"),
            UsagePart({"iterations": 0, "observed_lm_usage": {}, "duration_ms": 0}),
            TextPart("done"),
        ),
    )

    events = CommittedTurnEventProjector().project(
        turn,
        EventRecorder(uuid4(), uuid4()),
        mode="live_suffix",
    )

    assert [event.kind for event in events] == ["usage", "text.delta", "text.completed"]


def test_projector_maps_status_parts_back_to_transient_status_events() -> None:
    from fleet_rlm.chat.committed_turn_events import CommittedTurnEventProjector
    from fleet_rlm.chat.turn_detail_policy import commit_cancelled_tombstone
    from fleet_rlm.rlm.dspy_contract import empty_rlm_usage
    from fleet_rlm.rlm.events import EventRecorder, Status

    turn = commit_cancelled_tombstone(empty_rlm_usage())

    events = CommittedTurnEventProjector().project(turn, EventRecorder(uuid4(), uuid4()), mode="replay")

    status = events[0].detail
    assert isinstance(status, Status)
    assert (status.phase, status.status, status.message) == ("cancelled", "cancelled", None)
    assert [event.kind for event in events] == ["status", "usage", "text.delta", "text.completed"]

    suffix = CommittedTurnEventProjector().project(turn, EventRecorder(uuid4(), uuid4()), mode="live_suffix")
    # Status markers are not part of the post-commit live suffix.
    assert [event.kind for event in suffix] == ["usage", "text.delta", "text.completed"]
