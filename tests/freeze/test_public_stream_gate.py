"""Integrated deterministic P41 public stream gate."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4, uuid5

import pytest

from fleet_rlm.api.sse import FLEET_UI_CHUNK_TYPES, AISDKUIProjector
from fleet_rlm.api.ui_stream import FleetUIMessageChunkAdapter
from fleet_rlm.composition.testing import create_testing_app
from fleet_rlm.rlm.events import (
    RUNTIME_DETAIL_TYPES,
    EventRecorder,
    ObservationSession,
    RLMCode,
    RLMOutput,
    RLMReasoning,
    RunCompleted,
    RunStarted,
    RuntimeEvent,
    Status,
    StepFinished,
    StepStarted,
    StructuredResult,
    TextCompleted,
    TextDelta,
    ToolCompleted,
    ToolStarted,
    Usage,
    WarningEvent,
)
from fleet_rlm.sessions.assistant_parts import AssistantPartModelUnion

_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / ".."
    / "tools"
    / "fleet-tui"
    / "src"
    / "tests"
    / "fixtures"
    / "turn-stream.jsonl"
).resolve()

_RUNTIME_TYPES = {
    "run.started",
    "status",
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


def test_runtime_and_transport_vocabularies_are_exact_and_disjoint() -> None:
    assert {detail.kind for detail in RUNTIME_DETAIL_TYPES} == _RUNTIME_TYPES
    assert tuple(FLEET_UI_CHUNK_TYPES) == (
        "start",
        "start-step",
        "finish-step",
        "reasoning-start",
        "reasoning-delta",
        "reasoning-end",
        "data-status",
        "data-skill",
        "data-rlm-code",
        "data-rlm-output",
        "tool-input-available",
        "tool-output-available",
        "tool-output-error",
        "data-attachment",
        "data-warning",
        "data-artifact",
        "data-usage",
        "data-structured-result",
        "text-start",
        "text-delta",
        "text-end",
        "finish",
        "abort",
        "error",
    )
    durable = {model.model_fields["type"].default for model in AssistantPartModelUnion}
    assert durable == {
        "step",
        "reasoning",
        "code",
        "output",
        "tool_call",
        "skill",
        "attachment",
        "warning",
        "status",
        "artifact",
        "usage",
        "structured_result",
        "text",
    }
    assert {"tool_call", "structured_result"}.isdisjoint(FLEET_UI_CHUNK_TYPES)


def test_public_api_keeps_one_stream_route_and_no_new_auth_surface() -> None:
    schema = create_testing_app().openapi()
    paths = schema["paths"]
    stream_paths = [
        path for path, operations in paths.items() if "text/event-stream" in json.dumps(operations, sort_keys=True)
    ]

    assert stream_paths == ["/api/sessions/{session_id}/turns"]
    assert not any(path.startswith("/api/v1") for path in paths)
    assert "/api/chat" not in paths
    assert not any(path.startswith("/ws") for path in paths)
    assert "securitySchemes" not in schema.get("components", {})
    assert all(
        "security" not in operation
        for operations in paths.values()
        for operation in operations.values()
        if isinstance(operation, dict)
    )


def test_runtime_event_identity_is_immutable_and_contiguous() -> None:
    run_id = uuid4()
    session_id = uuid4()
    recorder = EventRecorder(run_id, session_id)
    events = [
        recorder.record(RunStarted("live")),
        recorder.record(Status("execution", "running")),
        recorder.record(StepStarted(1)),
        recorder.record(RLMReasoning("reason", 1)),
        recorder.record(RLMCode("print(1)", 1)),
        recorder.record(RLMOutput("1", 1)),
        recorder.record(StepFinished(1)),
        recorder.record(TextDelta("done")),
        recorder.record(TextCompleted("done")),
        recorder.record(RunCompleted(1, "live")),
    ]

    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    assert len({event.event_id for event in events}) == len(events)
    assert {event.run_id for event in events} == {run_id}
    assert {event.session_id for event in events} == {session_id}
    assert all(event.schema_version == 1 for event in events)
    assert all(event.timestamp.tzinfo == UTC for event in events)
    with pytest.raises((AttributeError, TypeError)):
        events[0].sequence = 99  # type: ignore[misc]


def test_projector_preserves_step_pairing_and_settlement_order() -> None:
    run_id = UUID("11111111-1111-1111-1111-111111111111")
    session_id = UUID("00000000-0000-0000-0000-000000000000")
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    details = (
        RunStarted("live"),
        Status("execution", "running"),
        StepStarted(1),
        RLMReasoning("reason", 1),
        RLMCode("print(1)", 1),
        RLMOutput("1", 1),
        StepFinished(1),
        ToolStarted("call-1", "lookup", {"count": 1}),
        ToolCompleted("call-1", "lookup", {"ok": True}),
        Usage({"iterations": 1, "observed_lm_usage": {}, "duration_ms": 2}),
        StructuredResult("answer", "1", {"answer": "ok"}),
        TextDelta("ok"),
        TextCompleted("ok"),
        RunCompleted(1, "live"),
    )
    projector = AISDKUIProjector()
    chunks = []
    for sequence, detail in enumerate(details, start=1):
        event = RuntimeEvent(1, uuid5(run_id, str(sequence)), run_id, session_id, sequence, timestamp, detail)
        chunks.extend(projector.project(event))

    types = [chunk["type"] for chunk in chunks]
    assert types.count("start") == 1
    assert types.count("start-step") == types.count("finish-step") == 1
    assert types.index("start-step") < types.index("reasoning-delta") < types.index("data-rlm-code")
    assert types.index("data-rlm-code") < types.index("data-rlm-output") < types.index("finish-step")
    assert types.index("data-usage") < types.index("data-structured-result") < types.index("text-start")
    assert types[-1] == "finish"
    assert all(FleetUIMessageChunkAdapter.validate_python(chunk, strict=False) is not None for chunk in chunks)


@pytest.mark.asyncio
async def test_observation_overflow_retains_lifecycle_and_emits_one_warning() -> None:
    async def not_cancelled() -> bool:
        return False

    class Worker:
        def done(self) -> bool:
            return True

        async def wait_until_done(self) -> None:
            return None

        async def settle_after_caller_cancellation(self) -> bool:
            return False

        def consume_exception(self) -> None:
            return None

    session = ObservationSession(uuid4(), uuid4(), maxsize=1)
    session.publish(RLMOutput("kept", 1))
    session.publish(RLMOutput("dropped", 1))
    session.publish(StepStarted(1))
    session.publish(StepFinished(1))

    context = SimpleNamespace(
        execution=SimpleNamespace(
            deadline=asyncio.get_running_loop().time() + 10,
            cancellation_requested=not_cancelled,
        )
    )
    events = [
        event
        async for event in session.stream_worker(
            Worker(),
            context,
            lambda: (),
        )
    ]

    details = [event.detail for event in events]
    assert sum(isinstance(detail, WarningEvent) for detail in details) == 1
    assert details[-1] == WarningEvent("some detailed execution events were omitted")
    assert any(isinstance(detail, StepStarted) for detail in details)
    assert any(isinstance(detail, StepFinished) for detail in details)
    assert session.overflowed is True


def test_trace_metadata_is_confined_to_existing_start_finish_chunks() -> None:
    run_id = uuid4()
    session_id = uuid4()
    trace_id = "trace-p41-canonical"
    recorder = EventRecorder(run_id, session_id)
    projector = AISDKUIProjector()
    events = (
        recorder.record(RunStarted("live", trace_id)),
        recorder.record(Status("execution", "running")),
        recorder.record(RunCompleted(1, "live", trace_id=trace_id)),
    )
    chunks = [chunk for event in events for chunk in projector.project(event)]
    assert chunks[0]["messageMetadata"]["traceId"] == trace_id
    assert chunks[-1]["messageMetadata"]["traceId"] == trace_id
    assert all(trace_id not in json.dumps(chunk) for chunk in chunks[1:-1])


def test_live_fixture_has_closed_wire_kinds_and_no_unbounded_values() -> None:
    lines = [line for line in _FIXTURE.read_text(encoding="utf-8").splitlines() if line and line != "[DONE]"]
    assert lines
    for line in lines:
        chunk = json.loads(line)
        validated = FleetUIMessageChunkAdapter.validate_python(chunk, strict=False)
        assert validated is not None
    assert {event["type"] for line in lines for event in [json.loads(line)]} == set(FLEET_UI_CHUNK_TYPES)
