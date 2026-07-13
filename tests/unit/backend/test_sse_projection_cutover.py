"""Closed Runtime Event to AI SDK UI projection."""

from __future__ import annotations

from uuid import uuid4


def test_typed_success_projection_has_one_text_lifecycle_and_finish() -> None:
    from fleet_rlm.api.sse import AISDKUIProjector
    from fleet_rlm.rlm.events import EventRecorder, RunCompleted, RunStarted, TextCompleted, TextDelta

    recorder = EventRecorder(uuid4(), uuid4())
    events = (
        recorder.record(RunStarted("live")),
        recorder.record(TextDelta("done")),
        recorder.record(TextCompleted("done")),
        recorder.record(RunCompleted(1, "live")),
    )
    projector = AISDKUIProjector()
    chunks = [chunk for event in events for chunk in projector.project(event)]

    assert [chunk["type"] for chunk in chunks] == ["start", "text-start", "text-delta", "text-end", "finish"]
    assert chunks[-1]["messageMetadata"]["checkpointVersion"] == 1


def test_typed_cancel_projection_is_abort_only() -> None:
    from fleet_rlm.api.sse import AISDKUIProjector
    from fleet_rlm.rlm.events import EventRecorder, RunCancelled

    event = EventRecorder(uuid4(), uuid4()).record(RunCancelled())
    assert AISDKUIProjector().project(event) == [{"type": "abort", "reason": "Turn cancelled"}]
