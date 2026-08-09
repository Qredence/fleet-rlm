"""K-002: SSE projection contracts for RuntimeEvent v1."""

from __future__ import annotations

import ast
from pathlib import Path
from uuid import uuid4


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
    after = recorder.record(
        Status(
            "recursive",
            "child_completed",
            "call_index=1 recursive_depth=1 duration_ms=2 cleanup_status=completed",
        )
    )

    assert before.sequence == 1
    assert after.sequence == 2
    assert AISDKUIProjector().project(after) == [
        {
            "type": "data-status",
            "data": {
                "phase": "recursive",
                "status": "child_completed",
                "message": "call_index=1 recursive_depth=1 duration_ms=2 cleanup_status=completed",
            },
            "transient": True,
        }
    ]


def test_openapi_declares_typed_render_data_payloads() -> None:
    from fleet_rlm.composition.testing import create_testing_app

    schema = create_testing_app().openapi()
    variants = schema["components"]["schemas"]["FleetUIMessageChunk"]["oneOf"]
    by_type = {variant["properties"]["type"]["const"]: variant for variant in variants}

    code_data = by_type["data-rlm-code"]["properties"]["data"]
    output_data = by_type["data-rlm-output"]["properties"]["data"]
    structured_data = by_type["data-structured-result"]["properties"]["data"]

    assert code_data["type"] == "object"
    assert code_data["properties"]["code"]["type"] == "string"
    assert output_data["properties"]["output"]["type"] == "string"
    assert "type" not in structured_data["properties"]["value"]
