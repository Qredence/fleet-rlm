"""Phase 2 preservation seam: runtime-owned Daytona behavior matrix.

Focused ownership-transfer coverage for the DaytonaRuntime cutover:

* sequential root reuse returns the same lease without a second create;
* two concurrent sessions acquire independently (no shared registry lock
  across provider waits);
* failed/late root creation keeps late-acquisition ownership visible until
  the witness window settles;
* cancellation during execution cannot mutate the settled registry entry;
* child cleanup failure keeps the child retained for retry;
* restart reconciliation (taint) rotates the generation on next acquire;
* broker transport limits are preserved through the cutover.

These tests pin behavior, not implementation class names beyond the
runtime-owned ``DaytonaSessionRecord`` seam.
"""

from __future__ import annotations

import asyncio
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
async def test_failed_root_creation_keeps_late_ownership_visible() -> None:
    async def acquire(_spec: RootSessionSpec, **_kwargs: object) -> object:
        await asyncio.sleep(10.0)
        return _closable()  # pragma: no cover

    runtime = DaytonaRuntime(root_acquirer=acquire, root_releaser=lambda lease: lease.release())
    spec = RootSessionSpec(
        workspace_id=uuid4(),
        session_id=uuid4(),
        deadline=asyncio.get_running_loop().time() + 0.05,
    )

    with pytest.raises((TimeoutError, asyncio.TimeoutError)):
        await runtime.acquire_root_session(spec)

    # The timed-out create remains tracked until its Sandbox settles.
    assert runtime.has_pending_ownership
    assert await runtime.aclose(deadline=asyncio.get_running_loop().time() + 5.0) is True
    assert not runtime.has_pending_ownership


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
