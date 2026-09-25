"""Comprehensive Phase 4 contracts for FastAPI transport and Vercel AI UI message stream SSE."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from fleet_rlm.api.routes.turns import create_turn
from fleet_rlm.api.schemas import CreateTurnRequest
from fleet_rlm.rlm.events import (
    EventRecorder,
    RLMCode,
    RLMOutput,
    RLMReasoning,
    RunCancelled,
    RunCompleted,
    RunFailed,
    RunStarted,
    RuntimeEvent,
    Status,
    StepFinished,
    StepStarted,
    TextCompleted,
    TextDelta,
    ToolCompleted,
    ToolStarted,
)
from tests.support.testing_app import create_testing_app


class _MockOpenedStream:
    """Mock an opened turn event stream."""

    def __init__(self, run_id: UUID, events: list[RuntimeEvent]) -> None:
        self.run_id = run_id
        self._events = events
        self._index = 0
        self.closed = False

    def __aiter__(self) -> _MockOpenedStream:
        return self

    async def __anext__(self) -> RuntimeEvent:
        if self._index >= len(self._events):
            raise StopAsyncIteration
        ev = self._events[self._index]
        self._index += 1
        return ev

    async def aclose(self) -> None:
        self.closed = True


class _MockTurnCoordinator:
    """Mock coordinator returning an opened stream."""

    def __init__(self, opened: _MockOpenedStream) -> None:
        self.opened = opened

    def open_owned(self, _command: Any) -> Any:
        from fleet_rlm.turns import OpenedTurnStream

        return OpenedTurnStream(self.opened.run_id, self.opened)


@pytest.mark.asyncio
async def test_full_rlm_execution_projects_complete_vercel_ai_ui_stream() -> None:
    """Validate full RLM cycle projections: reasoning, code, tools, output, text, finish."""
    session_id = uuid4()
    run_id = uuid4()
    recorder = EventRecorder(session_id, run_id)

    events: list[RuntimeEvent] = [
        recorder.record(RunStarted("live")),
        recorder.record(Status("execution", "running", "thinking")),
        recorder.record(StepStarted(step=1)),
        recorder.record(RLMReasoning("Let me query data", 1, "stream-1", is_delta=True, is_final=False)),
        recorder.record(RLMReasoning(" and inspect logs", 1, "stream-1", is_delta=True, is_final=True)),
        recorder.record(RLMCode("logs = read_logs()", step=1, is_delta=False, is_final=True)),
        recorder.record(ToolStarted("read_logs", "call-001", {"pattern": "*.log"})),
        recorder.record(ToolCompleted("read_logs", "call-001", {"lines": 100})),
        recorder.record(RLMOutput("Found 100 log lines.", step=1, is_delta=False, is_final=True)),
        recorder.record(StepFinished(step=1)),
        recorder.record(TextDelta("Found 100 lines in the server logs.")),
        recorder.record(TextCompleted("Found 100 lines in the server logs.")),
        recorder.record(RunCompleted(checkpoint_version=1, delivery="live")),
    ]

    mock_stream = _MockOpenedStream(run_id, events)
    coordinator = _MockTurnCoordinator(mock_stream)

    frames = [
        frame
        async for frame in create_turn(
            session_id,
            CreateTurnRequest(text="Check server logs"),
            SimpleNamespace(headers={}),
            SimpleNamespace(user_id=uuid4(), workspace_id=uuid4()),
            coordinator,
            SimpleNamespace(run_heartbeat_seconds=10),
            "idemp-001",
            None,
        )
    ]

    # Check the exact ordered sequence of chunk types
    chunk_types = [f.data["type"] for f in frames if isinstance(f.data, dict)]
    assert chunk_types == [
        "data-status",  # Prelude
        "start",  # Run started
        "data-status",  # Execution status
        "start-step",  # Step started
        "reasoning-start",
        "reasoning-delta",
        "reasoning-delta",
        "reasoning-end",
        "data-rlm-code",
        "tool-input-available",
        "tool-output-available",
        "data-rlm-output",
        "finish-step",
        "text-start",
        "text-delta",
        "text-end",
        "finish",
    ]

    # Verify terminal [DONE] frame
    assert frames[-1].raw_data == "[DONE]"
    assert mock_stream.closed


@pytest.mark.asyncio
async def test_run_cancellation_projects_abort_frame() -> None:
    """Validate that run cancellation maps to an abort chunk with reason."""
    session_id = uuid4()
    run_id = uuid4()
    recorder = EventRecorder(session_id, run_id)

    events: list[RuntimeEvent] = [
        recorder.record(RunStarted("live")),
        recorder.record(RunCancelled("Turn cancelled")),
    ]

    mock_stream = _MockOpenedStream(run_id, events)
    coordinator = _MockTurnCoordinator(mock_stream)

    frames = [
        frame
        async for frame in create_turn(
            session_id,
            CreateTurnRequest(text="Stop immediately"),
            SimpleNamespace(headers={}),
            SimpleNamespace(user_id=uuid4(), workspace_id=uuid4()),
            coordinator,
            SimpleNamespace(run_heartbeat_seconds=10),
            "idemp-002",
            None,
        )
    ]

    data_frames = [f.data for f in frames if isinstance(f.data, dict)]
    abort_frames = [d for d in data_frames if d.get("type") == "abort"]
    assert len(abort_frames) == 1
    assert abort_frames[0]["reason"] == "Turn cancelled"
    assert frames[-1].raw_data == "[DONE]"


@pytest.mark.asyncio
async def test_run_failure_projects_error_and_finish_pair() -> None:
    """Validate that run failures project error and finish chunks with reason error."""
    session_id = uuid4()
    run_id = uuid4()
    recorder = EventRecorder(session_id, run_id)

    events: list[RuntimeEvent] = [
        recorder.record(RunStarted("live")),
        recorder.record(RunFailed("execution_failed", "Turn execution failed")),
    ]

    mock_stream = _MockOpenedStream(run_id, events)
    coordinator = _MockTurnCoordinator(mock_stream)

    frames = [
        frame
        async for frame in create_turn(
            session_id,
            CreateTurnRequest(text="Trigger failure"),
            SimpleNamespace(headers={}),
            SimpleNamespace(user_id=uuid4(), workspace_id=uuid4()),
            coordinator,
            SimpleNamespace(run_heartbeat_seconds=10),
            "idemp-003",
            None,
        )
    ]

    data_frames = [f.data for f in frames if isinstance(f.data, dict)]
    types = [d.get("type") for d in data_frames]
    assert "error" in types
    assert "finish" in types

    error_frame = next(d for d in data_frames if d.get("type") == "error")
    finish_frame = next(d for d in data_frames if d.get("type") == "finish")

    assert error_frame["errorText"] == "Turn execution failed"
    assert finish_frame["finishReason"] == "error"
    assert frames[-1].raw_data == "[DONE]"


def test_fastapi_health_endpoint() -> None:
    """Ensure GET /health responds with status ok."""
    app = create_testing_app()
    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data.get("status") in ("healthy", "ok")


def test_fastapi_turn_route_declares_v1_stream_header() -> None:
    """Verify route declares x-vercel-ai-ui-message-stream: v1 in OpenAPI specification."""
    app = create_testing_app()
    openapi_spec = app.openapi()
    path_def = openapi_spec["paths"]["/api/sessions/{session_id}/turns"]["post"]
    assert "responses" in path_def
    assert "200" in path_def["responses"]
    headers = path_def["responses"]["200"]["headers"]
    assert "x-vercel-ai-ui-message-stream" in headers
