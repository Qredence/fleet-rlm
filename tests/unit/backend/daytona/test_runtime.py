"""Public Daytona root/child runtime lifecycle contracts."""

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace
from uuid import uuid4

import pytest

from fleet_rlm.daytona._lease import LeaseState, RootSessionLease
from fleet_rlm.daytona.runtime import ChildEnvironmentSpec, DaytonaRuntime, DaytonaRuntimeState, RootSessionSpec


@dataclass
class FakeRoot:
    name: str
    released: int = 0
    fail: bool = False

    async def release(self) -> None:
        self.released += 1
        if self.fail:
            raise RuntimeError("release failed")


@pytest.mark.asyncio
async def test_root():
    roots = []

    async def factory(*, spec: RootSessionSpec, force_new: bool = False):
        del spec, force_new
        x = FakeRoot(str(len(roots)))
        roots.append(x)
        return x

    runtime = DaytonaRuntime(root_acquirer=factory)
    spec = RootSessionSpec("w", "s", context_fingerprint="a")
    first = await runtime.acquire_root_session(spec)
    assert await runtime.acquire_root_session(spec) is first
    second = await runtime.acquire_root_session(RootSessionSpec("w", "s", context_fingerprint="b"))
    assert second is not first
    assert first.state is LeaseState.CLOSED
    assert roots[0].released == 1
    runtime.mark_root_tainted("w", "s")
    third = await runtime.acquire_root_session(RootSessionSpec("w", "s", context_fingerprint="b"))
    assert third is not second
    assert second.state is LeaseState.CLOSED
    assert await runtime.aclose() is True
    assert runtime.state is DaytonaRuntimeState.CLOSED
    assert third.state is LeaseState.CLOSED


@pytest.mark.asyncio
async def test_child():
    closed = []

    class C:
        async def close(self):
            closed.append(1)

    async def f(spec: ChildEnvironmentSpec):
        del spec
        return C()

    runtime = DaytonaRuntime(child_acquirer=f)
    async with runtime.open_child(ChildEnvironmentSpec(call_index=1)) as child:
        assert child.state is LeaseState.OPEN
    assert child.state is LeaseState.CLOSED
    assert closed == [1]


@pytest.mark.asyncio
async def test_root_failure():
    value = FakeRoot("x", fail=True)
    runtime = DaytonaRuntime(root_acquirer=lambda _spec: value)
    owner = await runtime.acquire_root_session(RootSessionSpec("w", "s"))
    assert await runtime.aclose() is False
    assert runtime.state is DaytonaRuntimeState.FAILED
    assert owner.state is LeaseState.FAILED


@pytest.mark.asyncio
async def test_concurrent_close():
    started = asyncio.Event()
    done = asyncio.Event()
    calls = 0

    async def release(_):
        nonlocal calls
        calls += 1
        started.set()
        await done.wait()

    owner = RootSessionLease("k", object(), release)
    first = asyncio.create_task(owner.close())
    await started.wait()
    second = asyncio.create_task(owner.close())
    await asyncio.sleep(0)
    assert owner.state is LeaseState.CLOSING
    done.set()
    await asyncio.gather(first, second)
    assert calls == 1 and owner.state is LeaseState.CLOSED


@pytest.mark.asyncio
async def test_runtime_close_retires_unentered_child() -> None:
    closed: list[int] = []

    class Child:
        async def close(self) -> None:
            closed.append(1)

    async def factory(_spec: ChildEnvironmentSpec) -> Child:
        return Child()

    runtime = DaytonaRuntime(child_acquirer=factory)
    context = runtime.open_child(ChildEnvironmentSpec(call_index=7))
    child = await context.__aenter__()
    assert child in runtime.children
    assert await runtime.aclose() is True
    assert child.state is LeaseState.CLOSED
    assert closed == [1]
    assert runtime.children == ()


@pytest.mark.asyncio
async def test_root_replacement_does_not_deadlock_when_close_callback_is_pending() -> None:
    started = asyncio.Event()
    release_now = asyncio.Event()
    roots: list[FakeRoot] = []

    async def release(root: FakeRoot) -> None:
        started.set()
        await release_now.wait()
        await root.release()

    def factory(_spec: RootSessionSpec) -> FakeRoot:
        root = FakeRoot(str(len(roots)))
        roots.append(root)
        return root

    runtime = DaytonaRuntime(root_acquirer=factory, root_releaser=release)
    first = await runtime.acquire_root_session(RootSessionSpec("w", "s", context_fingerprint="a"))
    closing = asyncio.create_task(first.close())
    await started.wait()
    replacing = asyncio.create_task(runtime.acquire_root_session(RootSessionSpec("w", "s", context_fingerprint="b")))
    await asyncio.sleep(0)
    release_now.set()
    second = await replacing
    await closing
    assert second is not first
    assert first.state is LeaseState.CLOSED
    await runtime.aclose()


@pytest.mark.asyncio
async def test_root_acquisition_timeout_closes_late_lease() -> None:
    started = asyncio.Event()
    finish = asyncio.Event()
    released = 0

    class LateRoot:
        async def release(self) -> None:
            nonlocal released
            released += 1

    async def factory(_spec: RootSessionSpec) -> LateRoot:
        started.set()
        await finish.wait()
        return LateRoot()

    runtime = DaytonaRuntime(root_acquirer=factory)
    spec = RootSessionSpec("w", "s", deadline=asyncio.get_running_loop().time() + 0.01)
    with pytest.raises(TimeoutError):
        await runtime.acquire_root_session(spec)
    finish.set()
    for _ in range(20):
        await asyncio.sleep(0)
        if released:
            break
    assert released == 1
    assert await runtime.aclose() is True


@pytest.mark.asyncio
async def test_cancelled_root_acquisition_retains_late_lease() -> None:
    started = asyncio.Event()
    finish = asyncio.Event()
    released = 0

    class LateRoot:
        async def release(self) -> None:
            nonlocal released
            released += 1

    async def factory(_spec: RootSessionSpec) -> LateRoot:
        started.set()
        await finish.wait()
        return LateRoot()

    runtime = DaytonaRuntime(root_acquirer=factory)
    acquisition = asyncio.create_task(runtime.acquire_root_session(RootSessionSpec("w", "s")))
    await started.wait()
    acquisition.cancel()
    with pytest.raises(asyncio.CancelledError):
        await acquisition
    finish.set()
    for _ in range(20):
        await asyncio.sleep(0)
        if released:
            break
    assert released == 1
    assert await runtime.aclose() is True


@pytest.mark.asyncio
async def test_cancelled_child_acquisition_closes_late_lease() -> None:
    started = asyncio.Event()
    finish = asyncio.Event()
    closed = 0

    class LateChild:
        async def close(self) -> None:
            nonlocal closed
            closed += 1

    async def factory(_spec: ChildEnvironmentSpec) -> LateChild:
        started.set()
        await finish.wait()
        return LateChild()

    runtime = DaytonaRuntime(child_acquirer=factory)
    context = runtime.open_child(ChildEnvironmentSpec())
    acquisition = asyncio.create_task(context.__aenter__())
    await started.wait()
    acquisition.cancel()
    with pytest.raises(asyncio.CancelledError):
        await acquisition
    finish.set()
    for _ in range(20):
        await asyncio.sleep(0)
        if closed:
            break
    assert closed == 1
    assert await runtime.aclose() is True


@pytest.mark.asyncio
async def test_child_acquisition_started_before_shutdown_is_drained() -> None:
    started = asyncio.Event()
    finish = asyncio.Event()
    closed = 0

    class Child:
        async def close(self) -> None:
            nonlocal closed
            closed += 1

    async def factory(_spec: ChildEnvironmentSpec) -> Child:
        started.set()
        await finish.wait()
        return Child()

    runtime = DaytonaRuntime(child_acquirer=factory)
    context = runtime.open_child(ChildEnvironmentSpec())
    entering = asyncio.create_task(context.__aenter__())
    await started.wait()
    closing = asyncio.create_task(runtime.aclose())
    await asyncio.sleep(0)
    finish.set()

    with pytest.raises(RuntimeError, match="Daytona runtime is closing"):
        await entering
    assert await closing is True
    assert closed == 1
    assert runtime.state is DaytonaRuntimeState.CLOSED


@pytest.mark.asyncio
async def test_root_failed_receipt_is_not_published_as_closed() -> None:
    class FailedReceipt:
        first_error = "provider cleanup failed"

    async def release(_lease: object) -> FailedReceipt:
        return FailedReceipt()

    owner = RootSessionLease("k", object(), release)
    with pytest.raises(RuntimeError, match="root Session cleanup failed"):
        await owner.close()
    assert owner.state is LeaseState.FAILED
    assert owner.closed is False


@pytest.mark.asyncio
async def test_resource_root_lookup_failure_releases_manager_lease() -> None:
    lease = SimpleNamespace(sandbox_id="sandbox-1")
    released: list[object] = []
    quarantined: list[tuple[object, object]] = []

    class Manager:
        async def acquire(self, request, *, deadline, force_new):
            del request, deadline, force_new
            return lease

        async def quarantine(self, value, request, *, deadline):
            del deadline
            quarantined.append((value, request))

        async def release(self, value):
            released.append(value)

    class Platform:
        async def get(self, sandbox_id):
            assert sandbox_id == "sandbox-1"
            raise RuntimeError("provider lookup failed")

    runtime = DaytonaRuntime(resources=SimpleNamespace(session_manager=Manager(), platform=Platform()))
    with pytest.raises(RuntimeError, match="provider lookup failed"):
        await runtime._acquire_from_resources(RootSessionSpec(workspace_id=uuid4(), session_id=uuid4()))
    assert quarantined and quarantined[0][0] is lease
    assert released == [lease]


@pytest.mark.asyncio
async def test_resource_root_missing_sandbox_quarantines_and_releases_manager_lease() -> None:
    lease = SimpleNamespace(sandbox_id="sandbox-missing")
    calls: list[str] = []

    class Manager:
        async def acquire(self, request, *, deadline, force_new):
            del request, deadline, force_new
            return lease

        async def quarantine(self, value, request, *, deadline):
            del value, request, deadline
            calls.append("quarantine")

        async def release(self, value):
            assert value is lease
            calls.append("release")

    class Platform:
        async def get(self, sandbox_id):
            assert sandbox_id == lease.sandbox_id
            return None

    runtime = DaytonaRuntime(resources=SimpleNamespace(session_manager=Manager(), platform=Platform()))
    with pytest.raises(RuntimeError, match="Sandbox is unavailable"):
        await runtime._acquire_from_resources(RootSessionSpec(workspace_id=uuid4(), session_id=uuid4()))
    assert calls == ["release", "quarantine"]


@pytest.mark.asyncio
async def test_resource_root_lookup_cleanup_error_is_not_hidden() -> None:
    lease = SimpleNamespace(sandbox_id="sandbox-1")
    released: list[object] = []

    class Manager:
        async def acquire(self, request, *, deadline, force_new):
            del request, deadline, force_new
            return lease

        async def quarantine(self, value, request, *, deadline):
            del value, request, deadline
            raise RuntimeError("quarantine failed")

        async def release(self, value):
            released.append(value)

    class Platform:
        async def get(self, sandbox_id):
            del sandbox_id
            return None

    runtime = DaytonaRuntime(resources=SimpleNamespace(session_manager=Manager(), platform=Platform()))
    with pytest.raises(RuntimeError, match="quarantine failed"):
        await runtime._acquire_from_resources(RootSessionSpec(workspace_id=uuid4(), session_id=uuid4()))
    assert released == [lease]
