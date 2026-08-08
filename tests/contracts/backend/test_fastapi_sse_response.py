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

from fleet_rlm.chat.turn_lifecycle import TurnInProgressError
from fleet_rlm.composition.testing import create_testing_app
from fleet_rlm.rlm.events import EventRecorder, RLMReasoning, RunCompleted, RunStarted, RuntimeEvent

_END = object()

PREPARATION_PRELUDE = {
    "type": "data-status",
    "data": {"phase": "preparation", "status": "running", "message": None},
    "transient": True,
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

    async def open(self, command: object) -> _ControlledOpenedTurn:
        self.open_calls += 1
        self.command = command
        if self._gate is not None:
            await self._gate.wait()
        if self._error is not None:
            raise self._error
        assert self._opened is not None
        return self._opened


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
    from fleet_rlm.api.dependencies import get_turn_coordinator
    from fleet_rlm.api.routes.turns import router

    app = FastAPI()
    app.state.settings = SimpleNamespace(run_heartbeat_seconds=10)
    app.include_router(router)

    app.dependency_overrides[get_turn_coordinator] = lambda: coordinator
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
    assert chunks[1]["type"] == "start"
    assert chunks[-1]["type"] == "finish"


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
    assert json.loads(body.decode().removeprefix("data: ")) == PREPARATION_PRELUDE
    # Nothing else is emitted while the claim and preparation remain unresolved.
    assert coordinator.open_calls == 1
    gate.set()
    recorder = EventRecorder(opened.run_id, uuid4())
    opened.put(recorder.record(RunStarted("live")), recorder.record(RunCompleted(1, "live")), _END)
    await asyncio.wait_for(probe.task, timeout=1)
    assert _data_frames(probe.body)[-1] == "[DONE]"


@pytest.mark.asyncio
async def test_open_failure_projects_closed_error_and_finish_frames() -> None:
    coordinator = _ControlledCoordinator(error=TurnInProgressError("Turn is already running"))
    probe = await _start_route(coordinator)

    await asyncio.wait_for(probe.task, timeout=1)

    chunks = _data_chunks(probe.body)
    assert chunks[0] == PREPARATION_PRELUDE
    assert chunks[1:] == [
        {"type": "error", "errorText": "A Turn is already running"},
        {"type": "finish", "finishReason": "error"},
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
    frames = probe.body.decode().split("\n\n")
    data = [frame.removeprefix("data: ") for frame in frames if frame.startswith("data: ")]
    chunks = [json.loads(value) for value in data if value != "[DONE]"]

    assert [chunk["type"] for chunk in chunks] == ["data-status", "start", "finish"]
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

    assert types[0] == "data-status"
    assert chunks[0] == PREPARATION_PRELUDE
    assert types[1:5] == ["start", "reasoning-start", "reasoning-delta", "reasoning-delta"]
    assert types[5:] == ["reasoning-end", "finish"]
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
