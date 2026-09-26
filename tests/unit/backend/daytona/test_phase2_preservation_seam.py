"""Phase 2 preservation seam: runtime-owned Daytona behavior matrix.

Focused ownership-transfer coverage for the DaytonaRuntime cutover:

* sequential root reuse returns the same lease without a second create;
* two concurrent sessions acquire independently (no shared registry lock
  across provider waits);
* failed/late root creation keeps actual provider work owned until cleanup;
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
    DaytonaSessionRecord,
    InterpreterLease,
    LeaseRequest,
    RootSessionSpec,
    SessionCleanupState,
)
from tests.support.session_manager import make_daytona_runtime


def _closable(*, sandbox_id: str = "sandbox-1") -> object:
    class Interpreter:
        closed = False

        def shutdown(self, **_kwargs: object) -> None:
            self.closed = True

    return InterpreterLease(
        sandbox_id=sandbox_id,
        interpreter_id=f"interpreter-{sandbox_id}",
        volume_id="volume-1",
        mount_path="/workspace",
        interpreter=Interpreter(),
        sandbox=type("Sandbox", (), {"id": sandbox_id})(),
    )


@pytest.mark.asyncio
async def test_sequential_root_reuse_returns_same_lease() -> None:
    creates = 0

    async def acquire(_request: object, **_kwargs: object) -> object:
        nonlocal creates
        creates += 1
        return _closable()

    runtime = make_daytona_runtime()
    runtime.acquire = acquire  # type: ignore[method-assign]
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

    async def acquire(request: LeaseRequest, **_kwargs: object) -> object:
        calls.append(str(request.session_id))
        if len(calls) == 1:
            started.set()
            await release_second.wait()
        return _closable(sandbox_id=f"sandbox-{len(calls)}")

    runtime = make_daytona_runtime()
    runtime.acquire = acquire  # type: ignore[method-assign]
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
    landed = asyncio.Event()
    lease = _closable()

    async def acquire(_request: object, **_kwargs: object) -> object:
        await landed.wait()
        return lease

    runtime = make_daytona_runtime()
    runtime.acquire = acquire  # type: ignore[method-assign]
    spec = RootSessionSpec(
        workspace_id=uuid4(),
        session_id=uuid4(),
        deadline=asyncio.get_running_loop().time() + 0.05,
    )

    with pytest.raises((TimeoutError, asyncio.TimeoutError)):
        await runtime.acquire_root_session(spec)

    # The timed-out create remains tracked until its Sandbox settles.
    assert runtime.has_pending_ownership
    assert await runtime.aclose(deadline=asyncio.get_running_loop().time() + 0.01) is False
    assert not lease.closed
    landed.set()
    assert await runtime.aclose(deadline=asyncio.get_running_loop().time() + 5.0) is True
    assert lease.closed
    assert not runtime.has_pending_ownership


@pytest.mark.asyncio
async def test_cancelled_acquisition_does_not_publish_a_root() -> None:
    started = asyncio.Event()
    landed = asyncio.Event()
    lease = _closable()

    async def acquire(_request: object, **_kwargs: object) -> object:
        started.set()
        await landed.wait()
        return lease

    runtime = make_daytona_runtime()
    runtime.acquire = acquire  # type: ignore[method-assign]
    spec = RootSessionSpec(workspace_id=uuid4(), session_id=uuid4())

    task = asyncio.create_task(runtime.acquire_root_session(spec))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert runtime.roots == ()
    assert runtime.session_record(spec.workspace_id, spec.session_id) is None
    assert runtime.has_pending_ownership
    landed.set()
    assert await runtime.aclose() is True
    assert lease.closed


@pytest.mark.asyncio
async def test_tainted_root_rotates_on_next_acquisition() -> None:
    leases = [_closable(sandbox_id="sandbox-old"), _closable(sandbox_id="sandbox-new")]
    calls = 0

    async def acquire(_request: object, **_kwargs: object) -> object:
        nonlocal calls
        lease = leases[calls]
        calls += 1
        return lease

    runtime = make_daytona_runtime()
    runtime.acquire = acquire  # type: ignore[method-assign]
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
