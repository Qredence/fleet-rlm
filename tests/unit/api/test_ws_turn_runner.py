from __future__ import annotations

from typing import Any

import pytest

from fleet_rlm.api.events import ExecutionStepBuilder
from fleet_rlm.api.routers.ws.turn_runner import _emit_stream_event
from fleet_rlm.api.runtime_services.run_lifecycle import ExecutionLifecycleManager
from fleet_rlm.runtime.schemas import StreamEvent


class _FakeEmitter:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def emit(self, event: Any) -> None:
        self.events.append(event)


class _FakeWebSocket:
    def __init__(self) -> None:
        self.messages: list[Any] = []

    async def send_json(self, payload: Any) -> None:
        self.messages.append(payload)


def _lifecycle() -> tuple[ExecutionLifecycleManager, ExecutionStepBuilder, _FakeEmitter]:
    step_builder = ExecutionStepBuilder(run_id="run-1")
    emitter = _FakeEmitter()
    lifecycle = ExecutionLifecycleManager(
        run_id="run-1",
        workspace_id="workspace",
        user_id="user",
        session_id="session",
        execution_emitter=emitter,
        step_builder=step_builder,
    )
    return lifecycle, step_builder, emitter


async def _persist_session_state(**kwargs: Any) -> None:
    _ = kwargs


@pytest.mark.asyncio
async def test_emit_stream_event_sends_non_terminal_frame_to_websocket() -> None:
    lifecycle, step_builder, emitter = _lifecycle()
    websocket = _FakeWebSocket()

    await _emit_stream_event(
        websocket=websocket,  # type: ignore[arg-type]
        lifecycle=lifecycle,
        step_builder=step_builder,
        event=StreamEvent(kind="status", text="Starting turn..."),
        persist_session_state=_persist_session_state,
        request_message="hello",
        execution_emitter=emitter,  # type: ignore[arg-type]
    )

    assert websocket.messages
    assert websocket.messages[0]["type"] == "event"
    frame = websocket.messages[0]["data"]
    assert frame["kind"] == "execution_step"
    assert frame["text"] == "Starting turn..."
    assert frame["payload"]["source_type"] == "status"
    assert lifecycle.run_completed is False


@pytest.mark.asyncio
async def test_emit_stream_event_sends_terminal_frame_before_completion() -> None:
    lifecycle, step_builder, emitter = _lifecycle()
    websocket = _FakeWebSocket()

    await _emit_stream_event(
        websocket=websocket,  # type: ignore[arg-type]
        lifecycle=lifecycle,
        step_builder=step_builder,
        event=StreamEvent(kind="done", text="done", payload={"history_turns": 1}),
        persist_session_state=_persist_session_state,
        request_message="hello",
        execution_emitter=emitter,  # type: ignore[arg-type]
    )

    assert websocket.messages
    assert websocket.messages[0]["type"] == "event"
    frame = websocket.messages[0]["data"]
    assert frame["kind"] == "execution_completed"
    assert frame["text"] == "done"
    assert frame["payload"]["source_type"] == "turn_completed"
    assert frame["payload"]["final_artifact"] is not None
    assert frame["payload"]["run_summary"]["status"] == "completed"
    assert lifecycle.run_completed is True
