"""Async/sync boundary helpers for Daytona integration code."""

from __future__ import annotations

import asyncio
import inspect
import threading
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar, cast, overload

T = TypeVar("T")


async def _await_if_needed(value: T | Awaitable[T]) -> T:
    """Await SDK values that are awaitable and pass through sync values."""
    if inspect.isawaitable(value):
        return await cast(Awaitable[T], value)
    return cast(T, value)


async def _run_sync_in_thread(fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
    """Run a blocking sync Daytona call without blocking the caller's event loop."""
    return await asyncio.to_thread(fn, *args, **kwargs)


@overload
def _run_async_compat(fn: Callable[..., Awaitable[T]], /, *args: Any, **kwargs: Any) -> T: ...


@overload
def _run_async_compat(fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> T: ...


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

    box: dict[str, Any] = {}

    def _runner() -> None:
        try:
            box["value"] = asyncio.run(_consume())
        except BaseException as exc:  # pragma: no cover - background boundary
            box["error"] = exc

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    thread.join()
    if "error" in box:
        raise box["error"]
    return box["value"]


__all__ = ["_await_if_needed", "_run_async_compat", "_run_sync_in_thread"]
