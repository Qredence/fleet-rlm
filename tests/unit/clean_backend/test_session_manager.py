"""impl-08: DaytonaSessionManager lifecycle and leases (fake platform, no network)."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from fleet_rlm_clean.daytona.bindings import InMemoryBindingStore, SandboxBinding
from fleet_rlm_clean.daytona.lifecycle import LifecycleCapabilityError
from fleet_rlm_clean.daytona.session_manager import DaytonaSessionManager, LeaseRequest
from fleet_rlm_clean.daytona.volumes import VolumeConfig


class _FakeVolume:
    def __init__(self, volume_id: str = "vol-1") -> None:
        self.id = volume_id


class _FakeVolumeClient:
    def __init__(self) -> None:
        self.gets: list[tuple[str, bool]] = []

    def get(self, name: str, *, create: bool = False) -> _FakeVolume:
        self.gets.append((name, create))
        return _FakeVolume(f"vol-{name}")


class _FakeSandbox:
    def __init__(self, sandbox_id: str, state: str = "running") -> None:
        self.id = sandbox_id
        self.state = state
        self.ops: list[str] = []
        self.backend = None  # interpreter will tolerate missing backend until execute

    def start(self) -> None:
        self.ops.append("start")
        self.state = "running"

    def stop(self) -> None:
        self.ops.append("stop")
        self.state = "stopped"

    def pause(self) -> None:
        self.ops.append("pause")
        self.state = "paused"

    def resume(self) -> None:
        self.ops.append("resume")
        self.state = "running"

    def archive(self) -> None:
        self.ops.append("archive")
        self.state = "archived"

    def restore(self) -> None:
        self.ops.append("restore")
        self.state = "stopped"


class _LimitedSandbox:
    """Only stop/start — no pause/archive methods at all."""

    def __init__(self, sandbox_id: str, state: str = "running") -> None:
        self.id = sandbox_id
        self.state = state
        self.ops: list[str] = []
        self.backend = None

    def start(self) -> None:
        self.ops.append("start")
        self.state = "running"

    def stop(self) -> None:
        self.ops.append("stop")
        self.state = "stopped"


class _FakePlatform:
    def __init__(self) -> None:
        self.sandboxes: dict[str, _FakeSandbox] = {}
        self.created: list[dict[str, Any]] = []
        self.deleted: list[str] = []
        self._n = 0

    def get(self, sandbox_id: str) -> _FakeSandbox | None:
        return self.sandboxes.get(sandbox_id)

    def create(
        self,
        *,
        volume_id: str,
        mount_path: str,
        labels: dict[str, str] | None = None,
    ) -> _FakeSandbox:
        self._n += 1
        sid = f"sb-{self._n}"
        sb = _FakeSandbox(sid, state="running")
        self.sandboxes[sid] = sb
        self.created.append(
            {"volume_id": volume_id, "mount_path": mount_path, "labels": labels or {}, "id": sid}
        )
        return sb

    def delete(self, sandbox_id: str) -> None:
        self.deleted.append(sandbox_id)
        self.sandboxes.pop(sandbox_id, None)


def _manager(
    platform: _FakePlatform | None = None,
    bindings: InMemoryBindingStore | None = None,
) -> tuple[DaytonaSessionManager, _FakePlatform, InMemoryBindingStore, _FakeVolumeClient]:
    plat = platform or _FakePlatform()
    store = bindings or InMemoryBindingStore()
    volumes = _FakeVolumeClient()
    mgr = DaytonaSessionManager(
        platform=plat,
        volume_client=volumes,
        volume_config=VolumeConfig(),
        bindings=store,
    )
    return mgr, plat, store, volumes


def _request() -> LeaseRequest:
    return LeaseRequest(session_id=uuid4(), user_id=uuid4(), workspace_id=uuid4())


@pytest.mark.asyncio
async def test_acquire_creates_running_sandbox_and_lease() -> None:
    mgr, plat, store, volumes = _manager()
    req = _request()
    lease = await mgr.acquire(req)

    assert lease.sandbox_id in plat.sandboxes
    assert lease.volume_id.startswith("vol-")
    assert lease.mount_path == "/home/daytona/fleet"
    assert volumes.gets  # volume resolved
    binding = await store.get(req.session_id)
    assert binding is not None
    assert binding.provider_state == "running"
    assert binding.sandbox_id == lease.sandbox_id


@pytest.mark.asyncio
async def test_acquire_reuses_running_sandbox() -> None:
    mgr, plat, store, _volumes = _manager()
    req = _request()
    first = await mgr.acquire(req)
    await mgr.release(first)  # release active lease before re-acquire
    second = await mgr.acquire(req)
    assert first.sandbox_id == second.sandbox_id
    assert len(plat.created) == 1


@pytest.mark.asyncio
async def test_stop_start_lifecycle() -> None:
    mgr, plat, _store, _volumes = _manager()
    req = _request()
    lease = await mgr.acquire(req)
    await mgr.stop(lease.sandbox_id)
    assert plat.sandboxes[lease.sandbox_id].state == "stopped"
    await mgr.start(lease.sandbox_id)
    assert plat.sandboxes[lease.sandbox_id].state == "running"


@pytest.mark.asyncio
async def test_pause_resume_when_supported() -> None:
    mgr, plat, _store, _volumes = _manager()
    req = _request()
    lease = await mgr.acquire(req)
    await mgr.pause(lease.sandbox_id)
    assert plat.sandboxes[lease.sandbox_id].state == "paused"
    await mgr.resume(lease.sandbox_id)
    assert plat.sandboxes[lease.sandbox_id].state == "running"


@pytest.mark.asyncio
async def test_pause_raises_when_unsupported() -> None:
    limited = _LimitedSandbox("lim-1", state="running")

    class _Plat:
        def get(self, sandbox_id: str) -> Any:
            return limited if sandbox_id == "lim-1" else None

        def create(self, **kwargs: Any) -> Any:
            raise AssertionError("create should not be called")

        def delete(self, sandbox_id: str) -> None:
            return None

    mgr = DaytonaSessionManager(
        platform=_Plat(),
        volume_client=_FakeVolumeClient(),
        volume_config=VolumeConfig(),
        bindings=InMemoryBindingStore(),
    )
    with pytest.raises(LifecycleCapabilityError):
        await mgr.pause("lim-1")


@pytest.mark.asyncio
async def test_release_never_deletes_sandbox() -> None:
    mgr, plat, _store, _volumes = _manager()
    req = _request()
    lease = await mgr.acquire(req)
    sid = lease.sandbox_id
    await mgr.release(lease)
    await mgr.release(lease)  # idempotent
    assert sid in plat.sandboxes
    assert plat.deleted == []


@pytest.mark.asyncio
async def test_acquire_starts_stopped_sandbox() -> None:
    mgr, plat, store, _volumes = _manager()
    req = _request()
    lease = await mgr.acquire(req)
    plat.sandboxes[lease.sandbox_id].state = "stopped"
    await store.upsert(
        SandboxBinding(
            session_id=req.session_id,
            sandbox_id=lease.sandbox_id,
            volume_id=lease.volume_id,
            mount_path=lease.mount_path,
            provider_state="stopped",
        )
    )
    await mgr.release(lease)
    again = await mgr.acquire(req)
    assert again.sandbox_id == lease.sandbox_id
    assert "start" in plat.sandboxes[lease.sandbox_id].ops


@pytest.mark.asyncio
async def test_replace_keeps_volume_id() -> None:
    mgr, plat, store, _volumes = _manager()
    req = _request()
    lease = await mgr.acquire(req)
    old_sid = lease.sandbox_id
    volume_id = lease.volume_id
    binding = await store.get(req.session_id)
    assert binding is not None
    new_binding = await mgr.replace(binding)
    assert new_binding.volume_id == volume_id
    assert new_binding.sandbox_id != old_sid
    assert old_sid in plat.deleted
    assert new_binding.sandbox_id in plat.sandboxes


@pytest.mark.asyncio
async def test_acquire_recreates_when_sandbox_missing() -> None:
    mgr, plat, store, _volumes = _manager()
    req = _request()
    first = await mgr.acquire(req)
    await mgr.release(first)
    # Simulate gone sandbox
    plat.sandboxes.clear()
    second = await mgr.acquire(req)
    assert second.sandbox_id != first.sandbox_id
    assert len(plat.created) == 2
