from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any, cast

from fleet_rlm.api.routers.ws.terminal import (
    build_stream_event_dict,
    handle_terminal_stream_event,
)
from fleet_rlm.runtime.models import StreamEvent
from tests.ui.fixtures_ui import ts


class _RecordingWebSocket:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.sent.append(payload)


class _LifecycleStub:
    def __init__(self) -> None:
        self.run_id = "test-run"
        self.run_completed = False
        self.completed_with: dict[str, Any] | None = None

    async def complete_run(
        self,
        status: Any,
        step: Any = None,
        error_json: Any = None,
        summary: Any = None,
    ) -> None:
        self.run_completed = True
        self.completed_with = {
            "status": status,
            "step": step,
            "error_json": error_json,
            "summary": summary,
        }


class _HangingLifecycle(_LifecycleStub):
    async def complete_run(
        self,
        status: Any,
        step: Any = None,
        error_json: Any = None,
        summary: Any = None,
    ) -> None:
        self.completed_with = {
            "status": status,
            "step": step,
            "error_json": error_json,
            "summary": summary,
        }
        await asyncio.Future()


def test_build_stream_event_dict_serializes_core_fields() -> None:
    event = StreamEvent(
        kind="status", text="hello", payload={"ok": True}, timestamp=ts()
    )

    event_dict = build_stream_event_dict(event=event, payload=event.payload)

    assert event_dict["kind"] == "status"
    assert event_dict["text"] == "hello"
    assert event_dict["payload"] == {"ok": True}
    assert event_dict["version"] == 2
    assert isinstance(event_dict["event_id"], str) and event_dict["event_id"]


def test_handle_terminal_stream_event_final_completes_and_sends() -> None:
    async def scenario() -> None:
        websocket = _RecordingWebSocket()
        lifecycle = _LifecycleStub()
        persist_calls: list[bool] = []
        event = StreamEvent(kind="final", text="done", timestamp=ts())

        async def persist_session_state(*, include_volume_save: bool = True) -> None:
            persist_calls.append(include_volume_save)

        await handle_terminal_stream_event(
            websocket=cast(Any, websocket),
            lifecycle=cast(Any, lifecycle),
            event=event,
            event_dict=build_stream_event_dict(event=event, payload=event.payload),
            step=None,
            persist_session_state=cast(Any, persist_session_state),
            request_message="hello",
        )

        assert persist_calls == [True]
        assert lifecycle.run_completed is True
        assert websocket.sent[0]["data"]["kind"] == "final"
        assert lifecycle.completed_with is not None
        assert lifecycle.completed_with["summary"]["status"] == "completed"

    asyncio.run(scenario())


def test_handle_terminal_stream_event_error_sends_before_completion() -> None:
    async def scenario() -> None:
        websocket = _RecordingWebSocket()
        lifecycle = _HangingLifecycle()
        event = StreamEvent(kind="error", text="boom", timestamp=ts())

        async def persist_session_state(*, include_volume_save: bool = True) -> None:
            _ = include_volume_save

        task = asyncio.create_task(
            handle_terminal_stream_event(
                websocket=cast(Any, websocket),
                lifecycle=cast(Any, lifecycle),
                event=event,
                event_dict=build_stream_event_dict(event=event, payload=event.payload),
                step=None,
                persist_session_state=cast(Any, persist_session_state),
                request_message="hello",
            )
        )

        deadline = asyncio.get_running_loop().time() + 0.2
        while not websocket.sent and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)

        assert websocket.sent
        assert websocket.sent[0]["data"]["kind"] == "error"

        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
