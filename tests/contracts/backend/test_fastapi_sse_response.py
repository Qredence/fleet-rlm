"""Native FastAPI SSE contracts for the request-bound AI SDK UI stream."""

from __future__ import annotations

import asyncio
import inspect
import json
from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.sse import EventSourceResponse
from fastapi.testclient import TestClient

from fleet_rlm.composition.testing import create_testing_app
from fleet_rlm.rlm.events import EventRecorder, RunCompleted, RunStarted, RuntimeEvent

_END = object()


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


async def _start_route(opened: _ControlledOpenedTurn) -> _ASGIProbe:
    from fleet_rlm.api.routes.turns import _open_turn, router

    app = FastAPI()
    app.include_router(router)

    async def override_open_turn() -> _ControlledOpenedTurn:
        return opened

    app.dependency_overrides[_open_turn] = override_open_turn
    incoming: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    incoming.put_nowait({"type": "http.request", "body": b"", "more_body": False})
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
        "headers": [(b"host", b"fleet.test")],
        "client": ("127.0.0.1", 1),
        "server": ("fleet.test", 80),
        "root_path": "",
    }
    task = asyncio.create_task(app(scope, receive, send))
    return _ASGIProbe(task, incoming, bodies, messages)


async def _wait_for_ping(probe: _ASGIProbe, count: int = 1) -> None:
    seen = 0
    while seen < count:
        body = await asyncio.wait_for(probe.bodies.get(), timeout=1)
        if body == b": ping\n\n":
            seen += 1


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
    assert UUID(response.headers["x-fleet-run-id"])
    assert "connection" not in response.headers


@pytest.mark.asyncio
async def test_native_heartbeats_keep_one_pending_event_read_and_done_is_emitted_once(monkeypatch) -> None:
    import fastapi.routing

    monkeypatch.setattr(fastapi.routing, "_PING_INTERVAL", 0.01)
    run_id = uuid4()
    recorder = EventRecorder(run_id, uuid4())
    opened = _ControlledOpenedTurn(run_id)
    probe = await _start_route(opened)

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

    assert [chunk["type"] for chunk in chunks] == ["start", "finish"]
    assert data.count("[DONE]") == 1
    assert data[-1] == "[DONE]"
    assert opened.close_calls == 1


@pytest.mark.asyncio
async def test_native_disconnect_cancels_the_pending_read_closes_once_and_omits_done(monkeypatch) -> None:
    import fastapi.routing

    monkeypatch.setattr(fastapi.routing, "_PING_INTERVAL", 0.01)
    opened = _ControlledOpenedTurn(uuid4())
    probe = await _start_route(opened)

    await _wait_for_ping(probe)
    probe.incoming.put_nowait({"type": "http.disconnect"})
    await asyncio.wait_for(probe.task, timeout=1)

    assert b"data: [DONE]" not in probe.body
    assert opened.anext_calls == 1
    assert opened.cancelled_reads == 1
    assert opened.close_calls == 1


@pytest.mark.asyncio
async def test_native_iterator_failure_closes_once_and_does_not_emit_done() -> None:
    opened = _ControlledOpenedTurn(uuid4())
    opened.put(RuntimeError("stream failed"))
    probe = await _start_route(opened)

    with pytest.raises(BaseExceptionGroup) as raised:
        await asyncio.wait_for(probe.task, timeout=1)

    runtime_errors = raised.value.subgroup(RuntimeError)
    assert runtime_errors is not None
    assert any(str(error) == "stream failed" for error in runtime_errors.exceptions)
    assert b"data: [DONE]" not in probe.body
    assert opened.close_calls == 1
