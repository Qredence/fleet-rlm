"""Native FastAPI SSE contracts for the request-bound AI SDK UI stream."""

from __future__ import annotations

import asyncio
import inspect
import json
import time
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.sse import EventSourceResponse
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
from fleet_rlm.sessions.run_state import RunInProgressError
from tests.support.testing_app import create_testing_app

_END = object()

PREPARATION_PRELUDE = {
    "type": "turn_status",
    "phase": "preparation",
    "status": "running",
    "message": None,
}


class _ControlledOpenedTurn:
    def __init__(self, run_id: UUID) -> None:
        self.run_id = run_id
        self.anext_calls = 0
        self.cancelled_reads = 0
        self.close_calls = 0
        self._items: asyncio.Queue[RuntimeEvent | BaseException | object] = asyncio.Queue()

    def __aiter__(self) -> _ControlledOpenedTurn:
        return self

    async def __anext__(self) -> RuntimeEvent:
        self.anext_calls += 1
        try:
            item = await self._items.get()
        except asyncio.CancelledError:
            self.cancelled_reads += 1
            raise
        if item is _END:
            raise StopAsyncIteration
        if isinstance(item, BaseException):
            raise item
        return cast("RuntimeEvent", item)

    async def aclose(self) -> None:
        self.close_calls += 1

    def put(self, *items: RuntimeEvent | BaseException | object) -> None:
        for item in items:
            self._items.put_nowait(item)


class _ControlledCoordinator:
    def __init__(
        self,
        opened: _ControlledOpenedTurn | None = None,
        error: BaseException | None = None,
        gate: asyncio.Event | None = None,
    ) -> None:
        self._opened = opened
        self._error = error
        self._gate = gate
        self.open_calls = 0
        self.command: object | None = None

    async def _open(self, command: object) -> _ControlledOpenedTurn:
        self.open_calls += 1
        self.command = command
        if self._gate is not None:
            await self._gate.wait()
        if self._error is not None:
            raise self._error
        assert self._opened is not None
        return self._opened

    def open_owned(self, command: object):
        from fleet_rlm.turns import OpenedTurnStream

        return OpenedTurnStream(
            None,
            open_task=asyncio.create_task(self._open(command), name="test-turn-open"),
        )


@dataclass
class _ASGIProbe:
    task: asyncio.Task[None]
    incoming: asyncio.Queue[dict[str, Any]]
    bodies: asyncio.Queue[bytes]
    messages: list[dict[str, Any]]

    @property
    def body(self) -> bytes:
        return b"".join(
            message.get("body", b"") for message in self.messages if message["type"] == "http.response.body"
        )


async def _start_route(coordinator: _ControlledCoordinator) -> _ASGIProbe:
    from fleet_rlm.api.dependencies import get_turn_runtime
    from fleet_rlm.api.routes.turns import router

    app = FastAPI()
    app.state.settings = SimpleNamespace(run_heartbeat_seconds=10)
    app.include_router(router)

    app.dependency_overrides[get_turn_runtime] = lambda: coordinator
    body = json.dumps({"text": "hello"}).encode()
    incoming: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    incoming.put_nowait({"type": "http.request", "body": body, "more_body": False})
    bodies: asyncio.Queue[bytes] = asyncio.Queue()
    messages: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return await incoming.get()

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)
        if message["type"] == "http.response.body":
            bodies.put_nowait(message.get("body", b""))

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": f"/api/sessions/{uuid4()}/turns",
        "raw_path": b"",
        "query_string": b"",
        "headers": [
            (b"host", b"fleet.test"),
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
            (b"idempotency-key", f"probe-{uuid4()}".encode()),
        ],
        "client": ("127.0.0.1", 1),
        "server": ("fleet.test", 80),
        "root_path": "",
    }
    task = asyncio.create_task(app(scope, receive, send))
    return _ASGIProbe(task, incoming, bodies, messages)


def _data_frames(body: bytes) -> list[str]:
    return [
        line.removeprefix("data: ")
        for frame in body.decode().split("\n\n")
        for line in frame.splitlines()
        if line.startswith("data: ")
    ]


def _data_chunks(body: bytes) -> list[dict[str, Any]]:
    return [json.loads(value) for value in _data_frames(body) if value != "[DONE]"]


async def _wait_for_ping(probe: _ASGIProbe, count: int = 1) -> None:
    seen = 0
    while seen < count:
        body = await asyncio.wait_for(probe.bodies.get(), timeout=1)
        if body == b": ping\n\n":
            seen += 1


async def _wait_for_first_data_frame(probe: _ASGIProbe) -> bytes:
    while True:
        body = await asyncio.wait_for(probe.bodies.get(), timeout=1)
        if body != b": ping\n\n":
            return body


def _turn_route() -> APIRoute:
    from fleet_rlm.api.routes.turns import router

    return next(
        route
        for route in router.routes
        if isinstance(route, APIRoute) and route.path == "/api/sessions/{session_id}/turns" and "POST" in route.methods
    )


def test_turn_route_uses_fastapi_native_sse_generator() -> None:
    route = _turn_route()

    assert route.response_class is EventSourceResponse
    assert inspect.isasyncgenfunction(route.endpoint)


def test_turn_response_exposes_the_native_sse_header_contract() -> None:
    app = create_testing_app()

    with TestClient(app) as client:
        session = client.post("/api/sessions", json={})
        response = client.post(
            f"/api/sessions/{session.json()['id']}/turns",
            json={"text": "hello"},
            headers={"Idempotency-Key": "phase-8-native-headers"},
        )

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/event-stream; charset=utf-8"
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"
    assert response.headers["x-vercel-ai-ui-message-stream"] == "v1"
    # The run id cannot be a response header anymore: it only exists once the
    # in-stream open completes, after SSE headers were committed. Consumers read
    # it from the start chunk metadata instead.
    assert "x-fleet-run-id" not in response.headers
    assert "connection" not in response.headers


def test_turn_stream_opens_with_a_transient_preparation_prelude() -> None:
    app = create_testing_app()

    with TestClient(app) as client:
        session = client.post("/api/sessions", json={})
        response = client.post(
            f"/api/sessions/{session.json()['id']}/turns",
            json={"text": "hello"},
            headers={"Idempotency-Key": "prelude-contract"},
        )

    frames = _data_frames(response.content)
    chunks = _data_chunks(response.content)
    assert frames[-1] == "[DONE]"
    assert chunks[0] == PREPARATION_PRELUDE
    assert chunks[1]["type"] == "turn_start"
    assert chunks[-1]["type"] == "turn_finish"


def test_preparation_prelude_never_enters_the_durable_turn_log() -> None:
    app = create_testing_app()

    with TestClient(app) as client:
        session = client.post("/api/sessions", json={})
        session_id = session.json()["id"]
        response = client.post(
            f"/api/sessions/{session_id}/turns",
            json={"text": "hello"},
            headers={"Idempotency-Key": "prelude-durability"},
        )
        assert response.status_code == 200
        turns = client.get(f"/api/sessions/{session_id}/turns")

    assert turns.status_code == 200
    messages = turns.json()["items"]
    assert [message["role"] for message in messages] == ["user", "assistant"]
    part_types = [part["type"] for part in messages[1]["parts"]]
    assert "data-status" not in part_types
    assert "preparation" not in response.text.replace(json.dumps(PREPARATION_PRELUDE), "")


@pytest.mark.asyncio
async def test_first_frame_precedes_slow_open_by_a_wide_margin() -> None:
    gate = asyncio.Event()
    opened = _ControlledOpenedTurn(uuid4())
    coordinator = _ControlledCoordinator(opened, gate=gate)
    probe = await _start_route(coordinator)

    started = time.perf_counter()
    body = await _wait_for_first_data_frame(probe)
    elapsed = time.perf_counter() - started

    assert elapsed < 1.0
    assert json.loads(_data_frames(body)[0]) == PREPARATION_PRELUDE
    # Nothing else is emitted while the claim and preparation remain unresolved.
    assert coordinator.open_calls == 1
    gate.set()
    recorder = EventRecorder(opened.run_id, uuid4())
    opened.put(recorder.record(RunStarted("live")), recorder.record(RunCompleted(1, "live")), _END)
    await asyncio.wait_for(probe.task, timeout=1)
    assert _data_frames(probe.body)[-1] == "[DONE]"


@pytest.mark.asyncio
async def test_open_failure_projects_closed_error_and_finish_frames() -> None:
    coordinator = _ControlledCoordinator(error=RunInProgressError("Turn is already running"))
    probe = await _start_route(coordinator)

    await asyncio.wait_for(probe.task, timeout=1)

    chunks = _data_chunks(probe.body)
    assert chunks[0] == PREPARATION_PRELUDE
    assert chunks[1:] == [
        {"type": "turn_error", "message": "A Turn is already running", "code": "open_failed"},
        {"type": "turn_finish", "finishReason": "error", "status": "error"},
    ]
    assert _data_frames(probe.body)[-1] == "[DONE]"


@pytest.mark.asyncio
async def test_native_heartbeats_keep_one_pending_event_read_and_done_is_emitted_once(monkeypatch) -> None:
    import fastapi.routing

    monkeypatch.setattr(fastapi.routing, "_PING_INTERVAL", 0.01)
    run_id = uuid4()
    recorder = EventRecorder(run_id, uuid4())
    opened = _ControlledOpenedTurn(run_id)
    probe = await _start_route(_ControlledCoordinator(opened))

    await _wait_for_ping(probe, 2)
    assert opened.anext_calls == 1

    opened.put(
        recorder.record(RunStarted("live")),
        recorder.record(RunCompleted(checkpoint_version=1, delivery="live")),
        _END,
    )
    await asyncio.wait_for(probe.task, timeout=1)
    data = _data_frames(probe.body)
    chunks = _data_chunks(probe.body)

    assert [chunk["type"] for chunk in chunks] == ["turn_status", "turn_start", "turn_finish"]
    assert chunks[0] == PREPARATION_PRELUDE
    assert data.count("[DONE]") == 1
    assert data[-1] == "[DONE]"
    assert opened.close_calls == 1


@pytest.mark.asyncio
async def test_native_sse_forwards_rlm_delta_before_terminal_finish() -> None:
    run_id = uuid4()
    recorder = EventRecorder(run_id, uuid4())
    opened = _ControlledOpenedTurn(run_id)
    probe = await _start_route(_ControlledCoordinator(opened))

    opened.put(
        recorder.record(RunStarted("live")),
        recorder.record(RLMReasoning("first token", 1, "stream-1", True, False)),
        recorder.record(RLMReasoning("last token", 1, "stream-1", True, True)),
        recorder.record(RunCompleted(checkpoint_version=1, delivery="live")),
        _END,
    )
    await asyncio.wait_for(probe.task, timeout=1)

    chunks = _data_chunks(probe.body)
    types = [chunk["type"] for chunk in chunks]

    assert types[0] == "turn_status"
    assert chunks[0] == PREPARATION_PRELUDE
    assert types[1:] == ["turn_start", "reasoning", "reasoning", "turn_finish"]
    assert _data_frames(probe.body)[-1] == "[DONE]"


@pytest.mark.asyncio
async def test_native_disconnect_cancels_the_pending_read_closes_once_and_omits_done(monkeypatch) -> None:
    import fastapi.routing

    monkeypatch.setattr(fastapi.routing, "_PING_INTERVAL", 0.01)
    opened = _ControlledOpenedTurn(uuid4())
    probe = await _start_route(_ControlledCoordinator(opened))

    await _wait_for_ping(probe)
    probe.incoming.put_nowait({"type": "http.disconnect"})
    await asyncio.wait_for(probe.task, timeout=1)

    assert b"data: [DONE]" not in probe.body
    assert opened.anext_calls == 1
    assert opened.cancelled_reads == 1
    assert opened.close_calls == 1


@pytest.mark.asyncio
async def test_native_disconnect_during_preparation_waits_open_out_then_closes_it() -> None:
    gate = asyncio.Event()
    run_id = uuid4()
    recorder = EventRecorder(run_id, uuid4())
    opened = _ControlledOpenedTurn(run_id)
    coordinator = _ControlledCoordinator(opened, gate=gate)
    probe = await _start_route(coordinator)

    await _wait_for_first_data_frame(probe)
    probe.incoming.put_nowait({"type": "http.disconnect"})
    await asyncio.sleep(0.05)
    # The unresolved open is never cancelled; disconnect settlement waits it out.
    assert not probe.task.done()

    gate.set()
    opened.put(recorder.record(RunStarted("live")))
    await asyncio.sleep(0.05)
    opened.put(_END)
    await asyncio.wait_for(probe.task, timeout=1)

    assert b"data: [DONE]" not in probe.body
    # Opening settled; the route started then closed the stream so the driver
    # could run its cancellation settlement path.
    assert opened.anext_calls >= 1
    assert opened.close_calls == 1


@pytest.mark.asyncio
async def test_native_iterator_failure_closes_once_and_does_not_emit_done() -> None:
    opened = _ControlledOpenedTurn(uuid4())
    opened.put(RuntimeError("stream failed"))
    probe = await _start_route(_ControlledCoordinator(opened))

    with pytest.raises(BaseExceptionGroup) as raised:
        await asyncio.wait_for(probe.task, timeout=1)

    runtime_errors = raised.value.subgroup(RuntimeError)
    assert runtime_errors is not None
    assert any(str(error) == "stream failed" for error in runtime_errors.exceptions)
    assert b"data: [DONE]" not in probe.body
    assert opened.close_calls == 1


class _OpenedStream:
    def __init__(self, events: tuple[RuntimeEvent, ...]) -> None:
        self.run_id = events[0].run_id
        self._events = events
        self._index = 0
        self.closed = False

    def __aiter__(self) -> _OpenedStream:
        return self

    async def __anext__(self) -> RuntimeEvent:
        if self._index >= len(self._events):
            raise StopAsyncIteration
        event = self._events[self._index]
        self._index += 1
        return event

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_native_runtime_deltas_reach_fastapi_sse_before_done() -> None:
    from fleet_rlm.api.routes.turns import create_turn
    from fleet_rlm.api.schemas import CreateTurnRequest

    recorder = EventRecorder(uuid4(), uuid4())
    opened = _OpenedStream(
        (
            recorder.record(RunStarted("live")),
            recorder.record(RLMReasoning("first", 1, "stream-1", True, False)),
            recorder.record(RLMReasoning("last", 1, "stream-1", True, True)),
            recorder.record(RunCompleted(checkpoint_version=1, delivery="live")),
        )
    )

    class _Coordinator:
        def open_owned(self, _command: object):
            from fleet_rlm.turns import OpenedTurnStream

            return OpenedTurnStream(opened.run_id, opened)

    frames = [
        frame
        async for frame in create_turn(
            uuid4(),
            CreateTurnRequest(text="hello"),
            SimpleNamespace(headers={}),
            SimpleNamespace(user_id=uuid4(), workspace_id=uuid4()),
            _Coordinator(),
            SimpleNamespace(run_heartbeat_seconds=10),
            "vertical-slice",
            None,
        )
    ]

    assert [frame.data["type"] for frame in frames[:-1]] == [
        "turn_status",
        "turn_start",
        "reasoning",
        "reasoning",
        "turn_finish",
    ]
    assert frames[0].data == {
        "type": "turn_status",
        "phase": "preparation",
        "status": "running",
        "message": None,
    }
    assert frames[-1].raw_data == "[DONE]"
    assert opened.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_scratch", [True, False])
async def test_run_scratch_cleanup_preserves_completed_sse_boundary(missing_scratch: bool) -> None:
    from daytona.common.errors import DaytonaFileNotFoundError

    from fleet_rlm.api.routes.turns import create_turn
    from fleet_rlm.api.schemas import CreateTurnRequest
    from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, _SandboxProcessBackend
    from fleet_rlm.turns import OpenedTurnStream

    run_id = uuid4()
    path = f"/tmp/fleet/{run_id}"
    deleted: list[str] = []

    class Filesystem:
        def delete_file(self, target: str, *, recursive: bool) -> None:
            assert recursive
            deleted.append(target)
            if missing_scratch:
                raise DaytonaFileNotFoundError("missing Run scratch", status_code=404)
            raise RuntimeError("Sandbox is unavailable")

    template = DaytonaCodeInterpreter(backend=_SandboxProcessBackend(SimpleNamespace(fs=Filesystem())))
    template.bind_run_scratch(run_id)
    invocation = template.new_invocation()
    assert invocation._backend._run_scratch_path == path

    recorder = EventRecorder(uuid4(), run_id)

    class Opened:
        def __init__(self) -> None:
            self.events = iter((recorder.record(RunStarted("live")), recorder.record(RunCompleted(1, "live"))))

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self.events)
            except StopIteration:
                template.cleanup_run_scratch()
                raise StopAsyncIteration from None

        async def aclose(self) -> None:
            return None

    class Coordinator:
        def open_owned(self, _command):
            return OpenedTurnStream(run_id, Opened())

    stream = create_turn(
        uuid4(),
        CreateTurnRequest(text="hello"),
        SimpleNamespace(headers={}),
        SimpleNamespace(user_id=uuid4(), workspace_id=uuid4()),
        Coordinator(),
        SimpleNamespace(run_heartbeat_seconds=10),
        "scratch-cleanup",
        None,
    )
    if missing_scratch:
        frames = [frame async for frame in stream]
        assert [frame.data["type"] for frame in frames[1:-1]] == ["turn_start", "turn_finish"]
        assert frames[-1].raw_data == "[DONE]"
        assert template._backend._run_scratch_path is None
    else:
        with pytest.raises(RuntimeError, match="Sandbox is unavailable"):
            _ = [frame async for frame in stream]
        assert template._backend._run_scratch_path == path
    assert deleted == [path]


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

    chunk_types = [f.data["type"] for f in frames if isinstance(f.data, dict)]
    assert chunk_types == [
        "turn_status",
        "turn_start",
        "turn_status",
        "step_start",
        "reasoning",
        "reasoning",
        "code",
        "tool_call",
        "tool_result",
        "output",
        "step_finish",
        "text",
        "text",
        "turn_finish",
    ]
    assert frames[-1].raw_data == "[DONE]"
    assert mock_stream.closed


@pytest.mark.asyncio
async def test_run_cancellation_projects_abort_frame() -> None:
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
    cancel_frames = [d for d in data_frames if d.get("type") == "turn_cancelled"]
    assert len(cancel_frames) == 1
    assert cancel_frames[0]["reason"] == "Turn cancelled"
    assert frames[-1].raw_data == "[DONE]"


@pytest.mark.asyncio
async def test_run_failure_projects_error_and_finish_pair() -> None:
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
    assert "turn_error" in types
    assert "turn_finish" in types

    error_frame = next(d for d in data_frames if d.get("type") == "turn_error")
    finish_frame = next(d for d in data_frames if d.get("type") == "turn_finish")

    assert error_frame["message"] == "Turn execution failed"
    assert finish_frame["finishReason"] == "error"
    assert frames[-1].raw_data == "[DONE]"


def test_fastapi_turn_route_declares_v1_stream_header() -> None:
    app = create_testing_app()
    openapi_spec = app.openapi()
    path_def = openapi_spec["paths"]["/api/sessions/{session_id}/turns"]["post"]
    assert "responses" in path_def
    assert "200" in path_def["responses"]
    headers = path_def["responses"]["200"]["headers"]
    assert "x-vercel-ai-ui-message-stream" in headers
