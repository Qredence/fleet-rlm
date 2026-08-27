"""RC-7 regression contracts: sync Daytona bridges use the composition service loop.

RC-7: bridges that captured a caller loop deadlocked when that loop's thread
nested-sync-waited on work fulfilled through the same bridge (DSPy
``_execute_code`` runs synchronously on the worker ``asyncio.run(...)`` loop
thread while a broker ``_fulfill`` worker blocks on the posted coroutine).
The rework routes every bridge's SDK coroutines to the composition-wide
service loop registered by ``install_daytona_composition`` — the loop all
loop-affine Daytona SDK objects were created on, and the one loop that by
construction never performs nested synchronous waits. When no service loop is
registered (private-test compositions), bridges fall back to capturing the
caller-provided loop, matching legacy behavior.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from fleet_rlm.daytona import recursive_child_runtime
from fleet_rlm.daytona.dspy_sync_bridge import (
    SyncBridgeDispatcher,
    sync_sandbox,
)
from fleet_rlm.daytona.errors import DaytonaAdapterError
from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, sandbox_backend
from fleet_rlm.daytona.session_manager import DaytonaAdmission, DaytonaAdmissionPermit
from fleet_rlm.rlm.recursion import ChildRuntimeCleanupError

_DEADLOCK_BOUND_S = 5.0
_POLL_BOUND_S = 0.5


class _AsyncFs:
    """Async FS double recording which loop/thread services each call."""

    def __init__(self) -> None:
        self.loops: list[asyncio.AbstractEventLoop] = []
        self.thread_names: list[str] = []

    async def download_file(self, path: str) -> bytes:
        self.loops.append(asyncio.get_running_loop())
        self.thread_names.append(threading.current_thread().name)
        return f"bytes:{path}".encode()


class _AsyncProcess:
    async def code_run(self, code: str) -> SimpleNamespace:
        return SimpleNamespace(exit_code=0, result=f"ran:{code}")


def _sandbox(fs: _AsyncFs | None = None) -> SimpleNamespace:
    return SimpleNamespace(fs=fs or _AsyncFs(), process=_AsyncProcess())


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

    def stop(self, *, close: bool = True, join: bool = True) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        if join:
            self.thread.join(timeout=_DEADLOCK_BOUND_S)
        if close and not self.loop.is_closed():
            self.loop.close()

    def __exit__(self, *_exc: object) -> None:
        if self.thread.is_alive():
            self.stop()
        elif not self.loop.is_closed():
            self.loop.close()


@contextlib.contextmanager
def _registered_service_loop(name: str = "fleet-test-service-loop"):
    dispatcher = SyncBridgeDispatcher()
    with _ServingLoop(name) as server:
        dispatcher.set_loop(server.loop)
        try:
            yield server, dispatcher
        finally:
            dispatcher.clear_loop(server.loop)


def _run_worker(body) -> threading.Thread:
    return threading.Thread(target=lambda: asyncio.run(body()), daemon=True)


def test_no_service_loop_falls_back_to_caller_captured_loop() -> None:
    """Undispatched bridges capture the caller loop, matching legacy behavior."""
    fs = _AsyncFs()
    result_holder: dict[str, object] = {}
    holder: dict[str, object] = {}
    done = threading.Event()

    async def worker() -> None:
        holder["loop"] = asyncio.get_running_loop()
        bridge = sync_sandbox(_sandbox(fs), asyncio.get_running_loop())
        result_holder["value"] = await asyncio.to_thread(bridge.fs.download_file, "/probe")
        done.set()

    worker_thread = _run_worker(worker)
    started = time.perf_counter()
    worker_thread.start()
    worker_thread.join(timeout=_DEADLOCK_BOUND_S)
    elapsed = time.perf_counter() - started

    assert done.is_set()
    assert result_holder["value"] == b"bytes:/probe"
    assert fs.loops and all(loop is holder["loop"] for loop in fs.loops)
    assert fs.thread_names and all(name == worker_thread.name for name in fs.thread_names)
    assert elapsed < _DEADLOCK_BOUND_S


def test_registered_service_loop_services_every_bridge() -> None:
    """Dispatched bridges default to the registered service loop regardless of capture loop."""
    fs = _AsyncFs()
    with _registered_service_loop() as (server, dispatcher):
        assert dispatcher.service_loop() is server.loop
        worker_loop_owner = _ServingLoop("fleet-test-worker-loop")
        with worker_loop_owner:
            bridge_a = sync_sandbox(_sandbox(fs), worker_loop_owner.loop, dispatcher)
            bridge_b = sync_sandbox(_sandbox(fs), server.loop, dispatcher)
            assert bridge_a.fs.download_file("/a") == b"bytes:/a"
            assert bridge_b.process.code_run("print(1)").result == "ran:print(1)"
    assert fs.loops and all(loop is server.loop for loop in fs.loops)
    assert all(name == server.thread.name for name in fs.thread_names)
    assert dispatcher.service_loop() is None


def test_sync_bridge_completes_inside_nested_deadlock_shape() -> None:
    """Exact RC-7 shape against the service loop: worker parks, fulfill answers.

    The worker thread's ``asyncio.run`` loop is parked in a synchronous
    ``Future.result()``-style wait (mirroring DSPy ``_execute_code`` → broker
    ``_poll_once``) whose completion requires a sync bridge call. The bridge
    completes because the registered service loop is never the parked loop.
    """
    fs = _AsyncFs()
    outcome: dict[str, object] = {}
    with _registered_service_loop() as (server, dispatcher):

        async def worker() -> None:
            # Declared against THIS (later-parked) loop, like
            # _build_interpreter(loop=asyncio.get_running_loop()).
            bridge = sync_sandbox(_sandbox(fs), asyncio.get_running_loop(), dispatcher)

            def fulfill(path: str) -> bytes:
                return bridge.fs.download_file(path)

            # Synchronous nested wait on the loop thread, mirroring
            # _poll_once's ThreadPoolExecutor.map(Future.result()) chain.
            with ThreadPoolExecutor(max_workers=1) as pool:
                results = list(pool.map(fulfill, ["/a"]))
            outcome["value"] = results[0]

        worker_thread = _run_worker(worker)
        started = time.perf_counter()
        worker_thread.start()
        worker_thread.join(timeout=_DEADLOCK_BOUND_S)
        elapsed = time.perf_counter() - started

        assert not worker_thread.is_alive(), "nested sync wait deadlocked (RC-7 shape)"
        assert outcome["value"] == b"bytes:/a"
        assert fs.loops and all(loop is server.loop for loop in fs.loops)
        assert elapsed < _DEADLOCK_BOUND_S


def test_legacy_caller_loop_shape_deadlocks() -> None:
    """Pre-fix reproduction: posting to the caller-captured loop hangs.

    Documents why service-loop routing is required: the legacy bridge shape
    parks the worker loop thread AND waits on a coroutine that only that loop
    can run. The waiter is released by cancellation so the harness exits.
    """
    fs = _AsyncFs()
    loop_ready = threading.Event()
    holder: dict[str, asyncio.AbstractEventLoop] = {}
    posted: list[Future[bytes]] = []
    completion = threading.Event()

    def legacy_sync_download(path: str) -> bytes:
        # Pre-fix _sync_await: post to the caller-captured loop and block.
        future: Future[bytes] = asyncio.run_coroutine_threadsafe(fs.download_file(path), holder["loop"])
        posted.append(future)
        return future.result()

    async def worker() -> object:
        holder["loop"] = asyncio.get_running_loop()
        loop_ready.set()
        with ThreadPoolExecutor(max_workers=1) as pool:
            results = list(pool.map(legacy_sync_download, ["/a"]))
            return results[0]

    def run_worker() -> None:
        with contextlib.suppress(BaseException):
            asyncio.run(worker())
        completion.set()

    thread = threading.Thread(target=run_worker, daemon=True)
    thread.start()
    assert loop_ready.wait(timeout=2.0)
    thread.join(timeout=2.0)
    assert not completion.is_set(), "legacy caller-captured loop shape should deadlock (pre-fix shape)"
    for future in posted:
        future.cancel()
    thread.join(timeout=_DEADLOCK_BOUND_S)
    assert completion.is_set(), "cancellation must release the legacy-shape waiter"


def test_sequential_and_concurrent_calls_share_one_service_loop() -> None:
    """Sequential reuse and concurrent first use all hit the one service loop."""
    fs = _AsyncFs()
    with _registered_service_loop() as (server, dispatcher):
        bridge = sync_sandbox(_sandbox(fs), asyncio.new_event_loop(), dispatcher)
        results: list[bytes] = []
        results_lock = threading.Lock()

        def call(path: str) -> None:
            with results_lock:
                results.append(bridge.fs.download_file(path))

        call("/first")
        threads = [threading.Thread(target=call, args=(f"/c{i}",), daemon=True) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=_DEADLOCK_BOUND_S)
            assert not t.is_alive()
        call("/last")

        assert len(results) == 10
        assert set(fs.loops) == {server.loop}


def test_close_fails_calls_typed_fast_and_start_recovers() -> None:
    """close() tombstones the bridge (typed-fast); start() reopens it."""
    with _registered_service_loop() as (_server, dispatcher):
        bridge = sync_sandbox(_sandbox(), asyncio.new_event_loop(), dispatcher)
        assert bridge.fs.download_file("/warm") == b"bytes:/warm"
        bridge.close()
        started = time.perf_counter()
        with pytest.raises(DaytonaAdapterError) as exc_info:
            bridge.fs.download_file("/x")
        assert time.perf_counter() - started < _DEADLOCK_BOUND_S
        assert exc_info.value.cause_type == "InterpreterBridgeError"
        # close() is idempotent and survives close/reopen.
        bridge.close()
        bridge.start()
        assert bridge.fs.download_file("/ok") == b"bytes:/ok"
        bridge.close()


def test_stopped_service_loop_fails_typed_fast_and_reregistration_recovers() -> None:
    """A dead service loop raises typed-fast; re-registering restores service."""
    dispatcher = SyncBridgeDispatcher()
    server = _ServingLoop("fleet-test-dead-loop")
    with server:
        dispatcher.set_loop(server.loop)
        bridge = sync_sandbox(_sandbox(), asyncio.new_event_loop(), dispatcher)
        assert bridge.fs.download_file("/warm") == b"bytes:/warm"
        # Stop serving WITHOUT closing the loop: posts queue but never run.
        server.stop(close=False)
        started = time.perf_counter()
        with pytest.raises(DaytonaAdapterError) as exc_info:
            bridge.fs.download_file("/x")
        # Heartbeat-sliced wait bounds the detection.
        assert time.perf_counter() - started < _DEADLOCK_BOUND_S + 2 * _POLL_BOUND_S
        assert exc_info.value.cause_type == "InterpreterBridgeError"
        server.loop.close()
    dispatcher.clear_loop(server.loop)

    with _registered_service_loop() as (_server2, dispatcher2):
        bridge2 = sync_sandbox(_sandbox(), asyncio.new_event_loop(), dispatcher2)
        assert bridge2.fs.download_file("/recover") == b"bytes:/recover"


def test_call_from_service_loop_fails_fast() -> None:
    """A sync bridge call issued from the service loop itself fails fast."""
    with _registered_service_loop() as (server, dispatcher):
        bridge = sync_sandbox(_sandbox(), asyncio.new_event_loop(), dispatcher)

        async def host_side() -> object:
            return bridge.fs.download_file("/x")

        future = asyncio.run_coroutine_threadsafe(host_side(), server.loop)
        with pytest.raises(DaytonaAdapterError) as exc_info:
            future.result(timeout=_DEADLOCK_BOUND_S)
        assert exc_info.value.cause_type == "InterpreterThreadError"


def test_interpreter_shutdown_tombstones_bridge_calls_only() -> None:
    """shutdown() tombstones its bridge while the shared service loop survives."""
    guard_loop = asyncio.new_event_loop()
    try:
        with _registered_service_loop() as (server, dispatcher):
            backend = sandbox_backend(_sandbox(), loop=guard_loop, dispatcher=dispatcher)
            other = sync_sandbox(_sandbox(), guard_loop, dispatcher)
            interpreter = DaytonaCodeInterpreter(backend=backend)
            interpreter.start()
            assert backend.sandbox.fs.download_file("/warm") == b"bytes:/warm"

            interpreter.shutdown()
            with pytest.raises(DaytonaAdapterError) as exc_info:
                backend.sandbox.fs.download_file("/x")
            assert exc_info.value.cause_type == "InterpreterBridgeError"
            # shutdown is idempotent; other bridges keep using the service loop.
            interpreter.shutdown()
            assert other.fs.download_file("/ok") == b"bytes:/ok"
            assert fs_loop_running(server)
    finally:
        guard_loop.close()


def fs_loop_running(server: _ServingLoop) -> bool:
    return server.loop.is_running() and server.thread.is_alive()


def test_child_runtime_cleanup_result_is_bounded() -> None:
    """Sibling site: child cleanup posting to a stalled loop fails typed, not silent."""
    loop = asyncio.new_event_loop()
    try:
        shutdown_calls: list[bool] = []

        class _Interpreter:
            def shutdown(self, *, strict_broker_cleanup: bool = False) -> None:
                shutdown_calls.append(strict_broker_cleanup)

        class _Platform:
            async def delete(self, _sandbox_id: str) -> None:
                return None

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(recursive_child_runtime, "_CHILD_CLEANUP_RESULT_TIMEOUT_S", 0.2)
            started = time.perf_counter()
            with pytest.raises(ChildRuntimeCleanupError):
                recursive_child_runtime._close_child_runtime_sync(
                    loop=loop,
                    platform=_Platform(),
                    sandbox=SimpleNamespace(fs=None),
                    sandbox_id="sb-child",
                    mount_path="/home/daytona/fleet",
                    interpreter=_Interpreter(),
                    permit=DaytonaAdmissionPermit(DaytonaAdmission(max_active_leases=1)._semaphore),
                )
            elapsed = time.perf_counter() - started
        assert shutdown_calls == [True]
        assert elapsed < _DEADLOCK_BOUND_S
    finally:
        loop.close()
