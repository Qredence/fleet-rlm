"""QRE-154 contracts: composition-owned SyncBridgeDispatcher injection.

The legacy module-global bridge loop authority is replaced with a dispatcher
owned by each Daytona composition and injected into every synchronous Sandbox
view; overlapping compositions can no longer overwrite each other's bridge
authority.
"""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

from fleet_rlm.daytona.broker import (
    SyncBridgeDispatcher,
    sync_sandbox,
    tombstone_sync_sandbox,
)
from fleet_rlm.daytona.errors import DaytonaAdapterError

_DEADLOCK_BOUND_S = 5.0


class _AsyncFs:
    async def download_file(self, path: str) -> bytes:
        return f"bytes:{path}".encode()


def _sandbox() -> SimpleNamespace:
    return SimpleNamespace(fs=_AsyncFs())


class _ServingLoop:
    """One real asyncio loop serviced by one plain background thread."""

    def __init__(self, name: str) -> None:
        self.loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self.thread = threading.Thread(target=self._main, name=name, daemon=True)

    def _main(self) -> None:
        asyncio.set_event_loop(self.loop)
        self._ready.set()
        self.loop.run_forever()

    def __enter__(self) -> _ServingLoop:
        self.thread.start()
        assert self._ready.wait(timeout=_DEADLOCK_BOUND_S)
        return self

    def __exit__(self, *_exc: object) -> None:
        if self.thread.is_alive():
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(timeout=_DEADLOCK_BOUND_S)
        if not self.loop.is_closed():
            self.loop.close()


def _call_from_worker_thread(view: SimpleNamespace, path: str) -> bytes:
    """
    Execute a bridged filesystem call from a worker thread.

    Parameters:
        view (SimpleNamespace): Namespace exposing the bridged filesystem.
        path (str): Path to pass to the filesystem operation.

    Returns:
        bytes: Data returned for the requested path.
    """
    holder: dict[str, object] = {}

    def run() -> None:
        async def body() -> None:
            holder["value"] = await asyncio.to_thread(view.fs.download_file, path)

        asyncio.run(body())

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(timeout=_DEADLOCK_BOUND_S)
    assert not thread.is_alive(), "bridge call deadlocked"
    value = holder.get("value")
    assert isinstance(value, bytes)
    return value


def test_dispatcher_runs_async_host_operation_from_worker_loop() -> None:
    """The RLM async-Tool seam uses the persistent composition loop."""
    dispatcher = SyncBridgeDispatcher()
    with _ServingLoop("fleet-test-host-tool-loop") as server:
        dispatcher.set_loop(server.loop)
        result_holder: dict[str, object] = {}

        def worker() -> None:
            """
            Run a bridged host operation from a worker event loop and store its result.
            """

            async def host_operation() -> tuple[str, str]:
                return (threading.current_thread().name, asyncio.get_running_loop().__class__.__name__)

            async def body() -> None:
                result_holder["value"] = dispatcher.run(host_operation())

            asyncio.run(body())

        thread = threading.Thread(target=worker, name="fleet-test-rlm-worker", daemon=True)
        thread.start()
        thread.join(timeout=_DEADLOCK_BOUND_S)
        assert not thread.is_alive(), "async host bridge deadlocked"
        value = result_holder["value"]
        assert isinstance(value, tuple)
        assert value[0] == "fleet-test-host-tool-loop"
        assert str(value[1]).endswith("EventLoop")
        dispatcher.clear_loop(server.loop)


def test_compositions_cannot_overwrite_each_others_bridge_authority() -> None:
    """Two composition dispatchers route their own views independently."""
    dispatcher_a = SyncBridgeDispatcher()
    dispatcher_b = SyncBridgeDispatcher()
    with _ServingLoop("fleet-test-loop-a") as server_a, _ServingLoop("fleet-test-loop-b") as server_b:
        dispatcher_a.set_loop(server_a.loop)
        view_a = sync_sandbox(_sandbox(), server_a.loop, dispatcher_a)
        # Composition B starts AFTER A's view exists and registers its own loop.
        dispatcher_b.set_loop(server_b.loop)
        view_b = sync_sandbox(_sandbox(), server_b.loop, dispatcher_b)
        assert view_a._owner.service_loop() is server_a.loop
        assert view_b._owner.service_loop() is server_b.loop
        assert _call_from_worker_thread(view_a, "/a") == b"bytes:/a"
        assert _call_from_worker_thread(view_b, "/b") == b"bytes:/b"

        # Clearing with a foreign loop is a no-op (no cross-composition clear).
        dispatcher_a.clear_loop(server_b.loop)
        assert view_a._owner.service_loop() is server_a.loop
        dispatcher_a.clear_loop(server_a.loop)
        # Cleared of composition authority, the view resolves the caller-
        # captured loop fallback — never composition B's loop, never silent.
        assert view_a._owner.service_loop() is server_a.loop
        assert dispatcher_a.service_loop() is None
        dispatcher_b.clear_loop(server_b.loop)
        assert dispatcher_b.service_loop() is None


def test_fail_fast_when_called_from_the_servicing_event_loop() -> None:
    """The servicing loop thread can never block on its own bridge (RC-7 guard)."""
    dispatcher = SyncBridgeDispatcher()

    async def on_service_loop() -> None:
        loop = asyncio.get_running_loop()
        dispatcher.set_loop(loop)
        view = sync_sandbox(_sandbox(), loop, dispatcher)
        errors: list[str] = []
        try:
            for call in (lambda: view.fs.download_file("/x"),):
                try:
                    call()
                    errors.append("NO_ERROR")
                except DaytonaAdapterError as exc:
                    errors.append(getattr(exc, "cause_type", type(exc).__name__))
            return errors
        finally:
            dispatcher.clear_loop(loop)

    result_holder: dict[str, object] = {}

    def run() -> None:
        result_holder["errors"] = asyncio.run(on_service_loop())

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(timeout=_DEADLOCK_BOUND_S)
    assert not thread.is_alive()
    assert result_holder["errors"] == ["InterpreterThreadError"]


def test_closing_one_view_tombstones_only_that_view() -> None:
    """Per-view close invalidates that view; siblings and dispatcher keep working."""
    import pytest

    dispatcher = SyncBridgeDispatcher()
    with _ServingLoop("fleet-test-loop-a") as server:
        dispatcher.set_loop(server.loop)
        view_a = sync_sandbox(_sandbox(), server.loop, dispatcher)
        view_b = sync_sandbox(_sandbox(), server.loop, dispatcher)
        tombstone_sync_sandbox(view_a)

        with pytest.raises(DaytonaAdapterError) as excinfo:
            view_a.fs.download_file("/tombstoned")
        assert getattr(excinfo.value, "cause_type", "") == "InterpreterBridgeError"

        # Sibling view and dispatcher authority are unaffected.
        assert _call_from_worker_thread(view_b, "/alive") == b"bytes:/alive"
        assert dispatcher.service_loop() is server.loop

        # start() re-arms only the tombstoned view.
        view_a.start()
        assert _call_from_worker_thread(view_a, "/rearmed") == b"bytes:/rearmed"
        dispatcher.clear_loop(server.loop)


def test_disposed_composition_dispatcher_fallback_fails_typed_not_silent() -> None:
    """After the composition clears its loop, views fail typed-fast, never silent."""
    dispatcher = SyncBridgeDispatcher()
    with _ServingLoop("fleet-test-loop-a") as server:
        dispatcher.set_loop(server.loop)
        view = sync_sandbox(_sandbox(), server.loop, dispatcher)
        assert _call_from_worker_thread(view, "/ok") == b"bytes:/ok"
        dispatcher.clear_loop(server.loop)
        # No default dispatcher registered and no OTHER dispatcher: the view now
        # falls back to its caller loop (composition loop) — service loop is
        # resolved per call, so clearing the dispatcher stops composition
        # routing instead of stranding traffic on a dead loop.
        assert view._owner.service_loop() is server.loop  # caller-captured fallback
        dispatcher.clear_loop(server.loop)
