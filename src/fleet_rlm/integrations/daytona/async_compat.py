"""Async/sync boundary helpers for Daytona integration code."""

from __future__ import annotations

import asyncio
import atexit
import inspect
import threading
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar, cast, overload

T = TypeVar("T")


class _BackgroundAsyncRunner:
    """Run awaitables on a persistent background event loop.

    Sync compatibility shims may be invoked from threads that already own a
    running event loop (for example notebook or websocket request threads).
    ``asyncio.run`` cannot be nested there, so we keep a single daemon thread
    with its own loop and dispatch coroutines onto it.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()

    @staticmethod
    async def _consume(awaitable: Awaitable[T]) -> T:
        return await awaitable

    def _thread_main(self, ready: threading.Event) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        with self._lock:
            self._loop = loop
        ready.set()
        try:
            loop.run_forever()
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.run_until_complete(loop.shutdown_default_executor())
            asyncio.set_event_loop(None)
            loop.close()
            with self._lock:
                if self._loop is loop:
                    self._loop = None
                current_thread = threading.current_thread()
                if self._thread is current_thread:
                    self._thread = None

    def ensure_loop(self) -> asyncio.AbstractEventLoop:
        wait_ready: threading.Event
        with self._lock:
            loop = self._loop
            thread = self._thread
            if loop is not None and thread is not None and thread.is_alive() and not loop.is_closed():
                return loop
            ready = self._ready
            if thread is not None and thread.is_alive() and not ready.is_set():
                wait_ready = ready
            else:
                ready = threading.Event()
                thread = threading.Thread(
                    target=self._thread_main,
                    args=(ready,),
                    daemon=True,
                    name="daytona-async-compat",
                )
                self._ready = ready
                self._thread = thread
                self._loop = None
                thread.start()
                wait_ready = ready
        wait_ready.wait()
        with self._lock:
            loop = self._loop
            thread = self._thread
            if loop is None or thread is None or not thread.is_alive() or loop.is_closed():
                raise RuntimeError("Failed to start async compatibility runner")
            return loop

    def run(self, awaitable: Awaitable[T]) -> T:
        loop = self.ensure_loop()
        future = asyncio.run_coroutine_threadsafe(self._consume(awaitable), loop)
        return future.result()

    def close(self) -> None:
        with self._lock:
            loop = self._loop
            thread = self._thread
        if loop is None or thread is None or not thread.is_alive() or loop.is_closed():
            return
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=1.0)


_BACKGROUND_ASYNC_RUNNER = _BackgroundAsyncRunner()
atexit.register(_BACKGROUND_ASYNC_RUNNER.close)


async def _await_if_needed(value: T | Awaitable[T]) -> T:
    """Await SDK values that are awaitable and pass through sync values."""
    if inspect.isawaitable(value):
        return await cast(Awaitable[T], value)
    return cast(T, value)


async def _run_sync_in_thread(fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
    """Run a blocking sync Daytona call without blocking the caller's event loop."""
    return await asyncio.to_thread(fn, *args, **kwargs)


@overload
def _run_async_compat(fn: Callable[..., Awaitable[T]], /, *args: Any, **kwargs: Any) -> T:
    pass


@overload
def _run_async_compat(fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
    pass


def _run_async_compat(fn: Callable[..., T | Awaitable[T]], /, *args: Any, **kwargs: Any) -> T:
    """Run an async Daytona compatibility wrapper from synchronous callers.

    If no loop is running in this thread, use ``asyncio.run``. If a loop is
    already running, execute the coroutine on a short-lived background thread
    so sync compatibility APIs stay usable from notebook/websocket contexts.
    """
    result = fn(*args, **kwargs)
    if not inspect.isawaitable(result):
        return cast(T, result)

    awaitable = cast(Awaitable[T], result)

    async def _consume() -> T:
        return await awaitable

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_consume())

    return _BACKGROUND_ASYNC_RUNNER.run(_consume())


__all__ = ["_await_if_needed", "_run_async_compat", "_run_sync_in_thread"]
