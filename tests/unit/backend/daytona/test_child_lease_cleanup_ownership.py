"""P39 child lease/cleanup ownership contract lanes.

Behavior-only evidence for the contracted single child-runtime owner
(P36 rows P39-REC-002..006, absorbed behind one owner module):

- VAL-REC-027: strict interpreter/broker shutdown; broker failure fails the
  close while purge/delete/absence/permit restoration are still attempted.
- VAL-REC-029: admission restoration follows cleanup settlement on every
  path, without leaks or over-release.
- VAL-REC-030: cleanup failure is recorded as fatal and re-observed without
  rerunning cleanup.
- VAL-REC-031: close is single-owner, joinable, deadline-bounded, and
  re-observable under a two-thread barrier race.
- VAL-REC-032: cleanup survives owner-loop loss and dispatch failure through
  the bounded fallback executor/disposable loop.
"""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import Future
from dataclasses import dataclass, field
from types import SimpleNamespace
from uuid import uuid4

import pytest

from fleet_rlm.daytona import recursive_child_runtime
from fleet_rlm.daytona.recursive_child_runtime import (
    ChildRuntimeLease,
    ChildRuntimeLeaseState,
    LateCleanupOwner,
    build_child_runtime_factory,
)
from fleet_rlm.daytona.session_manager import DaytonaAdmission
from fleet_rlm.rlm.recursion import ChildRuntimeCleanupError

_MOUNT = "/home/daytona/fleet"


@dataclass
class _Fs:
    files: set[str]
    deleted: list[str] = field(default_factory=list)

    async def list_files(self, _root: str, *, depth: int | None) -> list[SimpleNamespace]:
        assert depth is None
        return [SimpleNamespace(path=path, is_dir=False) for path in sorted(self.files)]

    async def delete_file(self, path: str, *, recursive: bool = False) -> None:
        del recursive
        self.files.discard(path)
        self.deleted.append(path)


@dataclass
class _Sandbox:
    id: str
    fs: _Fs


class _RecordingPlatform:
    """Provider double recording every lifecycle interaction in order."""

    def __init__(self, child: _Sandbox) -> None:
        self.child = child
        self.steps: list[str] = []
        self.create_calls: list[dict[str, object]] = []
        self.deleted: list[str] = []
        self.probes: list[str] = []
        self.delete_error: BaseException | None = None

    async def create(self, **kwargs: object) -> _Sandbox:
        self.create_calls.append(kwargs)
        self.steps.append("create")
        return self.child

    async def delete(self, sandbox_id: str) -> None:
        self.steps.append(f"delete:{sandbox_id}")
        self.deleted.append(sandbox_id)
        if self.delete_error is not None:
            raise self.delete_error

    async def get(self, sandbox_id: str) -> None:
        self.steps.append(f"probe:{sandbox_id}")
        self.probes.append(sandbox_id)
        return None


class _RecordingInterpreter:
    """Interpreter double recording strict shutdown requests."""

    def __init__(self, steps: list[str], *, error: BaseException | None = None) -> None:
        self._steps = steps
        self._error = error
        self.shutdown_calls = 0

    def shutdown(self, *, strict_broker_cleanup: bool = False) -> None:
        assert strict_broker_cleanup is True
        self.shutdown_calls += 1
        self._steps.append("interpreter_shutdown")
        if self._error is not None:
            raise self._error


def _factory(
    *,
    platform: _RecordingPlatform,
    admission: DaytonaAdmission,
    interpreter_error: BaseException | None = None,
    deadline: float | None = None,
    monkeypatch: pytest.MonkeyPatch | None = None,
) -> tuple[object, _RecordingInterpreter]:
    loop = asyncio.get_running_loop()
    interpreter = _RecordingInterpreter(platform.steps, error=interpreter_error)

    def interpreter_factory(**_kwargs: object) -> _RecordingInterpreter:
        return interpreter

    if monkeypatch is not None:
        monkeypatch.setattr(recursive_child_runtime, "DaytonaCodeInterpreter", interpreter_factory)
        monkeypatch.setattr(recursive_child_runtime, "sandbox_backend", lambda sandbox, **_kwargs: sandbox)
    factory = build_child_runtime_factory(
        loop=loop,
        platform=platform,
        admission=admission,
        volume_id="shared-volume",
        mount_path=_MOUNT,
        workspace_id=uuid4(),
        run_id=uuid4(),
        deadline=deadline if deadline is not None else loop.time() + 30,
        execution_timeout_s=30,
        execution_output_cap=1000,
    )
    return factory, interpreter


async def _full_capacity_restored(admission: DaytonaAdmission, *, capacity: int) -> bool:
    permits = []
    try:
        for _ in range(capacity):
            permits.append(await admission.acquire(deadline=asyncio.get_running_loop().time() + 1))
    except RuntimeError:
        return False
    finally:
        for permit in permits:
            permit.release()
    return len(permits) == capacity


@pytest.mark.asyncio
async def test_val_rec_027_broker_shutdown_failure_fails_close_but_remaining_steps_still_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """VAL-REC-027: a failed strict broker cleanup fails the close as a typed
    cleanup failure while scope purge, provider deletion, absence confirmation,
    and permit restoration are still attempted."""
    child = _Sandbox("child-sandbox", _Fs({f"{_MOUNT}/child.txt"}))
    platform = _RecordingPlatform(child)
    admission = DaytonaAdmission(max_active_leases=2)
    factory, interpreter = _factory(
        platform=platform,
        admission=admission,
        interpreter_error=RuntimeError("broker cleanup failed"),
        monkeypatch=monkeypatch,
    )

    lease = await asyncio.to_thread(factory, 1)
    with pytest.raises(ChildRuntimeCleanupError, match="recursive child cleanup failed"):
        await asyncio.to_thread(lease.close)

    assert lease.state is ChildRuntimeLeaseState.FAILED
    assert interpreter.shutdown_calls == 1
    # Every remaining step was still attempted, in order.
    assert platform.steps == [
        "create",
        "interpreter_shutdown",
        "delete:child-sandbox",
        "probe:child-sandbox",
    ]
    assert child.fs.files == set()
    assert platform.deleted == ["child-sandbox"]
    assert platform.probes == ["child-sandbox"]
    # The close failure is re-observed on every later close attempt without
    # rerunning cleanup.
    with pytest.raises(ChildRuntimeCleanupError, match="recursive child cleanup failed"):
        await asyncio.to_thread(lease.close)
    assert interpreter.shutdown_calls == 1
    assert platform.deleted == ["child-sandbox"]
    # Admission was restored despite the failure.
    assert await _full_capacity_restored(admission, capacity=2)


@pytest.mark.asyncio
async def test_val_rec_027_blocked_broker_shutdown_is_quarantined_and_still_settles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """VAL-REC-027: a blocking shutdown fails the close within its bound while
    quarantine ownership retains the cleanup until deletion and admission
    restoration settle."""
    child = _Sandbox("child-sandbox", _Fs({f"{_MOUNT}/child.txt"}))
    platform = _RecordingPlatform(child)
    admission = DaytonaAdmission(max_active_leases=1)
    release_shutdown = threading.Event()
    monkeypatch.setattr(recursive_child_runtime, "_CHILD_CLEANUP_RESULT_TIMEOUT_S", 0.05)

    class _BlockingInterpreter:
        def __init__(self, **_kwargs: object) -> None:
            return None

        def shutdown(self, *, strict_broker_cleanup: bool = False) -> None:
            assert strict_broker_cleanup is True
            platform.steps.append("interpreter_shutdown")
            release_shutdown.wait(2)

    monkeypatch.setattr(recursive_child_runtime, "DaytonaCodeInterpreter", _BlockingInterpreter)
    monkeypatch.setattr(recursive_child_runtime, "sandbox_backend", lambda sandbox, **_kwargs: sandbox)
    loop = asyncio.get_running_loop()
    factory = build_child_runtime_factory(
        loop=loop,
        platform=platform,
        admission=admission,
        volume_id="shared-volume",
        mount_path=_MOUNT,
        workspace_id=uuid4(),
        run_id=uuid4(),
        deadline=loop.time() + 30,
        execution_timeout_s=30,
        execution_output_cap=1000,
    )
    lease = await asyncio.to_thread(factory, 1)

    started = loop.time()
    with pytest.raises(ChildRuntimeCleanupError, match="recursive child cleanup failed"):
        await asyncio.to_thread(lease.close)
    assert loop.time() - started < 1.0
    # Quarantined: nothing is deleted while the shutdown is still blocked, and
    # ownership reports the retained pending cleanup immediately.
    assert platform.deleted == []
    with pytest.raises(ChildRuntimeCleanupError, match="still pending"):
        factory.raise_if_cleanup_failed()

    release_shutdown.set()
    await asyncio.to_thread(factory.wait_owned)
    assert platform.deleted == ["child-sandbox"]
    assert platform.probes == ["child-sandbox"]
    assert child.fs.files == set()
    assert await _full_capacity_restored(admission, capacity=1)


@pytest.mark.asyncio
async def test_val_rec_028_provider_delete_failure_still_confirms_absence_and_restores_admission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """VAL-REC-028: a delete request error still probes for confirmed absence
    and restores admission; acceptance alone is never cleanup proof."""
    child = _Sandbox("child-sandbox", _Fs(set()))
    platform = _RecordingPlatform(child)
    platform.delete_error = RuntimeError("provider 503")
    admission = DaytonaAdmission(max_active_leases=1)
    factory, interpreter = _factory(platform=platform, admission=admission, monkeypatch=monkeypatch)

    lease = await asyncio.to_thread(factory, 1)
    with pytest.raises(ChildRuntimeCleanupError, match="recursive child cleanup failed"):
        await asyncio.to_thread(lease.close)

    assert interpreter.shutdown_calls == 1
    assert platform.deleted == ["child-sandbox"]
    assert platform.probes == ["child-sandbox"]
    assert lease.state is ChildRuntimeLeaseState.FAILED
    assert await _full_capacity_restored(admission, capacity=1)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    ["success", "interpreter_failure", "admission_timeout", "authorization_revoked", "create_failure"],
)
async def test_val_rec_029_admission_restored_exactly_once_on_every_path(
    path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """VAL-REC-029: after the owned close join settles, the full configured
    capacity is reacquirable on every cleanup path (no leak, no over-release)."""
    capacity = 1 if path == "admission_timeout" else 2
    child = _Sandbox("child-sandbox", _Fs(set()))

    class _FailingCreatePlatform(_RecordingPlatform):
        async def create(self, **kwargs: object) -> _Sandbox:
            self.create_calls.append(kwargs)
            raise RuntimeError("provider create failed")

    platform: _RecordingPlatform = (
        _FailingCreatePlatform(child) if path == "create_failure" else _RecordingPlatform(child)
    )
    admission = DaytonaAdmission(max_active_leases=capacity)
    loop = asyncio.get_running_loop()
    is_authorized = (lambda: False) if path == "authorization_revoked" else None
    interpreter = _RecordingInterpreter(platform.steps)

    def interpreter_factory(**_kwargs: object) -> _RecordingInterpreter:
        return interpreter

    monkeypatch.setattr(recursive_child_runtime, "DaytonaCodeInterpreter", interpreter_factory)
    monkeypatch.setattr(recursive_child_runtime, "sandbox_backend", lambda sandbox, **_kwargs: sandbox)
    factory = build_child_runtime_factory(
        loop=loop,
        platform=platform,
        admission=admission,
        volume_id="shared-volume",
        mount_path=_MOUNT,
        workspace_id=uuid4(),
        run_id=uuid4(),
        deadline=loop.time() + (0.05 if path == "admission_timeout" else 30),
        execution_timeout_s=30,
        execution_output_cap=1000,
        is_authorized=is_authorized,
    )
    if path == "admission_timeout":
        held = await admission.acquire(deadline=loop.time() + 1)
        try:
            with pytest.raises(TimeoutError, match="acquisition deadline exceeded"):
                await asyncio.to_thread(factory, 1)
        finally:
            held.release()
        await asyncio.to_thread(factory.wait_owned)
    elif path == "success":
        lease = await asyncio.to_thread(factory, 1)
        await asyncio.to_thread(lease.close)
        assert interpreter.shutdown_calls == 1
    elif path == "interpreter_failure":
        interpreter._error = RuntimeError("broker cleanup failed")
        lease = await asyncio.to_thread(factory, 1)
        with pytest.raises(ChildRuntimeCleanupError):
            await asyncio.to_thread(lease.close)
    elif path == "authorization_revoked":
        with pytest.raises(RuntimeError, match="no longer authorized"):
            await asyncio.to_thread(factory, 1)
        assert platform.create_calls == []
    else:
        with pytest.raises(RuntimeError):
            await asyncio.to_thread(factory, 1)

    assert await _full_capacity_restored(admission, capacity=capacity)


@pytest.mark.asyncio
async def test_val_rec_029_concurrent_idempotent_close_never_over_releases_capacity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """VAL-REC-029: concurrent and repeated close run cleanup once and never
    raise capacity above the configured maximum."""
    child = _Sandbox("child-sandbox", _Fs({f"{_MOUNT}/child.txt"}))
    platform = _RecordingPlatform(child)
    admission = DaytonaAdmission(max_active_leases=1)
    factory, interpreter = _factory(platform=platform, admission=admission, monkeypatch=monkeypatch)
    lease = await asyncio.to_thread(factory, 1)

    barrier = threading.Barrier(3)
    errors: list[BaseException] = []

    def run_close() -> None:
        barrier.wait(2)
        try:
            lease.close()
        except BaseException as exc:  # pragma: no cover - reported by assertions
            errors.append(exc)

    # The close path posts provider cleanup to the running loop, so the racing
    # closers must run off the loop thread; blocking joins here would deadlock.
    await asyncio.gather(*(asyncio.to_thread(run_close) for _ in range(3)))

    assert errors == []
    assert interpreter.shutdown_calls == 1
    assert platform.deleted == ["child-sandbox"]
    lease.close()
    assert interpreter.shutdown_calls == 1
    assert platform.deleted == ["child-sandbox"]
    # A second full-capacity acquisition must fail: no over-release.
    permit = await admission.acquire(deadline=asyncio.get_running_loop().time() + 1)
    with pytest.raises(RuntimeError):
        await asyncio.wait_for(admission.acquire(deadline=asyncio.get_running_loop().time() + 0.05), timeout=1)
    permit.release()


def test_val_rec_030_failed_close_stores_and_re_surfaces_without_rerunning_cleanup() -> None:
    """VAL-REC-030: a failed close lands the lease in FAILED, re-surfaces the
    same stored failure on every later observation, and never reruns cleanup."""
    runs = 0
    original = ChildRuntimeCleanupError("recursive child cleanup failed")

    def close() -> None:
        nonlocal runs
        runs += 1
        raise original

    lease = ChildRuntimeLease(SimpleNamespace(), "sandbox", "volume", "subpath", close)
    for _ in range(3):
        with pytest.raises(ChildRuntimeCleanupError) as raised:
            lease.close()
        assert raised.value is original
    assert runs == 1
    assert lease.state is ChildRuntimeLeaseState.FAILED
    assert lease.close_error is original


@pytest.mark.asyncio
async def test_val_rec_030_cleanup_failure_after_valid_child_result_fails_factory_observation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """VAL-REC-030: a syntactically valid child answer cannot override an
    unresolved cleanup failure; the factory observation re-surfaces it."""
    child = _Sandbox("child-sandbox", _Fs(set()))
    platform = _RecordingPlatform(child)
    platform.delete_error = RuntimeError("provider delete failed")
    admission = DaytonaAdmission(max_active_leases=2)
    factory, interpreter = _factory(platform=platform, admission=admission, monkeypatch=monkeypatch)

    lease = await asyncio.to_thread(factory, 1)
    # The child "answered" successfully before close; cleanup still fails closed.
    with pytest.raises(ChildRuntimeCleanupError):
        await asyncio.to_thread(lease.close)
    assert interpreter.shutdown_calls == 1
    assert platform.deleted == ["child-sandbox"]
    # Re-observation surfaces the stored failure without rerunning cleanup.
    with pytest.raises(ChildRuntimeCleanupError, match="recursive child cleanup failed"):
        await asyncio.to_thread(lease.close)
    assert interpreter.shutdown_calls == 1
    assert platform.deleted == ["child-sandbox"]
    assert lease.state is ChildRuntimeLeaseState.FAILED


def test_val_rec_031_barrier_race_joins_one_cleanup_execution() -> None:
    """VAL-REC-031: two threads racing into close join one cleanup execution;
    the second caller blocks until settlement and observes the same result."""
    entered = threading.Event()
    release = threading.Event()
    calls = 0

    def close() -> None:
        nonlocal calls
        calls += 1
        entered.set()
        assert release.wait(2)

    lease = ChildRuntimeLease(SimpleNamespace(), "sandbox", "volume", "subpath", close)
    barrier = threading.Barrier(2)
    observed_states: list[ChildRuntimeLeaseState] = []

    def run_close() -> None:
        barrier.wait(2)
        lease.close()
        observed_states.append(lease.state)

    first = threading.Thread(target=run_close)
    second = threading.Thread(target=run_close)
    first.start()
    second.start()
    assert entered.wait(2)
    # While one close runs, the lease is observably CLOSING.
    assert lease.state is ChildRuntimeLeaseState.CLOSING
    assert second.is_alive()
    release.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert calls == 1
    assert observed_states == [ChildRuntimeLeaseState.CLOSED, ChildRuntimeLeaseState.CLOSED]
    lease.close()
    assert calls == 1


def test_val_rec_031_barrier_race_on_failure_re_surfaces_stored_error() -> None:
    """VAL-REC-031: a racing close failure stores one stable cleanup error that
    every later observer re-surfaces without a second cleanup execution."""
    original = RuntimeError("broker cleanup failed")
    calls = 0
    entered = threading.Event()
    release = threading.Event()

    def close() -> None:
        nonlocal calls
        calls += 1
        entered.set()
        assert release.wait(2)
        raise original

    lease = ChildRuntimeLease(SimpleNamespace(), "sandbox", "volume", "subpath", close)
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def run_close() -> None:
        barrier.wait(2)
        try:
            lease.close()
        except BaseException as exc:
            errors.append(exc)

    first = threading.Thread(target=run_close)
    second = threading.Thread(target=run_close)
    first.start()
    second.start()
    assert entered.wait(2)
    release.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert calls == 1
    assert errors == [original, original]
    assert lease.state is ChildRuntimeLeaseState.FAILED
    assert lease.close_error is original
    with pytest.raises(RuntimeError) as raised:
        lease.close()
    assert raised.value is original
    assert calls == 1


def test_val_rec_031_reentrant_close_from_closing_thread_fails_closed() -> None:
    """VAL-REC-031: the closing thread cannot recursively close the lease."""

    def close() -> None:
        lease.close()

    lease = ChildRuntimeLease(SimpleNamespace(), "sandbox", "volume", "subpath", close)
    with pytest.raises(RuntimeError, match="not reentrant"):
        lease.close()
    assert lease.state is not ChildRuntimeLeaseState.CLOSED


@pytest.mark.asyncio
async def test_val_rec_032_cleanup_survives_owner_loop_loss_with_full_provider_settlement() -> None:
    """VAL-REC-032: with the owner loop closed, cleanup uses the disposable-loop
    fallback and still completes strict shutdown, purge, deletion, confirmed
    absence, and permit restoration."""
    child = _Sandbox("child-sandbox", _Fs({f"{_MOUNT}/child.txt"}))
    platform = _RecordingPlatform(child)
    admission = DaytonaAdmission(max_active_leases=1)
    permit = await admission.acquire(deadline=asyncio.get_running_loop().time() + 1)
    interpreter = _RecordingInterpreter(platform.steps)

    class _ClosedLoop:
        def call_soon_threadsafe(self, *_args: object, **_kwargs: object) -> None:
            raise RuntimeError("Event loop is closed")

    recursive_child_runtime._close_child_runtime_sync(
        loop=_ClosedLoop(),  # type: ignore[arg-type]
        platform=platform,
        sandbox=child,
        sandbox_id="child-sandbox",
        mount_path=_MOUNT,
        interpreter=interpreter,  # type: ignore[arg-type]
        permit=permit,
    )

    assert interpreter.shutdown_calls == 1
    assert platform.deleted == ["child-sandbox"]
    assert platform.probes == ["child-sandbox"]
    assert child.fs.files == set()
    assert await _full_capacity_restored(admission, capacity=1)


@pytest.mark.asyncio
async def test_val_rec_032_dispatch_failure_surfaces_and_quarantine_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """VAL-REC-032: when the quarantine thread cannot start, the bounded
    fallback executor still performs the cleanup and the failure surfaces
    through ownership observation instead of being silently ignored."""
    child = _Sandbox("child-sandbox", _Fs({f"{_MOUNT}/child.txt"}))
    platform = _RecordingPlatform(child)
    release_shutdown = threading.Event()
    real_thread = threading.Thread
    thread_starts = 0

    class _FailingQuarantineThread:
        def __init__(self, *, target: object, **kwargs: object) -> None:
            nonlocal thread_starts
            thread_starts += 1
            self._thread = real_thread(target=target, **kwargs)  # type: ignore[arg-type]

        def start(self) -> None:
            if thread_starts == 2:
                raise RuntimeError("quarantine thread start failed")
            self._thread.start()

    class _BlockingInterpreter:
        def __init__(self, **_kwargs: object) -> None:
            return None

        def shutdown(self, *, strict_broker_cleanup: bool = False) -> None:
            assert strict_broker_cleanup is True
            platform.steps.append("interpreter_shutdown")
            release_shutdown.wait(2)

    monkeypatch.setattr(recursive_child_runtime, "Thread", _FailingQuarantineThread)
    monkeypatch.setattr(recursive_child_runtime, "DaytonaCodeInterpreter", _BlockingInterpreter)
    monkeypatch.setattr(recursive_child_runtime, "sandbox_backend", lambda sandbox, **_kwargs: sandbox)
    monkeypatch.setattr(recursive_child_runtime, "_CHILD_CLEANUP_RESULT_TIMEOUT_S", 0.05)
    admission = DaytonaAdmission(max_active_leases=1)
    loop = asyncio.get_running_loop()
    factory = build_child_runtime_factory(
        loop=loop,
        platform=platform,
        admission=admission,
        volume_id="shared-volume",
        mount_path=_MOUNT,
        workspace_id=uuid4(),
        run_id=uuid4(),
        deadline=loop.time() + 30,
        execution_timeout_s=30,
        execution_output_cap=1000,
    )
    lease = await asyncio.to_thread(factory, 1)

    with pytest.raises(ChildRuntimeCleanupError, match="recursive child cleanup failed"):
        await asyncio.to_thread(lease.close)

    release_shutdown.set()
    await asyncio.to_thread(factory.wait_owned)
    assert thread_starts == 2
    assert platform.deleted == ["child-sandbox"]
    assert child.fs.files == set()
    assert await _full_capacity_restored(admission, capacity=1)


def test_val_rec_032_late_cleanup_dispatch_failure_is_re_observable() -> None:
    """VAL-REC-032: when every dispatch lane fails, the dispatch failure itself
    is recorded and surfaces through the ownership join."""

    class _FailingThread:
        def __init__(self, **_kwargs: object) -> None:
            return None

        def start(self) -> None:
            raise RuntimeError("thread start failed")

    owner = LateCleanupOwner(wait_timeout_s=1.0)
    acquisition: Future[object] = Future()

    class _FailingExecutor:
        @staticmethod
        def submit(_fn: object) -> None:
            raise RuntimeError("fallback executor unavailable")

    import fleet_rlm.daytona.recursive_child_runtime as owner_module

    original_thread = owner_module.Thread
    original_executor = owner_module._FALLBACK_CLEANUP_EXECUTOR
    owner_module.Thread = _FailingThread  # type: ignore[misc,assignment]
    owner_module._FALLBACK_CLEANUP_EXECUTOR = _FailingExecutor()  # type: ignore[misc,assignment]
    try:
        owner.adopt_late_acquisition(acquisition, lambda _lease: None)
        acquisition.set_result(object())
    finally:
        owner_module.Thread = original_thread  # type: ignore[misc,assignment]
        owner_module._FALLBACK_CLEANUP_EXECUTOR = original_executor  # type: ignore[misc,assignment]

    with pytest.raises(ChildRuntimeCleanupError, match="recursive child cleanup failed"):
        owner.wait_owned()
