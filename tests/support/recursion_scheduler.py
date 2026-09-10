"""Real application-loop composition for synchronous recursion unit callers."""

from __future__ import annotations

import asyncio
import threading
from typing import Any

from fleet_rlm.rlm.recursion import ChildAsyncScheduler
from fleet_rlm.rlm.recursion import RecursiveRLMExecutor as ProductionRecursiveRLMExecutor

_loop: asyncio.AbstractEventLoop | None = None
_thread: threading.Thread | None = None


def application_loop() -> asyncio.AbstractEventLoop:
    global _loop, _thread
    if _loop is None:
        ready = threading.Event()
        loop = asyncio.new_event_loop()

        def serve() -> None:
            asyncio.set_event_loop(loop)
            loop.call_soon(ready.set)
            loop.run_forever()
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.run_until_complete(loop.shutdown_default_executor())
            loop.close()

        _loop = loop
        _thread = threading.Thread(target=serve, name="test-recursion-application", daemon=True)
        _thread.start()
        assert ready.wait(5)
    return _loop


def close_application_loop() -> None:
    global _loop, _thread
    if _loop is not None:
        _loop.call_soon_threadsafe(_loop.stop)
        assert _thread is not None
        _thread.join(timeout=10)
        assert not _thread.is_alive(), "test child ownership did not settle"
        _loop = None
        _thread = None


class RecursiveRLMExecutor(ProductionRecursiveRLMExecutor):
    """Inject a genuine service loop without production-owned loop threads."""

    def __init__(self, **kwargs: Any) -> None:
        if kwargs.get("scheduler") is None:
            kwargs["scheduler"] = ChildAsyncScheduler(
                max_workers=kwargs["options"].max_parallel_children,
                loop=application_loop(),
            )
        super().__init__(**kwargs)
