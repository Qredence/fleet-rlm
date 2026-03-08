from __future__ import annotations

import asyncio
import inspect

import pytest
from fastapi import WebSocketDisconnect

from fleet_rlm.models import StreamEvent
from fleet_rlm.server.routers.ws.streaming import (
    _enqueue_latest_nonblocking,
    _emit_stream_event,
    _handle_stream_error,
    _should_reload_docs_path,
)
from fleet_rlm.server.routers import ws as ws_router
from fleet_rlm.server.routers.ws.helpers import (
    _close_websocket_safely,
    _try_send_json,
)
from fleet_rlm.server.schemas import WSMessage
from tests.ui.ws._fakes import ts


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
    async def send_json(self, payload):
        _ = payload
        raise RuntimeError(
            "Unexpected ASGI message 'websocket.send', after sending "
            "'websocket.close' or response already completed."
        )


class _ClosedCloseWebSocket:
    async def close(self, code: int = 1000):
        _ = code
        raise RuntimeError(
            "Unexpected ASGI message 'websocket.close', after sending "
            "'websocket.close' or response already completed."
        )


class _DisconnectingWebSocket:
    async def send_json(self, payload):
        _ = payload
        raise WebSocketDisconnect(code=1001)


class _LifecycleStub:
    def __init__(self) -> None:
        self.run_completed = False
        self.persist_error_checked = 0
        self.completed_with = None

    def raise_if_persistence_error(self) -> None:
        self.persist_error_checked += 1

    async def emit_step(self, step) -> None:
        _ = step

    async def persist_step(self, step) -> None:
        _ = step

    async def complete_run(self, status, step=None, error_json=None) -> None:
        self.run_completed = True
        self.completed_with = {
            "status": status,
            "step": step,
            "error_json": error_json,
        }


class _NoopStepBuilder:
    def from_stream_event(self, **kwargs):
        _ = kwargs
        return None


def test_try_send_json_returns_false_after_websocket_close() -> None:
    assert asyncio.run(_try_send_json(_ClosedSendWebSocket(), {"ok": True})) is False


def test_try_send_json_returns_false_on_disconnect() -> None:
    assert asyncio.run(_try_send_json(_DisconnectingWebSocket(), {"ok": True})) is False


def test_close_websocket_safely_swallows_duplicate_close_runtime_error() -> None:
    asyncio.run(_close_websocket_safely(_ClosedCloseWebSocket(), code=1011))


def test_emit_stream_event_translates_closed_send_runtime_error_to_disconnect() -> None:
    with pytest.raises(WebSocketDisconnect):
        asyncio.run(
            _emit_stream_event(
                websocket=_ClosedSendWebSocket(),
                lifecycle=_LifecycleStub(),
                step_builder=_NoopStepBuilder(),
                event=StreamEvent(kind="assistant_token", text="hi", timestamp=ts()),
                persist_session_state=_noop_persist,
            )
        )


def test_handle_stream_error_ignores_closed_socket_during_error_send() -> None:
    lifecycle = _LifecycleStub()

    asyncio.run(
        _handle_stream_error(
            websocket=_ClosedSendWebSocket(),
            lifecycle=lifecycle,
            step_builder=_NoopStepBuilder(),
            exc=RuntimeError("boom"),
        )
    )

    assert lifecycle.run_completed is True
    assert lifecycle.completed_with is not None


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


async def _noop_persist(*, include_volume_save: bool = True) -> None:
    _ = include_volume_save
