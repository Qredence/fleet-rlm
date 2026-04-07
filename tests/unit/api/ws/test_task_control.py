from __future__ import annotations

import asyncio
from typing import cast

import pytest
from fastapi import WebSocketDisconnect

from fleet_rlm.api.routers.ws.task_control import (
    cancel_task,
    cancelled_event_payload,
    enqueue_latest_nonblocking,
    should_reload_docs_path,
)


def test_should_reload_docs_path_dedupes_same_path() -> None:
    assert should_reload_docs_path(None, None) is False
    assert should_reload_docs_path(None, "") is False
    assert should_reload_docs_path(None, "docs/a.txt") is True
    assert should_reload_docs_path("docs/a.txt", "docs/a.txt") is False
    assert should_reload_docs_path("docs/a.txt", "docs/b.txt") is True


def test_enqueue_latest_nonblocking_drops_oldest_when_full() -> None:
    queue: asyncio.Queue[int] = asyncio.Queue(maxsize=2)

    assert enqueue_latest_nonblocking(queue, 1) is True
    assert enqueue_latest_nonblocking(queue, 2) is True
    assert enqueue_latest_nonblocking(queue, 3) is True

    assert queue.get_nowait() == 2
    assert queue.get_nowait() == 3


def test_cancelled_event_payload_structure() -> None:
    payload = cancelled_event_payload("stop now")

    assert payload["type"] == "event"
    assert payload["data"]["kind"] == "cancelled"
    assert payload["data"]["text"] == "stop now"
    assert payload["data"]["payload"] is None
    assert payload["data"]["version"] == 2
    assert isinstance(payload["data"]["event_id"], str) and payload["data"]["event_id"]
    assert isinstance(payload["data"]["timestamp"], str)


def test_cancelled_event_payload_default_message() -> None:
    payload = cancelled_event_payload()

    assert payload["data"]["text"] == "Request cancelled."


def test_cancel_task_handles_none() -> None:
    asyncio.run(cancel_task(None))


def test_cancel_task_handles_already_completed() -> None:
    async def scenario() -> None:
        async def done() -> None:
            return None

        task = asyncio.create_task(done())
        await task
        await cancel_task(cast(asyncio.Task[object], task))

    asyncio.run(scenario())


def test_cancel_task_swallows_websocket_disconnect() -> None:
    async def scenario() -> None:
        async def disconnect_on_cancel() -> None:
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError as exc:
                raise WebSocketDisconnect(code=1001) from exc

        task = asyncio.create_task(disconnect_on_cancel())
        await asyncio.sleep(0)
        await cancel_task(cast(asyncio.Task[object], task))

    asyncio.run(scenario())


def test_cancel_task_reraises_unexpected_exceptions() -> None:
    async def scenario() -> None:
        async def fail_on_cancel() -> None:
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError as exc:
                raise RuntimeError("boom") from exc

        task = asyncio.create_task(fail_on_cancel())
        await asyncio.sleep(0)
        with pytest.raises(RuntimeError, match="boom"):
            await cancel_task(cast(asyncio.Task[object], task))

    asyncio.run(scenario())
