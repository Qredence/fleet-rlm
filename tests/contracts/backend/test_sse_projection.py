"""K-002: SSE projection contracts for RuntimeEvent v1."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from uuid import uuid4

FIXTURES = Path(__file__).resolve().parent / "fixtures"
TRANSCRIPT = FIXTURES / "valid_run_transcript.sse"


def test_rlm_events_module_does_not_import_fastapi() -> None:
    events_path = Path(__file__).resolve().parents[3] / "src" / "fleet_rlm" / "rlm" / "events.py"
    tree = ast.parse(events_path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".", maxsplit=1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module.split(".", maxsplit=1)[0])
    assert "fastapi" not in imported
    assert "starlette" not in imported


def test_ai_sdk_projector_emits_typed_ui_message_chunks() -> None:
    from fleet_rlm.api.sse import AISDKUIProjector
    from fleet_rlm.rlm.events import EventRecorder, RunCompleted, RunStarted, TextDelta

    recorder = EventRecorder(run_id=uuid4(), session_id=uuid4())
    events = [
        recorder.record(RunStarted("live")),
        recorder.record(TextDelta("hello")),
        recorder.record(RunCompleted(0, "live", 1)),
    ]
    projector = AISDKUIProjector()
    payloads = [chunk for event in events for chunk in projector.project(event)]
    assert [item["type"] for item in payloads] == [
        "start",
        "text-start",
        "text-delta",
        "text-end",
        "finish",
    ]
    assert payloads[-1]["finishReason"] == "stop"


def test_projection_does_not_consume_extra_runtime_event_sequences() -> None:
    from fleet_rlm.api.sse import AISDKUIProjector
    from fleet_rlm.rlm.events import EventRecorder, RunStarted, Status

    recorder = EventRecorder(run_id=uuid4(), session_id=uuid4())
    before = recorder.record(RunStarted("live"))
    after = recorder.record(Status("execution", "running", "working"))

    assert before.sequence == 1
    assert after.sequence == 2
    assert AISDKUIProjector().project(after) == [
        {
            "type": "data-status",
            "data": {"phase": "execution", "status": "running", "message": "working"},
            "transient": True,
        }
    ]


def test_committed_fixture_is_valid_sse_transcript() -> None:
    assert TRANSCRIPT.is_file()
    text = TRANSCRIPT.read_text(encoding="utf-8")
    assert text.endswith("\n")

    data_lines = [line for line in text.splitlines() if line.startswith("data: ")]
    assert len(data_lines) >= 3
    chunks = [json.loads(line.removeprefix("data: ")) for line in data_lines if line.removeprefix("data: ") != "[DONE]"]
    assert chunks[0]["type"] == "start"
    assert chunks[-1] == {"type": "finish", "finishReason": "stop"}
    assert data_lines[-1] == "data: [DONE]"
    assert [chunk["type"] for chunk in chunks].count("finish") == 1
