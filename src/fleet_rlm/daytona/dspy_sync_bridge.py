"""Synchronous DSPy view over async Daytona sandbox SDK objects.

DSPy's interpreter port is synchronous. Fleet's Daytona composition owns
async ``AsyncSandbox`` objects on the composition/uvicorn loop. This module
is the explicit transport bridge: worker threads post SDK coroutines to the
registered service loop and block until they settle.

This is not a Daytona synchronous client stack. Provider authority remains
async; only the DSPy worker seam sees the sync view.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
from typing import Any

from fleet_rlm.daytona.errors import DaytonaAdapterError

_BRIDGE_SERVICE_POLL_S = 0.5


class SyncBridgeDispatcher:
    """Composition-owned routing authority for sync-view SDK coroutines.

    Each Daytona composition owns exactly one dispatcher and injects it into
    every :func:`sync_sandbox` view it creates, so multiple app/test
    compositions in one process cannot overwrite each other's bridge
    authority. The dispatcher carries the same RC-7 guarantee as the legacy
    module-global: its registered loop never performs nested synchronous
    waits, so posted coroutines are always serviced. Closing a view
    tombstones only that view's ``_SyncBridgeLoop``; the dispatcher itself is
    shared composition state and must never be tombstoned per-view.
    """

    def __init__(self) -> None:
        self._service_loop: asyncio.AbstractEventLoop | None = None

    def set_loop(self, loop: asyncio.AbstractEventLoop | None) -> None:
        """Register the loop servicing sync-view SDK coroutines for this composition."""
        self._service_loop = loop

    def clear_loop(self, loop: asyncio.AbstractEventLoop | None) -> None:
        """Unregister only when this dispatcher still routes onto ``loop``.

        A disposing composition can never clear another composition's loop:
        the identity check makes retreat behaviorally safe under overlapping
        lifespans.
        """
        if loop is not None and self._service_loop is not loop:
            return
        self._service_loop = None

    def service_loop(self) -> asyncio.AbstractEventLoop | None:
        """Return the registered composition loop, if any."""
        return self._service_loop


class _SyncBridgeLoop:
    """Service-loop routing and close state for one synchronous Daytona bridge.

    Posted SDK coroutines run on the composition-owned service loop exposed by
    the injected dispatcher; when no dispatcher is injected (e.g. private-test
    compositions that never install the Daytona inventory) the bridge falls
    back to its caller-captured loop. The bridge owns no threads, so Turns cannot leak daemon threads;
    :meth:`close` tombstones the bridge so late calls fail typed-fast instead
    of posting to a service loop after lease release.
    """

    def __init__(
        self,
        *,
        caller_loop: asyncio.AbstractEventLoop | None,
        dispatcher: SyncBridgeDispatcher | None = None,
    ) -> None:
        self._caller_loop = caller_loop
        self._dispatcher = dispatcher
        self._closed = False

    def _bridge_error(self, message: str) -> DaytonaAdapterError:
        return DaytonaAdapterError(message=message, cause_type="InterpreterBridgeError")

    def close(self) -> None:
        """Tombstone the bridge; further calls fail fast until start()."""
        self._closed = True

    def start(self) -> None:
        """Clear the close tombstone (survives close/reopen)."""
        self._closed = False

    def service_loop(self) -> asyncio.AbstractEventLoop | None:
        """Resolve the loop servicing this bridge: injected dispatcher first.

        Resolution order pins authority at view creation: an explicitly
        injected composition dispatcher, then the legacy process-default
        dispatcher, then the caller-captured loop (legacy/test fallback).
        """
        if self._dispatcher is not None:
            registered = self._dispatcher.service_loop()
            if registered is not None:
                return registered
        return self._caller_loop

    def run(self, awaitable: Any) -> Any:
        """Post one awaitable on the service loop and block until it settles."""
        if self._closed:
            if inspect.iscoroutine(awaitable):
                awaitable.close()
            raise self._bridge_error("synchronous Daytona bridge is closed")
        loop = self.service_loop()
        if loop is None or loop.is_closed():
            if inspect.iscoroutine(awaitable):
                awaitable.close()
            raise self._bridge_error("synchronous Daytona bridge service loop is unavailable")
        try:
            future = asyncio.run_coroutine_threadsafe(awaitable, loop)
        except RuntimeError as exc:
            if inspect.iscoroutine(awaitable):
                awaitable.close()
            raise self._bridge_error("synchronous Daytona bridge service loop is unavailable") from exc
        while True:
            try:
                return future.result(timeout=_BRIDGE_SERVICE_POLL_S)
            except TimeoutError:
                if loop.is_closed() or not loop.is_running():
                    future.cancel()
                    if inspect.iscoroutine(awaitable):
                        with contextlib.suppress(Exception):
                            awaitable.close()
                    raise self._bridge_error("synchronous Daytona bridge service loop stopped") from None


def _sync_await(
    awaitable: Any,
    owner: _SyncBridgeLoop,
    guard_loop: asyncio.AbstractEventLoop | None = None,
) -> Any:
    """Run one async SDK operation on the composition-wide bridge service loop.

    ``guard_loop`` anchors the legacy fail-fast contract: the loop a bridge
    was declared against (and the resolved service loop itself) may never call
    the bridge synchronously, because that loop's thread is the one that would
    have to service the call.
    """
    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None
    if current_loop is not None and (current_loop is guard_loop or current_loop is owner.service_loop()):
        if inspect.iscoroutine(awaitable):
            awaitable.close()
        raise DaytonaAdapterError(
            message="synchronous Daytona bridge called from its owning event loop",
            cause_type="InterpreterThreadError",
        )
    if not inspect.isawaitable(awaitable):
        raise DaytonaAdapterError(
            message="synchronous Daytona bridge requires an async SDK operation",
            cause_type="InterpreterBridgeContractError",
        )
    return owner.run(awaitable)


class _SyncCodeInterpreter:
    def __init__(
        self,
        service: Any,
        owner: _SyncBridgeLoop,
        guard_loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        self._service = service
        self._owner = owner
        self._guard_loop = guard_loop

    def create_context(self, **kwargs: Any) -> Any:
        return _sync_await(self._service.create_context(**kwargs), self._owner, self._guard_loop)

    def run_code(self, code: str, **kwargs: Any) -> Any:
        return _sync_await(self._service.run_code(code, **kwargs), self._owner, self._guard_loop)

    def delete_context(self, context: Any, **kwargs: Any) -> None:
        _sync_await(self._service.delete_context(context, **kwargs), self._owner, self._guard_loop)


class _SyncProcess:
    def __init__(
        self,
        service: Any,
        owner: _SyncBridgeLoop,
        guard_loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        self._service = service
        self._owner = owner
        self._guard_loop = guard_loop

    def code_run(self, code: str, **kwargs: Any) -> Any:
        return _sync_await(self._service.code_run(code, **kwargs), self._owner, self._guard_loop)

    def create_session(self, session_id: str, **kwargs: Any) -> Any:
        return _sync_await(self._service.create_session(session_id, **kwargs), self._owner, self._guard_loop)

    def execute_session_command(self, session_id: str, request: Any, **kwargs: Any) -> Any:
        return _sync_await(
            self._service.execute_session_command(session_id, request, **kwargs), self._owner, self._guard_loop
        )

    def delete_session(self, session_id: str, **kwargs: Any) -> Any:
        return _sync_await(self._service.delete_session(session_id, **kwargs), self._owner, self._guard_loop)


class _SyncFileSystem:
    def __init__(
        self,
        service: Any,
        owner: _SyncBridgeLoop,
        guard_loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        self._service = service
        self._owner = owner
        self._guard_loop = guard_loop

    def upload_file(self, content: bytes, path: str, **kwargs: Any) -> Any:
        return _sync_await(self._service.upload_file(content, path, **kwargs), self._owner, self._guard_loop)

    def download_file(self, path: str, **kwargs: Any) -> bytes:
        return _sync_await(self._service.download_file(path, **kwargs), self._owner, self._guard_loop)

    def delete_file(self, path: str, **kwargs: Any) -> Any:
        return _sync_await(self._service.delete_file(path, **kwargs), self._owner, self._guard_loop)

    def list_files(self, path: str, **kwargs: Any) -> Any:
        return _sync_await(self._service.list_files(path, **kwargs), self._owner, self._guard_loop)


class _DSPySyncSandboxView:
    """Explicit synchronous Daytona view used only by DSPy worker execution.

    Routes SDK coroutines through the composition-wide bridge service loop
    exposed by the injected dispatcher; the ``loop`` constructor argument
    anchors the fail-fast owning-loop guard and is the fallback target when
    no dispatcher is injected.
    """

    def __init__(
        self,
        sandbox: Any,
        loop: asyncio.AbstractEventLoop,
        dispatcher: SyncBridgeDispatcher | None = None,
    ) -> None:
        owner = _SyncBridgeLoop(caller_loop=loop, dispatcher=dispatcher)
        if hasattr(sandbox, "code_interpreter"):
            self.code_interpreter = _SyncCodeInterpreter(sandbox.code_interpreter, owner, loop)
        if hasattr(sandbox, "process"):
            self.process = _SyncProcess(sandbox.process, owner, loop)
        if hasattr(sandbox, "fs"):
            self.fs = _SyncFileSystem(sandbox.fs, owner, loop)
        self._sandbox = sandbox
        self._loop = loop
        self._owner = owner

    @property
    def id(self) -> object:
        """Expose the wrapped provider identity to per-Sandbox host caches."""
        return getattr(self._sandbox, "id", None)

    def get_preview_link(self, port: int, **kwargs: Any) -> Any:
        return _sync_await(self._sandbox.get_preview_link(port, **kwargs), self._owner, self._loop)

    def close(self) -> None:
        """Tombstone the bridge; further calls fail fast until start()."""
        self._owner.close()

    def start(self) -> None:
        """Clear the close tombstone after close()."""
        self._owner.start()


def sync_sandbox(
    sandbox: Any,
    loop: asyncio.AbstractEventLoop,
    dispatcher: SyncBridgeDispatcher | None = None,
) -> Any:
    """Return a synchronous sandbox view for DSPy worker-thread execution.

    ``dispatcher`` injects the composition-owned bridge authority (QRE-154);
    when omitted, the view falls back to its caller-captured loop. The
    concrete view type is private to this module. Callers that
    need to invalidate a view after lease release should use
    :func:`tombstone_sync_sandbox`.
    """
    if isinstance(sandbox, _DSPySyncSandboxView):
        return sandbox
    return _DSPySyncSandboxView(sandbox, loop, dispatcher)


def tombstone_sync_sandbox(sandbox: Any) -> None:
    """Tombstone a sync sandbox view so late calls fail typed-fast.

    No-op when ``sandbox`` is not a view created by :func:`sync_sandbox`.
    The shared bridge service loop outlives individual Turns.
    """
    if isinstance(sandbox, _DSPySyncSandboxView):
        sandbox.close()


__all__ = [
    "SyncBridgeDispatcher",
    "sync_sandbox",
    "tombstone_sync_sandbox",
]
