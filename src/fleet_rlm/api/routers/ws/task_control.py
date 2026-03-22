"""Task and queue control helpers for websocket chat streaming."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import WebSocketDisconnect


def should_reload_docs_path(last_docs_path: str | None, docs_path: str | None) -> bool:
    """Return True when a docs path is provided and differs from the last loaded path."""
    candidate = (docs_path or "").strip()
    if not candidate:
        return False
    return candidate != (last_docs_path or "")


def enqueue_latest_nonblocking(
    queue: asyncio.Queue[Any],
    item: Any,
) -> bool:
    """Enqueue without blocking, dropping the oldest item when the queue is full."""
    try:
        queue.put_nowait(item)
        return True
    except asyncio.QueueFull:
        pass

    try:
        _ = queue.get_nowait()
    except asyncio.QueueEmpty:
        return False

    try:
        queue.put_nowait(item)
        return True
    except asyncio.QueueFull:
        return False


def cancelled_event_payload(message: str = "Request cancelled.") -> dict[str, Any]:
    """Build the websocket event payload for cancellation notifications."""
    return {
        "type": "event",
        "data": {
            "kind": "cancelled",
            "text": message,
            "payload": None,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "version": 2,
            "event_id": str(uuid.uuid4()),
        },
    }


async def cancel_task(task: asyncio.Task[object] | None) -> None:
    """Cancel an in-flight task and swallow expected shutdown exceptions."""
    if task is None or task.done():
        return

    task.cancel()
    outcomes = await asyncio.gather(task, return_exceptions=True)
    if not outcomes:
        return

    outcome = outcomes[0]
    if isinstance(outcome, (asyncio.CancelledError, WebSocketDisconnect)):
        return
    if isinstance(outcome, BaseException):
        raise outcome
