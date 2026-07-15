"""QRE-78 P0 AI SDK UI turn lifecycle projection contracts."""

from __future__ import annotations

import json
from uuid import uuid4

from fleet_rlm.api.sse import SSEProjector
from fleet_rlm.rlm.events import (
    TERMINAL_DETAIL_TYPES,
    EventRecorder,
    RunCancelled,
    RunCompleted,
    RunFailed,
    RunStarted,
    RuntimeEvent,
    Status,
    TextCompleted,
    TextDelta,
    Usage,
)


def _projected_stream(events: list[RuntimeEvent]) -> tuple[list[str], str]:
    projector = SSEProjector()
    frames = [*projector.project(events), projector.done()]
    chunks = [json.loads(frame.removeprefix("data: ")) for frame in frames[:-1]]
    return [chunk["type"] for chunk in chunks], frames[-1]


def test_projected_turn_lifecycle_is_start_chunks_one_terminal_done() -> None:
    recorder = EventRecorder(run_id=uuid4(), session_id=uuid4())
    events = [
        recorder.record(RunStarted("live")),
        recorder.record(Status("execution", "running")),
        recorder.record(Usage({"iterations": 1})),
        recorder.record(TextDelta("ok")),
        recorder.record(TextCompleted("ok")),
        recorder.record(RunCompleted(1, "live")),
    ]

    terminal_events = [event for event in events if isinstance(event.detail, TERMINAL_DETAIL_TYPES)]
    assert len(terminal_events) == 1
    assert terminal_events[0] is events[-1]

    types, done = _projected_stream(events)
    assert types[0] == "start"
    assert types[-1] == "finish"
    assert types.count("finish") == 1
    assert not any(chunk_type in {"finish", "abort", "error"} for chunk_type in types[1:-1])
    assert done == "data: [DONE]\n\n"


def test_error_terminal_projects_error_then_finish_as_single_runtime_terminal() -> None:
    recorder = EventRecorder(run_id=uuid4(), session_id=uuid4())
    events = [recorder.record(RunStarted("live")), recorder.record(RunFailed("execution_failed", "Turn failed"))]

    assert len([event for event in events if isinstance(event.detail, TERMINAL_DETAIL_TYPES)]) == 1
    assert isinstance(events[-1].detail, TERMINAL_DETAIL_TYPES)
    types, done = _projected_stream(events)
    assert types == ["start", "error", "finish"]
    assert done == "data: [DONE]\n\n"


def test_abort_terminal_projects_abort_as_single_runtime_terminal() -> None:
    recorder = EventRecorder(run_id=uuid4(), session_id=uuid4())
    events = [recorder.record(RunStarted("live")), recorder.record(RunCancelled())]

    assert len([event for event in events if isinstance(event.detail, TERMINAL_DETAIL_TYPES)]) == 1
    assert isinstance(events[-1].detail, TERMINAL_DETAIL_TYPES)
    types, done = _projected_stream(events)
    assert types == ["start", "abort"]
    assert done == "data: [DONE]\n\n"
