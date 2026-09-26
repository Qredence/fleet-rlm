"""Phase 2 preservation seam: runtime-owned Daytona behavior matrix.

Focused ownership-transfer coverage for the DaytonaRuntime cutover:

* sequential root reuse returns the same lease without a second create;
* two concurrent sessions acquire independently (no shared registry lock
  across provider waits);
* failed root creation keeps provider-owned late acquisitions visible until
  the SessionManager reports that ownership settled;
* cancellation during execution cannot mutate the settled registry entry;
* child cleanup failure keeps the child retained for retry;
* restart reconciliation (taint) rotates the generation on next acquire;
* broker transport limits are preserved through the cutover.

These tests pin behavior, not implementation class names beyond the
runtime-owned ``DaytonaSessionRecord`` seam.
"""

from __future__ import annotations

import asyncio
import gc
from types import SimpleNamespace
from uuid import uuid4

import pytest

from fleet_rlm.daytona.broker import _MAX_REQUEST_BYTES
from fleet_rlm.daytona.runtime import (
    ChildEnvironmentSpec,
    DaytonaRuntime,
    DaytonaSessionRecord,
    RootSessionSpec,
    SessionCleanupState,
)


def _closable(*, sandbox_id: str = "sandbox-1") -> object:
    class Lease:
        closed = False

        async def release(self) -> None:
            self.closed = True

    lease = Lease()
    lease.sandbox_id = sandbox_id  # type: ignore[attr-defined]
    return lease


@pytest.mark.asyncio
async def test_sequential_root_reuse_returns_same_lease() -> None:
    creates = 0

    async def acquire(_spec: RootSessionSpec, **_kwargs: object) -> object:
        nonlocal creates
        creates += 1
        return _closable()

    runtime = DaytonaRuntime(root_acquirer=acquire, root_releaser=lambda lease: lease.release())
    spec = RootSessionSpec(workspace_id=uuid4(), session_id=uuid4())

    first = await runtime.acquire_root_session(spec)
    second = await runtime.acquire_root_session(spec)

    assert second is first
    assert creates == 1
    record = runtime.session_record(spec.workspace_id, spec.session_id)
    assert isinstance(record, DaytonaSessionRecord)
    assert record.cleanup_state is SessionCleanupState.ACTIVE
    assert await runtime.aclose() is True


@pytest.mark.asyncio
async def test_retiring_root_releases_record_and_idle_key_lock() -> None:
    runtime = DaytonaRuntime(
        root_acquirer=lambda _spec, **_kwargs: _closable(),
        root_releaser=lambda lease: lease.release(),
    )
    spec = RootSessionSpec(workspace_id=uuid4(), session_id=uuid4())

    await runtime.acquire_root_session(spec)
    assert runtime.session_record(spec.workspace_id, spec.session_id) is not None

    await runtime.close_root_session(spec.workspace_id, spec.session_id)
    gc.collect()

    assert runtime.roots == ()
    assert runtime.session_record(spec.workspace_id, spec.session_id) is None
    assert len(runtime._key_locks) == 0
    assert not runtime.has_pending_ownership


@pytest.mark.asyncio
async def test_two_concurrent_sessions_acquire_independently() -> None:
    started = asyncio.Event()
    release_second = asyncio.Event()
    calls: list[str] = []

    async def acquire(spec: RootSessionSpec, **_kwargs: object) -> object:
        calls.append(str(spec.session_id))
        if len(calls) == 1:
            started.set()
            await release_second.wait()
        return _closable(sandbox_id=f"sandbox-{len(calls)}")

    runtime = DaytonaRuntime(root_acquirer=acquire, root_releaser=lambda lease: lease.release())
    first_spec = RootSessionSpec(workspace_id=uuid4(), session_id=uuid4())
    second_spec = RootSessionSpec(workspace_id=uuid4(), session_id=uuid4())

    first_task = asyncio.create_task(runtime.acquire_root_session(first_spec))
    await started.wait()
    # The second session's registry access must not wait behind the first
    # session's in-flight provider call.
    second = await asyncio.wait_for(runtime.acquire_root_session(second_spec), timeout=5.0)
    release_second.set()
    first = await asyncio.wait_for(first_task, timeout=5.0)

    assert first is not second
    assert len(runtime.roots) == 2
    assert await runtime.aclose() is True


@pytest.mark.asyncio
async def test_same_session_acquisitions_share_a_lock_until_settled() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    creates = 0

    async def acquire(_spec: RootSessionSpec, **_kwargs: object) -> object:
        nonlocal creates
        creates += 1
        started.set()
        await release.wait()
        return _closable()

    runtime = DaytonaRuntime(root_acquirer=acquire, root_releaser=lambda lease: lease.release())
    spec = RootSessionSpec(workspace_id=uuid4(), session_id=uuid4())
    first_task = asyncio.create_task(runtime.acquire_root_session(spec))
    await started.wait()

    second_started = asyncio.Event()

    async def second_acquire():
        second_started.set()
        return await runtime.acquire_root_session(spec)

    second_task = asyncio.create_task(second_acquire())
    await second_started.wait()
    await asyncio.sleep(0)
    assert creates == 1

    release.set()
    first, second = await asyncio.gather(first_task, second_task)

    assert first is second
    assert creates == 1
    await runtime.close_root_session(spec.workspace_id, spec.session_id)
    gc.collect()
    assert len(runtime._key_locks) == 0


@pytest.mark.asyncio
async def test_failed_root_creation_checks_session_manager_ownership() -> None:
    class Manager:
        has_pending_ownership = False
        close_calls = 0

        async def aclose(self, *, drain_seconds: float) -> bool:
            assert drain_seconds >= 0
            self.close_calls += 1
            self.has_pending_ownership = False
            return True

    manager = Manager()

    async def acquire(_spec: RootSessionSpec, **_kwargs: object) -> object:
        manager.has_pending_ownership = True
        await asyncio.sleep(10.0)
        return _closable()  # pragma: no cover

    runtime = DaytonaRuntime(
        SimpleNamespace(session_manager=manager),
        root_acquirer=acquire,
        root_releaser=lambda lease: lease.release(),
    )
    spec = RootSessionSpec(
        workspace_id=uuid4(),
        session_id=uuid4(),
        deadline=asyncio.get_running_loop().time() + 0.05,
    )

    with pytest.raises((TimeoutError, asyncio.TimeoutError)):
        await runtime.acquire_root_session(spec)

    # Runtime ownership mirrors the provider manager's real settlement state.
    assert runtime.has_pending_ownership
    assert await runtime.aclose(deadline=asyncio.get_running_loop().time() + 5.0) is True
    assert not runtime.has_pending_ownership
    assert manager.close_calls == 1


@pytest.mark.asyncio
async def test_cancelled_acquisition_does_not_publish_a_root() -> None:
    started = asyncio.Event()

    async def acquire(_spec: RootSessionSpec, **_kwargs: object) -> object:
        started.set()
        await asyncio.sleep(10.0)
        return _closable()  # pragma: no cover

    runtime = DaytonaRuntime(root_acquirer=acquire, root_releaser=lambda lease: lease.release())
    spec = RootSessionSpec(workspace_id=uuid4(), session_id=uuid4())

    task = asyncio.create_task(runtime.acquire_root_session(spec))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert runtime.roots == ()
    assert runtime.session_record(spec.workspace_id, spec.session_id) is None


@pytest.mark.asyncio
async def test_child_cleanup_failure_stays_retained_for_retry() -> None:
    attempts = 0

    class Lease:
        async def close(self) -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("provider delete failed")

    runtime = DaytonaRuntime(child_acquirer=lambda _spec: Lease())
    with pytest.raises(RuntimeError, match="provider delete failed"):
        async with runtime.open_child(ChildEnvironmentSpec()):
            assert len(runtime.children) == 1

    # First close failed: the child stays owned so a later close can retry.
    assert len(runtime.children) == 1
    assert await runtime.aclose() is True
    assert runtime.children == ()
    assert attempts == 2


@pytest.mark.asyncio
async def test_tainted_root_rotates_on_next_acquisition() -> None:
    leases = [_closable(sandbox_id="sandbox-old"), _closable(sandbox_id="sandbox-new")]
    calls = 0

    async def acquire(_spec: RootSessionSpec, **_kwargs: object) -> object:
        nonlocal calls
        lease = leases[calls]
        calls += 1
        return lease

    runtime = DaytonaRuntime(root_acquirer=acquire, root_releaser=lambda lease: lease.release())
    spec = RootSessionSpec(workspace_id=uuid4(), session_id=uuid4())

    first = await runtime.acquire_root_session(spec)
    assert first.sandbox_id == "sandbox-old"
    # Restart reconciliation fences the resident root; the next acquisition
    # must replace it rather than reuse it.
    runtime.mark_root_tainted(spec.workspace_id, spec.session_id)
    second = await runtime.acquire_root_session(spec)

    assert second is not first
    assert second.sandbox_id == "sandbox-new"
    assert calls == 2
    assert await runtime.aclose() is True


def test_broker_transport_limits_preserved() -> None:
    assert _MAX_REQUEST_BYTES == 2 * 1024 * 1024
