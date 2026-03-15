from __future__ import annotations

import asyncio
import inspect
from contextlib import suppress
from typing import Any, Literal, cast

import pytest
from fastapi import WebSocketDisconnect

from fleet_rlm.models import StreamEvent
from fleet_rlm.api.routers.ws.streaming import (
    _enqueue_latest_nonblocking,
    _emit_stream_event,
    _handle_stream_error,
    _should_reload_docs_path,
)
from fleet_rlm.api.routers import ws as ws_router
from fleet_rlm.api.routers.ws.helpers import (
    _close_websocket_safely,
    _try_send_json,
)
from fleet_rlm.api.schemas import WSMessage
from tests.ui.fixtures_ui import ts


def test_should_reload_docs_path_dedupes_same_path() -> None:
    assert _should_reload_docs_path(None, None) is False
    assert _should_reload_docs_path(None, "") is False
    assert _should_reload_docs_path(None, "docs/a.txt") is True
    assert _should_reload_docs_path("docs/a.txt", "docs/a.txt") is False
    assert _should_reload_docs_path("docs/a.txt", "docs/b.txt") is True


def test_enqueue_latest_nonblocking_drops_oldest_when_full() -> None:
    queue: asyncio.Queue[int] = asyncio.Queue(maxsize=2)

    assert _enqueue_latest_nonblocking(queue, 1) is True
    assert _enqueue_latest_nonblocking(queue, 2) is True
    assert _enqueue_latest_nonblocking(queue, 3) is True

    assert queue.get_nowait() == 2
    assert queue.get_nowait() == 3


class _ClosedSendWebSocket:
    async def send_json(self, payload: Any) -> None:
        _ = payload
        raise RuntimeError(
            "Unexpected ASGI message 'websocket.send', after sending "
            "'websocket.close' or response already completed."
        )


class _ClosedCloseWebSocket:
    async def close(self, code: int = 1000) -> None:
        _ = code
        raise RuntimeError(
            "Unexpected ASGI message 'websocket.close', after sending "
            "'websocket.close' or response already completed."
        )


class _DisconnectingWebSocket:
    async def send_json(self, payload: Any) -> None:
        _ = payload
        raise WebSocketDisconnect(code=1001)


class _RecordingWebSocket:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.sent.append(payload)


class _LifecycleStub:
    def __init__(self) -> None:
        self.run_completed = False
        self.persist_error_checked = 0
        self.completed_with = None

    def raise_if_persistence_error(self) -> None:
        self.persist_error_checked += 1

    async def emit_step(self, step: Any) -> None:
        _ = step

    async def persist_step(self, step: Any) -> None:
        _ = step

    async def complete_run(
        self,
        status: Any,
        step: Any = None,
        error_json: Any = None,
    ) -> None:
        self.run_completed = True
        self.completed_with = {
            "status": status,
            "step": step,
            "error_json": error_json,
        }


class _HangingTerminalLifecycle(_LifecycleStub):
    async def complete_run(
        self,
        status: Any,
        step: Any = None,
        error_json: Any = None,
    ) -> None:
        self.completed_with = {
            "status": status,
            "step": step,
            "error_json": error_json,
        }
        await asyncio.Future()


class _NoopStepBuilder:
    def from_stream_event(self, **kwargs: Any) -> None:
        _ = kwargs
        return None


def test_try_send_json_returns_false_after_websocket_close() -> None:
    assert (
        asyncio.run(_try_send_json(cast(Any, _ClosedSendWebSocket()), {"ok": True}))
        is False
    )


def test_try_send_json_returns_false_on_disconnect() -> None:
    assert (
        asyncio.run(_try_send_json(cast(Any, _DisconnectingWebSocket()), {"ok": True}))
        is False
    )


def test_close_websocket_safely_swallows_duplicate_close_runtime_error() -> None:
    asyncio.run(_close_websocket_safely(cast(Any, _ClosedCloseWebSocket()), code=1011))


def test_emit_stream_event_translates_closed_send_runtime_error_to_disconnect() -> None:
    with pytest.raises(WebSocketDisconnect):
        asyncio.run(
            _emit_stream_event(
                websocket=cast(Any, _ClosedSendWebSocket()),
                lifecycle=cast(Any, _LifecycleStub()),
                step_builder=cast(Any, _NoopStepBuilder()),
                event=StreamEvent(kind="assistant_token", text="hi", timestamp=ts()),
                persist_session_state=_noop_persist,
            )
        )


def test_handle_stream_error_ignores_closed_socket_during_error_send() -> None:
    lifecycle = _LifecycleStub()

    asyncio.run(
        _handle_stream_error(
            websocket=cast(Any, _ClosedSendWebSocket()),
            lifecycle=cast(Any, lifecycle),
            step_builder=cast(Any, _NoopStepBuilder()),
            exc=RuntimeError("boom"),
        )
    )

    assert lifecycle.run_completed is True
    assert lifecycle.completed_with is not None


def test_emit_stream_event_sends_terminal_error_before_run_completion() -> None:
    async def scenario() -> None:
        websocket = _RecordingWebSocket()
        lifecycle = _HangingTerminalLifecycle()
        task = asyncio.create_task(
            _emit_stream_event(
                websocket=cast(Any, websocket),
                lifecycle=cast(Any, lifecycle),
                step_builder=cast(Any, _NoopStepBuilder()),
                event=StreamEvent(kind="error", text="invalid api key", timestamp=ts()),
                persist_session_state=_noop_persist,
            )
        )

        deadline = asyncio.get_running_loop().time() + 0.2
        while not websocket.sent and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)

        assert websocket.sent
        assert websocket.sent[0]["type"] == "event"
        assert websocket.sent[0]["data"]["kind"] == "error"
        assert websocket.sent[0]["data"]["text"] == "invalid api key"

        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("kind", "expected_include_volume_save"),
    [("cancelled", True), ("error", True)],
)
def test_emit_stream_event_persists_terminal_cancelled_and_error_events(
    kind: Literal["cancelled", "error"],
    expected_include_volume_save: bool,
) -> None:
    async def scenario() -> None:
        websocket = _RecordingWebSocket()
        lifecycle = _HangingTerminalLifecycle()
        persist_calls: list[bool] = []

        async def persist_session_state(*, include_volume_save: bool = True) -> None:
            persist_calls.append(include_volume_save)

        task = asyncio.create_task(
            _emit_stream_event(
                websocket=cast(Any, websocket),
                lifecycle=cast(Any, lifecycle),
                step_builder=cast(Any, _NoopStepBuilder()),
                event=StreamEvent(kind=kind, text=f"{kind} turn", timestamp=ts()),
                persist_session_state=persist_session_state,
            )
        )

        deadline = asyncio.get_running_loop().time() + 0.2
        while (
            not websocket.sent or not persist_calls
        ) and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)

        assert websocket.sent
        assert websocket.sent[0]["data"]["kind"] == kind
        assert persist_calls == [expected_include_volume_save]

        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    asyncio.run(scenario())


def test_chat_stream_local_persist_wrapper_calls_shared_persist_helper() -> None:
    source = inspect.getsource(ws_router.chat_streaming)
    start = source.index("async def local_persist(")
    end = source.index("await _chat_message_loop(", start)
    local_persist_block = source[start:end]

    assert "await persist_session_state(" in local_persist_block
    assert "await local_persist(" not in local_persist_block


def test_ws_message_accepts_execution_mode() -> None:
    message = WSMessage(type="message", content="hello", execution_mode="tools_only")

    assert message.execution_mode == "tools_only"


def test_ws_message_accepts_daytona_runtime_fields() -> None:
    message = WSMessage(
        type="message",
        content="hello",
        runtime_mode="daytona_pilot",
        repo_url="https://github.com/qredence/fleet-rlm.git",
        repo_ref="main",
        batch_concurrency=6,
    )

    assert message.runtime_mode == "daytona_pilot"
    assert message.repo_url == "https://github.com/qredence/fleet-rlm.git"
    assert message.repo_ref == "main"
    assert message.batch_concurrency == 6


async def _noop_persist(*, include_volume_save: bool = True) -> None:
    _ = include_volume_save
