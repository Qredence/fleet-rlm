"""Async/sync compatibility bridge for the Daytona integration."""

from __future__ import annotations

import asyncio
import atexit
import inspect
import threading
from typing import Any, Awaitable, Callable, Coroutine, TypeVar, cast

_T = TypeVar("_T")


class _AsyncCompatRunner:
    """Keep one background event loop alive for sync Daytona compatibility calls."""

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._started = threading.Event()
        self._lock = threading.Lock()

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._thread_id = threading.get_ident()
        self._started.set()
        try:
            loop.run_forever()
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
            loop.close()

    def _ensure_started(self) -> asyncio.AbstractEventLoop:
        loop = self._loop
        thread = self._thread
        if (
            loop is not None
            and not loop.is_closed()
            and thread is not None
            and thread.is_alive()
        ):
            return loop

        with self._lock:
            loop = self._loop
            thread = self._thread
            if (
                loop is not None
                and not loop.is_closed()
                and thread is not None
                and thread.is_alive()
            ):
                return loop

            self._loop = None
            self._thread_id = None
            self._started.clear()
            self._thread = threading.Thread(
                target=self._thread_main,
                name="daytona-async-compat",
                daemon=True,
            )
            self._thread.start()

        self._started.wait()
        loop = self._loop
        if loop is None or loop.is_closed():
            raise RuntimeError("Async compatibility runner failed to start.")
        return loop

    def run(self, awaitable: Awaitable[_T]) -> _T:
        loop = self._ensure_started()
        if self._thread_id == threading.get_ident():
            raise RuntimeError(
                "Async compatibility runner cannot be called from its own loop thread."
            )
        future = asyncio.run_coroutine_threadsafe(
            cast(Coroutine[Any, Any, _T], awaitable),
            loop,
        )
        return future.result()

    def shutdown(self) -> None:
        loop = self._loop
        thread = self._thread
        if loop is None or loop.is_closed() or thread is None:
            return
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=1)
        self._loop = None
        self._thread = None
        self._thread_id = None


_ASYNC_COMPAT_RUNNER = _AsyncCompatRunner()
atexit.register(_ASYNC_COMPAT_RUNNER.shutdown)


def _run_async_compat(
    async_fn: Callable[..., Awaitable[_T]],
    /,
    *args: Any,
    **kwargs: Any,
) -> _T:
    """Run an async implementation from sync code on one persistent loop."""
    return _ASYNC_COMPAT_RUNNER.run(async_fn(*args, **kwargs))


async def _await_if_needed(value: _T | Awaitable[_T]) -> _T:
    if inspect.isawaitable(value):
        awaited = await cast(Any, value)
        return cast(_T, awaited)
    return value


def _sync(method: Callable[..., Awaitable[_T]]) -> Callable[..., _T]:
    """Create a sync shim that delegates to *method* via ``_run_async_compat``."""

    def wrapper(self: Any, *args: Any, **kwargs: Any) -> _T:
        return _run_async_compat(method, self, *args, **kwargs)

    return wrapper


def sync_mirror(
    *,
    overrides: dict[str, str] | None = None,
    skip: set[str] | None = None,
) -> Callable[[type], type]:
    """Class decorator that auto-generates sync shims for async methods.

    For every method ``afoo`` that is not in *skip* and has no matching
    sync method already defined, a sync method ``foo`` is generated that
    delegates to ``_run_async_compat(self.afoo, *args, **kwargs)``.

    *overrides* maps async method names to custom sync method names when
    the default prefix-stripping rule does not apply.
    """
    overrides = overrides or {}
    skip = skip or set()

    def decorator(cls: type) -> type:
        for name, method in list(cls.__dict__.items()):
            if name in skip:
                continue
            if not inspect.iscoroutinefunction(method):
                continue
            sync_name = overrides.get(name)
            if sync_name is None:
                if name.startswith("a") and len(name) > 1:
                    sync_name = name[1:]
                else:
                    continue
            if sync_name in cls.__dict__:
                continue

            def make_sync_method(am: Callable[..., Awaitable[_T]]) -> Callable[..., _T]:
                def sync_method(self: Any, *args: Any, **kwargs: Any) -> _T:
                    return _run_async_compat(am, self, *args, **kwargs)

                return sync_method

            setattr(cls, sync_name, make_sync_method(method))
        return cls

    return decorator
