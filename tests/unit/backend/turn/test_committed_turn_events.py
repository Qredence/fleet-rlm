"""Committed Turn semantic replay projection."""

from __future__ import annotations

from uuid import uuid4


def test_projector_replays_terminal_child_progress_with_the_same_payload() -> None:
    from fleet_rlm.api.sse import AISDKUIProjector
    from fleet_rlm.rlm.events import EventRecorder
    from fleet_rlm.sessions.committed_turn import ChildProgressPart, CommittedTurn, TextPart, UsagePart
    from fleet_rlm.sessions.committed_turn_events import CommittedTurnEventProjector

    turn = CommittedTurn(
        schema_version=1,
        parts=(
            ChildProgressPart(
                "root:call-3",
                "Check references",
                "failed",
                930,
                "No reliable source",
                "complete",
                "run-17",
                result_file_count=1,
            ),
            ChildProgressPart(
                "root:call-4",
                "Inspect large input",
                "not_started",
                0,
                "Child admission budget exhausted",
                "not_required",
                "run-17",
            ),
            UsagePart({"iterations": 1, "observed_lm_usage": {}, "duration_ms": 930}),
            TextPart("Done"),
        ),
    )
    events = CommittedTurnEventProjector().project(turn, EventRecorder(uuid4(), uuid4()), mode="replay")
    child = next(event for event in events if event.kind == "child.progress")
    assert AISDKUIProjector().project(child) == [
        {
            "type": "data-child-progress",
            "id": "root:call-3",
            "data": {
                "child_id": "root:call-3",
                "task_label": "Check references",
                "state": "failed",
                "elapsed_ms": 930,
                "outcome": "No reliable source",
                "evidence": [],
                "gaps": [],
                "result_file_count": 1,
                "cleanup_state": "complete",
                "parent_run_id": "run-17",
            },
        }
    ]
    refused = next(event for event in events if getattr(event.detail, "child_id", None) == "root:call-4")
    assert AISDKUIProjector().project(refused) == [
        {
            "type": "data-child-progress",
            "id": "root:call-4",
            "data": {
                "child_id": "root:call-4",
                "task_label": "Inspect large input",
                "state": "not_started",
                "elapsed_ms": 0,
                "outcome": "Child admission budget exhausted",
                "evidence": [],
                "gaps": [],
                "result_file_count": 0,
                "cleanup_state": "not_required",
                "parent_run_id": "run-17",
            },
        }
    ]


def test_projector_replays_every_semantic_part_in_order() -> None:
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
    from fleet_rlm.sessions.committed_turn_events import CommittedTurnEventProjector

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
    from fleet_rlm.rlm.events import EventRecorder
    from fleet_rlm.sessions.committed_turn import CommittedTurn, ReasoningPart, TextPart, UsagePart
    from fleet_rlm.sessions.committed_turn_events import CommittedTurnEventProjector

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
    from fleet_rlm.rlm.events import EventRecorder, Status
    from fleet_rlm.rlm.result import empty_rlm_usage
    from fleet_rlm.sessions.committed_turn import commit_cancelled_tombstone
    from fleet_rlm.sessions.committed_turn_events import CommittedTurnEventProjector

    turn = commit_cancelled_tombstone(empty_rlm_usage())

    events = CommittedTurnEventProjector().project(turn, EventRecorder(uuid4(), uuid4()), mode="replay")

    status = events[0].detail
    assert isinstance(status, Status)
    assert (status.phase, status.status, status.message) == ("cancelled", "cancelled", None)
    assert [event.kind for event in events] == ["status", "usage", "text.delta", "text.completed"]

    suffix = CommittedTurnEventProjector().project(turn, EventRecorder(uuid4(), uuid4()), mode="live_suffix")
    # Status markers are not part of the post-commit live suffix.
    assert [event.kind for event in suffix] == ["usage", "text.delta", "text.completed"]
