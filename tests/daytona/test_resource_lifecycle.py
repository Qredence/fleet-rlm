"""Confirmed cleanup owns capacity independently of caller deadlines."""

import asyncio
from types import SimpleNamespace

import pytest

from fleet_rlm.daytona.leases import DaytonaAdmission, SandboxResource
from tests.support.session_manager import make_daytona_runtime


class CleanupSDK:
    """Explicit deletion acceptance, completion, and failure controls."""

    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.complete = asyncio.Event()
        self.present = True
        self.failure: Exception | None = None
        self.calls = 0

    async def delete(self, sandbox, *, wait: bool, timeout: float) -> None:
        assert wait and timeout > 0
        assert sandbox == "owned"
        self.calls += 1
        self.entered.set()
        await self.complete.wait()
        if self.failure:
            raise self.failure

    async def get(self, sandbox_id: str):
        assert sandbox_id == "owned"
        return SimpleNamespace(id=sandbox_id, state="running") if self.present else None


@pytest.mark.asyncio
async def test_cleanup_wait_timeout_preserves_task_and_admission_until_confirmed_absent() -> None:
    sdk = CleanupSDK()
    admission = DaytonaAdmission(max_active_leases=1)
    loop = asyncio.get_running_loop()
    permit = await admission.acquire(deadline=loop.time() + 1)
    owner = make_daytona_runtime(platform=sdk, admission=admission)
    resource = SandboxResource(owner=owner, sandbox=SimpleNamespace(id="owned"), sandbox_id="owned", permit=permit)
    waiter = asyncio.create_task(resource.aclose(deadline=loop.time() + 0.02))
    await sdk.entered.wait()
    with pytest.raises(TimeoutError):
        await waiter
    assert not permit._released
    assert resource in owner._sandbox_leases
    second = asyncio.create_task(resource.aclose())
    sdk.present = False
    sdk.complete.set()
    assert await second
    assert sdk.calls == 1
    assert permit._released
    assert resource not in owner._sandbox_leases
    replacement = await admission.acquire(deadline=loop.time() + 1)
    replacement.release()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_failure", [False, True])
async def test_unconfirmed_cleanup_retains_record_and_permit_for_retry(provider_failure: bool) -> None:
    sdk = CleanupSDK()
    sdk.complete.set()
    sdk.failure = RuntimeError("delete rejected") if provider_failure else None
    admission = DaytonaAdmission(max_active_leases=1)
    loop = asyncio.get_running_loop()
    permit = await admission.acquire(deadline=loop.time() + 1)
    owner = make_daytona_runtime(platform=sdk, admission=admission)
    resource = SandboxResource(owner=owner, sandbox=SimpleNamespace(id="owned"), sandbox_id="owned", permit=permit)
    assert await resource.aclose() is False
    assert resource.failed
    assert resource.failure is not None
    assert resource in owner._sandbox_leases
    assert not permit._released
    sdk.failure = None
    sdk.present = False
    assert await resource.aclose()
    assert sdk.calls == 2
    assert permit._released
    assert await resource.aclose()
    assert sdk.calls == 2


@pytest.mark.asyncio
async def test_concurrent_cleanup_callers_join_one_provider_operation() -> None:
    sdk = CleanupSDK()
    owner = make_daytona_runtime(platform=sdk)
    resource = SandboxResource(owner=owner, sandbox=SimpleNamespace(id="owned"))
    first = asyncio.create_task(resource.aclose())
    await sdk.entered.wait()
    second = asyncio.create_task(resource.aclose())
    await asyncio.sleep(0)
    assert sdk.calls == 1
    sdk.present = False
    sdk.complete.set()
    assert await first
    assert await second
    assert sdk.calls == 1


@pytest.mark.asyncio
async def test_late_child_create_requests_delete_when_sandbox_id_is_unavailable() -> None:
    """Late create must request provider delete even when sandbox_id_for fails."""

    class IdlessSandbox:
        pass

    class Platform:
        def __init__(self) -> None:
            self.deleted: list[object] = []

        async def create(self, **_kwargs: object) -> IdlessSandbox:
            return IdlessSandbox()

        async def delete(self, sandbox: object, **_kwargs: object) -> None:
            self.deleted.append(sandbox)

        async def get(self, sandbox_id: str):
            del sandbox_id
            return None

    platform = Platform()
    admission = DaytonaAdmission(max_active_leases=1)
    runtime = make_daytona_runtime(platform=platform, admission=admission)
    loop = asyncio.get_running_loop()
    permit = await admission.acquire(deadline=loop.time() + 1)
    creation = asyncio.create_task(platform.create())
    runtime._children._adopt_late_child_create(
        creation,
        platform=platform,
        permit=permit,
        mount_path=None,
        retain_pending_cleanup=None,
    )
    await asyncio.wait_for(asyncio.shield(creation), timeout=1)
    for _ in range(50):
        if permit._released and platform.deleted:
            break
        await asyncio.sleep(0.02)
    assert len(platform.deleted) == 1
    assert isinstance(platform.deleted[0], IdlessSandbox)
    # Successful delete of an opaque handle is best-effort confirmation.
    assert permit._released
    assert runtime._children._unidentified_child_sandboxes == []
