"""K-002: RuntimeEvent envelope, sequence, and terminal rules."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
from datetime import datetime
from uuid import UUID, uuid4

import pytest

FOUNDATION_KINDS = {
    "run.started",
    "status",
    "text.delta",
    "text.completed",
    "tool.started",
    "tool.completed",
    "skill.loaded",
    "attachment.read",
    "artifact.created",
    "usage",
    "warning",
    "error",
    "run.completed",
}


def test_runtime_event_kind_covers_foundation_kinds() -> None:
    from fleet_rlm.rlm.events import RuntimeEventKind

    assert {kind.value for kind in RuntimeEventKind} == FOUNDATION_KINDS


def test_runtime_event_envelope_is_immutable_v1() -> None:
    from fleet_rlm.rlm.events import RuntimeEvent, RuntimeEventKind

    event = RuntimeEvent(
        schema_version=1,
        event_id=uuid4(),
        run_id=uuid4(),
        session_id=uuid4(),
        sequence=1,
        timestamp=datetime.now().astimezone(),
        kind=RuntimeEventKind.RUN_STARTED,
        payload={},
    )
    assert event.schema_version == 1
    assert isinstance(event.event_id, UUID)
    assert isinstance(event.run_id, UUID)
    assert isinstance(event.session_id, UUID)
    with pytest.raises(FrozenInstanceError):
        event.sequence = 2  # type: ignore[misc]


def test_public_schema_has_no_hidden_reasoning_field() -> None:
    from fleet_rlm.rlm.events import RuntimeEvent

    field_names = {f.name for f in fields(RuntimeEvent)}
    forbidden = {
        "reasoning",
        "chain_of_thought",
        "hidden_reasoning",
        "thoughts",
        "private_trace",
    }
    assert not (field_names & forbidden)


def test_event_recorder_assigns_strictly_increasing_sequences() -> None:
    from fleet_rlm.rlm.events import EventRecorder, RuntimeEventKind

    run_id = uuid4()
    session_id = uuid4()
    recorder = EventRecorder(run_id=run_id, session_id=session_id)

    first = recorder.emit(RuntimeEventKind.RUN_STARTED, {})
    second = recorder.emit(RuntimeEventKind.TEXT_DELTA, {"text": "hi"})
    third = recorder.emit(RuntimeEventKind.USAGE, {"prompt_tokens": 1})

    assert [first.sequence, second.sequence, third.sequence] == [1, 2, 3]
    assert first.run_id == run_id == second.run_id == third.run_id


def test_event_recorder_allows_exactly_one_terminal() -> None:
    from fleet_rlm.rlm.events import (
        DuplicateTerminalEventError,
        EventRecorder,
        RuntimeEventKind,
    )

    recorder = EventRecorder(run_id=uuid4(), session_id=uuid4())
    recorder.emit(RuntimeEventKind.RUN_STARTED, {})
    terminal = recorder.emit(
        RuntimeEventKind.RUN_COMPLETED,
        {
            "status": "completed",
            "duration_ms": 12,
            "usage": {},
            "artifact_ids": [],
            "checkpoint_version": 1,
            "degraded": False,
        },
    )
    assert terminal.kind == RuntimeEventKind.RUN_COMPLETED

    with pytest.raises(DuplicateTerminalEventError):
        recorder.emit(RuntimeEventKind.ERROR, {"status": "failed"})


def test_error_is_also_terminal() -> None:
    from fleet_rlm.rlm.events import (
        DuplicateTerminalEventError,
        EventRecorder,
        RuntimeEventKind,
    )

    recorder = EventRecorder(run_id=uuid4(), session_id=uuid4())
    recorder.emit(RuntimeEventKind.ERROR, {"status": "failed", "message": "boom"})
    with pytest.raises(DuplicateTerminalEventError):
        recorder.emit(RuntimeEventKind.RUN_COMPLETED, {"status": "completed"})
