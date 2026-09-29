"""Durable per-Turn Runtime Event capture contracts.

The capture is observation, never execution authority: these tests pin the file
format, the module-level bounds, the disabled no-op, and the fail-soft writer
that keeps every filesystem call off the event loop.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from fleet_rlm.observability import turn_capture
from fleet_rlm.observability.turn_capture import NullEventCapture, TurnCaptureStore
from fleet_rlm.rlm.events import (
    EventRecorder,
    RLMOutput,
    RunCompleted,
    RunStarted,
    SkillLoaded,
    Status,
    StructuredResult,
    ToolStarted,
    Usage,
)
from fleet_rlm.rlm.result import empty_rlm_usage

_SECONDS_PER_DAY = 86_400


def _store(root: Path, **overrides: object) -> TurnCaptureStore:
    policy: dict[str, object] = {"root": root, "enabled": True, "retention_days": 14, "max_captures": 500}
    policy.update(overrides)
    return TurnCaptureStore(**policy)  # type: ignore[arg-type]


def _capture_path(store: TurnCaptureStore, session_id: UUID, run_id: UUID) -> Path:
    return store.captures_root / str(session_id) / f"{run_id}.jsonl"


def _read_lines(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _opened_capture(store: TurnCaptureStore) -> tuple[turn_capture.EventCapture, UUID, UUID, EventRecorder]:
    session_id, run_id = uuid4(), uuid4()
    capture = store.open(session_id, run_id)
    return capture, session_id, run_id, EventRecorder(run_id, session_id)


def test_capture_writes_opened_events_and_closed_records(tmp_path: Path) -> None:
    """A capture reopens as JSONL: opened line, one line per event, closed line."""
    store = _store(tmp_path)
    capture, session_id, run_id, recorder = _opened_capture(store)
    assert not isinstance(capture, NullEventCapture)
    events = [
        recorder.record(RunStarted(delivery="live")),
        recorder.record(Status("execution", "running", "step 1")),
        recorder.record(SkillLoaded("skill-a", "long-context", "2.3.0")),
        recorder.record(RunCompleted(checkpoint_version=7, delivery="live")),
    ]
    for event in events:
        capture.record(event)
    capture.finish(trace_id="tr-capture")
    store.aclose()

    lines = _read_lines(_capture_path(store, session_id, run_id))
    assert lines[0]["record"] == "capture_opened"
    assert lines[0]["session_id"] == str(session_id)
    assert lines[0]["run_id"] == str(run_id)
    assert len(lines) == len(events) + 2

    first = lines[1]
    assert first["schema_version"] == 1
    assert first["event_id"] == str(events[0].event_id)
    assert first["run_id"] == str(run_id)
    assert first["session_id"] == str(session_id)
    assert first["sequence"] == 1
    assert first["timestamp"] == events[0].timestamp.isoformat()
    assert first["kind"] == "run.started"
    assert first["detail"] == {"delivery": "live", "trace_id": None}
    assert lines[3]["kind"] == "skill.loaded"
    assert lines[3]["detail"] == {"skill_id": "skill-a", "name": "long-context", "version": "2.3.0"}

    closed = lines[-1]
    assert closed["record"] == "capture_closed"
    assert closed["stop_reason"] == "completed"
    assert closed["complete"] is True
    assert closed["truncated"] is False
    assert closed["event_count"] == len(events)
    assert closed["trace_id"] == "tr-capture"


def test_capture_stops_at_the_event_bound_and_marks_truncation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Past the module event bound the capture truncates instead of growing."""
    monkeypatch.setattr(turn_capture, "_MAX_EVENTS", 3)
    store = _store(tmp_path)
    capture, session_id, run_id, recorder = _opened_capture(store)
    for index in range(6):
        capture.record(recorder.record(Status("execution", "running", f"step {index}")))
    capture.mark_stop_reason("client_disconnect")
    capture.finish(trace_id=None)
    store.aclose()

    lines = _read_lines(_capture_path(store, session_id, run_id))
    assert [line.get("kind") for line in lines[1:-1]] == ["status"] * 3
    closed = lines[-1]
    assert closed["stop_reason"] == "client_disconnect"
    assert closed["complete"] is False
    assert closed["truncated"] is True
    assert closed["event_count"] == 3


def test_capture_keeps_frozen_json_details(tmp_path: Path) -> None:
    """Frozen Runtime Event payloads are captured instead of failing to serialize."""
    store = _store(tmp_path)
    capture, session_id, run_id, recorder = _opened_capture(store)
    capture.record(recorder.record(StructuredResult("answer", "1", {"answer": "42", "citations": ["a"]})))
    capture.record(recorder.record(Usage(empty_rlm_usage())))
    capture.finish(trace_id=None)
    store.aclose()

    lines = _read_lines(_capture_path(store, session_id, run_id))
    assert [line["kind"] for line in lines[1:-1]] == ["structured.result", "usage"]
    assert lines[1]["detail"] == {
        "schema_id": "answer",
        "schema_version": "1",
        "value": {"answer": "42", "citations": ["a"]},
    }
    assert lines[2]["detail"] == {"value": empty_rlm_usage()}
    assert lines[-1]["event_count"] == 2


def test_disabled_store_writes_nothing_and_starts_no_writer_thread(tmp_path: Path) -> None:
    """A disabled store is a no-op: no directory, no file, no writer thread."""
    writers_before = [thread for thread in threading.enumerate() if thread.name == turn_capture._WRITER_THREAD_NAME]
    store = _store(tmp_path, enabled=False)
    capture, session_id, run_id, recorder = _opened_capture(store)
    assert isinstance(capture, NullEventCapture)
    capture.record(recorder.record(RunStarted(delivery="live")))
    capture.mark_stop_reason("completed")
    capture.finish(trace_id="tr-disabled")
    store.aclose()

    assert not (tmp_path / "captures").exists()
    assert not _capture_path(store, session_id, run_id).exists()
    writers_after = [thread for thread in threading.enumerate() if thread.name == turn_capture._WRITER_THREAD_NAME]
    assert writers_after == writers_before


def test_writer_failure_stays_quiet_and_off_the_event_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A writer OSError disables one capture with a single WARNING and no raise."""
    filesystem_calls: list[str] = []
    real_open, real_mkdir = Path.open, Path.mkdir

    def spy_mkdir(self: Path, *args: object, **kwargs: object) -> None:
        filesystem_calls.append(threading.current_thread().name)
        return real_mkdir(self, *args, **kwargs)

    def failing_open(self: Path, *args: object, **kwargs: object):
        filesystem_calls.append(threading.current_thread().name)
        if str(self).endswith(".jsonl"):
            raise OSError(28, "No space left on device")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", spy_mkdir)
    monkeypatch.setattr(Path, "open", failing_open)

    store = _store(tmp_path)
    capture, _session_id, _run_id, recorder = _opened_capture(store)
    with caplog.at_level(logging.WARNING):
        for index in range(3):
            capture.record(recorder.record(Status("execution", "running", f"step {index}")))
        capture.mark_stop_reason("execution_failure")
        capture.finish(trace_id=None)
        store.aclose()

    assert filesystem_calls, "the writer thread must own capture filesystem work"
    assert set(filesystem_calls) == {turn_capture._WRITER_THREAD_NAME}
    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "disabled" in warnings[0].getMessage()


def test_store_prunes_expired_and_excess_captures(tmp_path: Path) -> None:
    """Startup pruning drops files past retention, then the oldest over the cap."""
    captures = tmp_path / "captures"
    expired = captures / "expired-session" / "expired.jsonl"
    expired.parent.mkdir(parents=True)
    expired.write_text("{}\n", encoding="utf-8")
    now = time.time()
    os.utime(expired, (now - 20 * _SECONDS_PER_DAY, now - 20 * _SECONDS_PER_DAY))

    staged: list[Path] = []
    for index, age_seconds in enumerate((100, 200, 300)):
        path = captures / f"session-{index}" / f"run-{index}.jsonl"
        path.parent.mkdir(parents=True)
        path.write_text("{}\n", encoding="utf-8")
        os.utime(path, (now - age_seconds, now - age_seconds))
        staged.append(path)

    store = _store(tmp_path, max_captures=2)
    capture, _session_id, _run_id, _recorder = _opened_capture(store)
    capture.finish()
    store.aclose()

    assert not expired.exists()
    assert not staged[2].exists()
    assert staged[0].exists() and not staged[1].exists()


def test_retention_is_enforced_across_sequential_captures(tmp_path: Path) -> None:
    store = _store(tmp_path, max_captures=2)
    runs = []
    for _ in range(5):
        capture, session_id, run_id, recorder = _opened_capture(store)
        runs.append(_capture_path(store, session_id, run_id))
        capture.record(recorder.record(RunCompleted(checkpoint_version=1, delivery="live")))
        capture.finish()
    store.aclose()

    assert set(store.captures_root.glob("*/*.jsonl")) == set(runs[-2:])
    assert all(_read_lines(path)[-1]["complete"] is True for path in runs[-2:])


def test_retention_protects_expired_active_capture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path, max_captures=1)
    opened = threading.Event()
    write = store._write

    def observed_write(job: turn_capture._Job) -> None:
        write(job)
        if isinstance(job, turn_capture._OpenedJob):
            opened.set()

    monkeypatch.setattr(store, "_write", observed_write)
    capture, session_id, run_id, recorder = _opened_capture(store)
    assert opened.wait(timeout=2)
    active = _capture_path(store, session_id, run_id)
    expired = time.time() - 20 * _SECONDS_PER_DAY
    os.utime(active, (expired, expired))
    other, _other_session, _other_run, _other_recorder = _opened_capture(store)
    other.finish()
    # Shutdown drains the other capture's pruning while this one remains active.
    store.aclose()

    assert active.exists()
    assert list(store.captures_root.glob("*/*.jsonl")) == [active]
    assert _read_lines(active)[0]["record"] == "capture_opened"
    del capture, recorder


@pytest.mark.parametrize("failure", ["encoding", "rendering"])
def test_failed_event_write_cannot_be_followed_by_complete_footer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    render = turn_capture._render

    def failing_render(job: turn_capture._Job) -> dict[str, object]:
        if failure == "rendering" and isinstance(job, turn_capture._EventJob):
            raise ValueError("synthetic serialization failure")
        return render(job)

    monkeypatch.setattr(turn_capture, "_render", failing_render)
    store = _store(tmp_path)
    capture, session_id, run_id, recorder = _opened_capture(store)
    capture.record(recorder.record(RLMOutput("\ud800" if failure == "encoding" else "output")))
    capture.record(recorder.record(RunCompleted(checkpoint_version=1, delivery="live")))
    capture.finish()
    store.aclose()

    lines = _read_lines(_capture_path(store, session_id, run_id))
    assert [line.get("record") for line in lines] == ["capture_opened"]
    assert not store._disabled


def test_capture_redacts_nested_sensitive_fields_without_masking_sandbox_paths(tmp_path: Path) -> None:
    store = _store(tmp_path)
    capture, session_id, run_id, recorder = _opened_capture(store)
    capture.record(
        recorder.record(
            ToolStarted(
                "call",
                "example",
                {
                    "nested": [{"password": "synthetic-private-value", "apiKey": "opaque-value"}],
                    "path": "/home/daytona/fleet/notes.md",
                },
            )
        )
    )
    capture.finish()
    store.aclose()

    payload = _read_lines(_capture_path(store, session_id, run_id))[1]["detail"]["input"]
    assert payload == {
        "nested": [{"password": "[redacted]", "apiKey": "[redacted]"}],
        "path": "/home/daytona/fleet/notes.md",
    }


def test_capture_uses_shared_collection_and_nesting_bounds(tmp_path: Path) -> None:
    nested: object = "leaf"
    for _ in range(12):
        nested = {"child": nested}
    store = _store(tmp_path)
    capture, session_id, run_id, recorder = _opened_capture(store)
    capture.record(recorder.record(StructuredResult("answer", "1", {"items": list(range(100)), "nested": nested})))
    capture.finish()
    store.aclose()

    value = _read_lines(_capture_path(store, session_id, run_id))[1]["detail"]["value"]
    assert value["items"] == list(range(50))
    assert "[truncated]" in json.dumps(value["nested"])
    assert "leaf" not in json.dumps(value["nested"])
