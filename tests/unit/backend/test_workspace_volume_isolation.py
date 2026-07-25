"""B4: Workspace Volume Scope isolation — subpath mount + acquire verify."""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID, uuid4

import pytest

from fleet_rlm.daytona.bindings import InMemoryBindingStore, SandboxBinding
from fleet_rlm.daytona.errors import DaytonaAdapterError
from fleet_rlm.daytona.platform import LiveDaytonaPlatform
from fleet_rlm.daytona.provisioning import (
    DaytonaSandboxSpec,
    ExpectedWorkspaceMount,
    VolumeConfig,
    require_scoped_volume_subpath,
    verify_sandbox_workspace_mount,
    volume_mount_spec,
    workspace_volume_subpath,
)
from fleet_rlm.daytona.session_manager import (
    DaytonaSessionManager,
    LeaseRequest,
)

_SPEC = DaytonaSandboxSpec("fleet-test-v1")


class _FakeVolume:
    id = "vol-shared"


class _FakeVolumeClient:
    async def get(self, name: str, *, create: bool = False) -> _FakeVolume:
        del name, create
        return _FakeVolume()


class _FakeFileInfo:
    def __init__(self, *, is_dir: bool) -> None:
        self.is_dir = is_dir


class _FakeFilesystem:
    def __init__(self, mount_path: str) -> None:
        self.directories = {mount_path}
        self.files: set[str] = set()

    async def get_file_info(self, path: str) -> _FakeFileInfo:
        if path in self.directories:
            return _FakeFileInfo(is_dir=True)
        if path in self.files:
            return _FakeFileInfo(is_dir=False)
        if path not in self.directories:
            raise FileNotFoundError(path)

    async def create_folder(self, path: str, mode: str) -> None:
        del mode
        self.directories.add(path)

    async def upload_file(self, data: bytes, path: str) -> None:
        del data
        self.files.add(path)


class _FakeSandbox:
    def __init__(
        self,
        sandbox_id: str,
        *,
        volume_id: str,
        mount_path: str,
        volume_subpath: str,
        labels: dict[str, str],
    ) -> None:
        self.id = sandbox_id
        self.state = "running"
        self.volume_id = volume_id
        self.mount_path = mount_path
        self.volume_subpath = volume_subpath
        self.labels = labels
        self.snapshot = _SPEC.snapshot
        self.fs = _FakeFilesystem(mount_path)
        self.volumes = [
            {
                "volume_id": volume_id,
                "mount_path": mount_path,
                "subpath": volume_subpath,
            }
        ]
        self.backend = None


class _FakePlatform:
    def __init__(self) -> None:
        self.sandboxes: dict[str, _FakeSandbox] = {}
        self.created: list[dict[str, Any]] = []
        self.deleted: list[str] = []
        self._n = 0

    async def get(self, sandbox_id: str) -> _FakeSandbox | None:
        return self.sandboxes.get(sandbox_id)

    async def create(
        self,
        *,
        volume_id: str,
        mount_path: str,
        volume_subpath: str,
        labels: dict[str, str] | None = None,
        ephemeral: bool = False,
    ) -> _FakeSandbox:
        del ephemeral
        require_scoped_volume_subpath(volume_subpath)
        self._n += 1
        sid = f"sb-{self._n}"
        labels = labels or {}
        sb = _FakeSandbox(
            sid,
            volume_id=volume_id,
            mount_path=mount_path,
            volume_subpath=volume_subpath,
            labels=labels,
        )
        self.sandboxes[sid] = sb
        self.created.append(
            {
                "volume_id": volume_id,
                "mount_path": mount_path,
                "volume_subpath": volume_subpath,
                "labels": labels,
                "id": sid,
            }
        )
        return sb

    async def delete(self, sandbox_id: str) -> None:
        self.deleted.append(sandbox_id)
        self.sandboxes.pop(sandbox_id, None)


def _manager() -> tuple[DaytonaSessionManager, _FakePlatform, InMemoryBindingStore]:
    plat = _FakePlatform()
    store = InMemoryBindingStore()
    mgr = DaytonaSessionManager(
        platform=plat,
        volume_client=_FakeVolumeClient(),
        volume_config=VolumeConfig(),
        bindings=store,
        sandbox_spec=_SPEC,
    )
    return mgr, plat, store


async def _acquire(mgr: DaytonaSessionManager, request: LeaseRequest):
    return await mgr.acquire(request, deadline=asyncio.get_running_loop().time() + 10)


def test_workspace_volume_subpath_canonical() -> None:
    wid = uuid4()
    assert workspace_volume_subpath(wid) == f"workspaces/{wid}"
    with pytest.raises(ValueError, match="zero UUID"):
        workspace_volume_subpath(UUID(int=0))


def test_volume_mount_spec_requires_workspace_subpath() -> None:
    wid = uuid4()
    spec = volume_mount_spec(VolumeConfig(), "vol-1", workspace_id=wid)
    assert spec["subpath"] == f"workspaces/{wid}"
    assert "subpath" in spec
    with pytest.raises(ValueError, match="zero UUID"):
        volume_mount_spec(VolumeConfig(), "vol-1", workspace_id=UUID(int=0))


@pytest.mark.asyncio
async def test_live_platform_rejects_unscoped_volume_mount() -> None:
    class _Client:
        async def create(self, params: Any) -> Any:
            raise AssertionError("must not create unscoped sandbox")

    platform = LiveDaytonaPlatform(_Client(), _SPEC)
    with pytest.raises(ValueError, match="without workspace subpath"):
        await platform.create(
            volume_id="vol-1",
            mount_path="/home/daytona/fleet",
            volume_subpath=None,
            with_volume=True,
        )


@pytest.mark.asyncio
async def test_acquire_persists_binding_workspace_scope_fields() -> None:
    mgr, plat, store = _manager()
    req = LeaseRequest(session_id=uuid4(), user_id=uuid4(), workspace_id=uuid4())
    lease = await _acquire(mgr, req)
    binding = await store.get(req.session_id)
    assert binding is not None
    assert binding.workspace_id == req.workspace_id
    assert binding.volume_id == lease.volume_id
    assert binding.volume_subpath == f"workspaces/{req.workspace_id}"
    assert binding.mount_path == lease.mount_path
    assert plat.created[0]["volume_subpath"] == binding.volume_subpath
    assert plat.created[0]["labels"]["workspace_id"] == str(req.workspace_id)


@pytest.mark.asyncio
async def test_sibling_workspaces_get_distinct_subpaths() -> None:
    mgr, plat, _store = _manager()
    ws_a = uuid4()
    ws_b = uuid4()
    lease_a = await _acquire(mgr, LeaseRequest(session_id=uuid4(), user_id=uuid4(), workspace_id=ws_a))
    lease_b = await _acquire(mgr, LeaseRequest(session_id=uuid4(), user_id=uuid4(), workspace_id=ws_b))
    assert lease_a.volume_id == lease_b.volume_id
    assert lease_a.volume_subpath != lease_b.volume_subpath
    assert lease_a.volume_subpath == f"workspaces/{ws_a}"
    assert lease_b.volume_subpath == f"workspaces/{ws_b}"
    assert {c["volume_subpath"] for c in plat.created} == {
        f"workspaces/{ws_a}",
        f"workspaces/{ws_b}",
    }


@pytest.mark.asyncio
async def test_acquire_rejects_binding_with_wrong_workspace_scope_without_replacement() -> None:
    mgr, plat, store = _manager()
    req = LeaseRequest(session_id=uuid4(), user_id=uuid4(), workspace_id=uuid4())
    lease = await _acquire(mgr, req)
    await mgr.release(lease)
    wrong_ws = uuid4()
    # Simulate a corrupted/stale binding pointing at another workspace subpath.
    await store.upsert(
        SandboxBinding(
            session_id=req.session_id,
            sandbox_id=lease.sandbox_id,
            workspace_id=wrong_ws,
            volume_id=lease.volume_id,
            volume_subpath=f"workspaces/{wrong_ws}",
            mount_path=lease.mount_path,
            provider_state="running",
        )
    )
    with pytest.raises(DaytonaAdapterError, match="binding does not match"):
        await _acquire(mgr, req)
    assert plat.deleted == []


@pytest.mark.asyncio
async def test_acquire_rejects_live_mount_mismatch_without_replacement() -> None:
    mgr, plat, store = _manager()
    req = LeaseRequest(session_id=uuid4(), user_id=uuid4(), workspace_id=uuid4())
    lease = await _acquire(mgr, req)
    await mgr.release(lease)
    sandbox = plat.sandboxes[lease.sandbox_id]
    other = uuid4()
    sandbox.volume_subpath = f"workspaces/{other}"
    sandbox.volumes = [
        {
            "volume_id": lease.volume_id,
            "mount_path": lease.mount_path,
            "subpath": f"workspaces/{other}",
        }
    ]
    with pytest.raises(DaytonaAdapterError, match="volume mount does not match"):
        await _acquire(mgr, req)
    assert plat.deleted == []


def test_verify_sandbox_workspace_mount_fail_closed() -> None:
    wid = uuid4()
    expected = ExpectedWorkspaceMount(
        volume_id="vol-1",
        volume_subpath=f"workspaces/{wid}",
        mount_path="/home/daytona/fleet",
        workspace_id=wid,
    )
    bad = _FakeSandbox(
        "sb-x",
        volume_id="vol-1",
        mount_path="/home/daytona/fleet",
        volume_subpath=f"workspaces/{uuid4()}",
        labels={"workspace_id": str(wid)},
    )
    with pytest.raises(DaytonaAdapterError) as exc:
        verify_sandbox_workspace_mount(bad, expected)
    assert exc.value.cause_type == "WorkspaceMountMismatch"


@pytest.mark.asyncio
async def test_binding_store_rejects_zero_workspace_on_upsert() -> None:
    store = InMemoryBindingStore()
    with pytest.raises(ValueError, match="zero UUID"):
        await store.upsert(
            SandboxBinding(
                session_id=uuid4(),
                sandbox_id="sb-1",
                workspace_id=UUID(int=0),
                volume_id="vol-1",
                volume_subpath="workspaces/00000000-0000-0000-0000-000000000000",
                mount_path="/home/daytona/fleet",
                provider_state="running",
            )
        )


@pytest.mark.asyncio
async def test_replace_rejects_zero_workspace_id() -> None:
    mgr, _plat, store = _manager()
    req = LeaseRequest(session_id=uuid4(), user_id=uuid4(), workspace_id=uuid4())
    lease = await _acquire(mgr, req)
    binding = await store.get(req.session_id)
    assert binding is not None
    with pytest.raises(ValueError, match="zero UUID"):
        await mgr.replace(binding, workspace_id=UUID(int=0), user_id=req.user_id)
    # Binding still usable with real workspace.
    replaced = await mgr.replace(binding, workspace_id=req.workspace_id, user_id=req.user_id)
    assert replaced.workspace_id == req.workspace_id
    assert replaced.sandbox_id != lease.sandbox_id
