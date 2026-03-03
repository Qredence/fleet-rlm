from __future__ import annotations

import asyncio
import inspect

from fleet_rlm.server.routers.ws.streaming import (
    _enqueue_latest_nonblocking,
    _should_reload_docs_path,
)
from fleet_rlm.server.routers import ws as ws_router


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


def test_chat_stream_local_persist_wrapper_calls_shared_persist_helper() -> None:
    source = inspect.getsource(ws_router.chat_streaming)
    start = source.index("async def local_persist(")
    end = source.index("\n            await _chat_message_loop(", start)
    local_persist_block = source[start:end]

    assert "await persist_session_state(" in local_persist_block
    assert "await local_persist(" not in local_persist_block
