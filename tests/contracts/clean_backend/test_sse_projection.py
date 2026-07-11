"""K-002: SSE projection contracts for RuntimeEvent v1."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from uuid import uuid4

FIXTURES = Path(__file__).resolve().parent / "fixtures"
TRANSCRIPT = FIXTURES / "valid_run_transcript.sse"


def test_rlm_events_module_does_not_import_fastapi() -> None:
    events_path = Path(__file__).resolve().parents[3] / "src" / "fleet_rlm_clean" / "rlm" / "events.py"
    tree = ast.parse(events_path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".", maxsplit=1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module.split(".", maxsplit=1)[0])
    assert "fastapi" not in imported
    assert "starlette" not in imported


def test_sse_projector_emits_data_lines_with_monotonic_sequences() -> None:
    from fleet_rlm_clean.api.sse import SSEProjector
    from fleet_rlm_clean.rlm.events import EventRecorder, RuntimeEventKind

    recorder = EventRecorder(run_id=uuid4(), session_id=uuid4())
    events = [
        recorder.emit(RuntimeEventKind.RUN_STARTED, {}),
        recorder.emit(RuntimeEventKind.TEXT_DELTA, {"text": "hello"}),
        recorder.emit(
            RuntimeEventKind.RUN_COMPLETED,
            {
                "status": "completed",
                "duration_ms": 1,
                "usage": {},
                "artifact_ids": [],
                "checkpoint_version": 0,
                "degraded": False,
            },
        ),
    ]
    lines = list(SSEProjector().project(events))
    assert all(line.startswith("data: ") and line.endswith("\n\n") for line in lines)
    payloads = [json.loads(line.removeprefix("data: ").rstrip("\n")) for line in lines]
    assert [item["sequence"] for item in payloads] == [1, 2, 3]
    assert payloads[-1]["kind"] == "run.completed"


def test_keepalive_does_not_consume_sequence() -> None:
    from fleet_rlm_clean.api.sse import SSEProjector
    from fleet_rlm_clean.rlm.events import EventRecorder, RuntimeEventKind

    recorder = EventRecorder(run_id=uuid4(), session_id=uuid4())
    projector = SSEProjector()
    before = recorder.emit(RuntimeEventKind.RUN_STARTED, {})
    keepalive = projector.keepalive()
    after = recorder.emit(RuntimeEventKind.STATUS, {"message": "working"})

    assert keepalive == ": keepalive\n\n"
    assert before.sequence == 1
    assert after.sequence == 2


def test_committed_fixture_is_valid_sse_transcript() -> None:
    assert TRANSCRIPT.is_file()
    text = TRANSCRIPT.read_text(encoding="utf-8")
    assert text.endswith("\n")

    data_lines = [line for line in text.splitlines() if line.startswith("data: ")]
    assert len(data_lines) >= 3
    events = [json.loads(line.removeprefix("data: ")) for line in data_lines]
    sequences = [event["sequence"] for event in events]
    assert sequences == list(range(1, len(sequences) + 1))
    assert events[0]["kind"] == "run.started"
    assert events[-1]["kind"] in {"run.completed", "error"}
    assert all(event["schema_version"] == 1 for event in events)
    terminal_kinds = [event["kind"] for event in events if event["kind"] in {"error", "run.completed"}]
    assert len(terminal_kinds) == 1
