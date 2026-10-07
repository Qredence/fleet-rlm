"""B4: Workspace Volume Scope isolation — subpath mount + acquire verify."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from fleet_rlm.daytona import runtime as recursive_child_runtime
from fleet_rlm.daytona.errors import DaytonaAdapterError
from fleet_rlm.daytona.runtime import (
    SESSION_WORKSPACE_MOUNT_PATH,
    ChildRuntimeLeaseState,
    DaytonaAdmission,
    DaytonaRuntime,
    DaytonaSandboxSpec,
    ExpectedWorkspaceMount,
    LeaseRequest,
    LiveDaytonaPlatform,
    VolumeConfig,
    ensure_volume_layout,
    get_or_create_volume_id,
    required_volume_directories,
    verify_execution_mount,
    verify_sandbox_workspace_mount,
    volume_mount_spec,
)
from fleet_rlm.paths import (
    UnsafePathError,
    VolumePaths,
    resolve_under_root,
    validate_mount_path,
    validate_path_id,
)
from fleet_rlm.sessions.bindings import (
    SandboxBinding,
    require_scoped_volume_subpath,
    workspace_volume_subpath,
)
from tests.support.in_memory_stores import InMemorySandboxBindingStore as InMemoryBindingStore
from tests.support.session_manager import make_daytona_child_factory, make_daytona_runtime

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
        self.process = SimpleNamespace(exec=AsyncMock(return_value=SimpleNamespace(exit_code=0)))
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
        if mount_path == "/workspace":
            assert volume_subpath.startswith("workspaces/") and volume_subpath.endswith("/workspace")
        else:
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


def _manager() -> tuple[DaytonaRuntime, _FakePlatform, InMemoryBindingStore]:
    plat = _FakePlatform()
    store = InMemoryBindingStore()
    mgr = make_daytona_runtime(
        platform=plat,
        volume_client=_FakeVolumeClient(),
        volume_config=VolumeConfig(),
        bindings=store,
        sandbox_spec=_SPEC,
    )
    return mgr, plat, store


async def _acquire(mgr: DaytonaRuntime, request: LeaseRequest):
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
            del params
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
    assert binding.volume_subpath == f"workspaces/{req.workspace_id}/sessions/{req.session_id}/workspace"
    assert binding.mount_path == lease.mount_path
    assert plat.created[0]["volume_subpath"] == binding.volume_subpath
    assert plat.created[0]["labels"]["workspace_id"] == str(req.workspace_id)


@pytest.mark.asyncio
async def test_sibling_workspaces_get_distinct_subpaths() -> None:
    mgr, plat, _store = _manager()
    ws_a = uuid4()
    ws_b = uuid4()
    session_a = uuid4()
    session_b = uuid4()
    lease_a = await _acquire(mgr, LeaseRequest(session_id=session_a, user_id=uuid4(), workspace_id=ws_a))
    lease_b = await _acquire(mgr, LeaseRequest(session_id=session_b, user_id=uuid4(), workspace_id=ws_b))
    assert lease_a.volume_id == lease_b.volume_id
    assert lease_a.volume_subpath != lease_b.volume_subpath
    assert lease_a.volume_subpath == f"workspaces/{ws_a}/sessions/{session_a}/workspace"
    assert lease_b.volume_subpath == f"workspaces/{ws_b}/sessions/{session_b}/workspace"
    assert {c["volume_subpath"] for c in plat.created} == {
        f"workspaces/{ws_a}/sessions/{session_a}/workspace",
        f"workspaces/{ws_b}/sessions/{session_b}/workspace",
    }


@pytest.mark.asyncio
async def test_acquire_rejects_binding_with_wrong_workspace_scope_without_replacement() -> None:
    mgr, plat, store = _manager()
    req = LeaseRequest(session_id=uuid4(), user_id=uuid4(), workspace_id=uuid4())
    lease = await _acquire(mgr, req)
    await mgr.release(lease)
    wrong_ws = uuid4()
    # Simulate a corrupted/stale row without bypassing the store's
    # cross-workspace write fence.
    store._items[req.session_id] = SandboxBinding(
        session_id=req.session_id,
        sandbox_id=lease.sandbox_id,
        workspace_id=wrong_ws,
        volume_id=lease.volume_id,
        volume_subpath=f"workspaces/{wrong_ws}",
        mount_path=lease.mount_path,
        provider_state="running",
    )
    with pytest.raises(DaytonaAdapterError, match="binding does not match"):
        await _acquire(mgr, req)
    assert plat.deleted == []


@pytest.mark.asyncio
async def test_acquire_rejects_live_mount_mismatch_without_replacement() -> None:
    mgr, plat, _store = _manager()
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


def test_verify_sandbox_workspace_mount_rejects_missing_metadata() -> None:
    wid = uuid4()
    expected = ExpectedWorkspaceMount(
        volume_id="vol-1",
        volume_subpath=f"workspaces/{wid}",
        mount_path="/home/daytona/fleet",
        workspace_id=wid,
    )
    missing = type("SandboxWithoutMountMetadata", (), {"labels": {"workspace_id": str(wid)}})()

    with pytest.raises(DaytonaAdapterError, match="mount metadata is unavailable"):
        verify_sandbox_workspace_mount(missing, expected)


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
async def test_binding_store_cannot_overwrite_session_from_another_workspace() -> None:
    store = InMemoryBindingStore()
    session_id = uuid4()
    first_workspace = uuid4()
    await store.upsert(
        SandboxBinding(
            session_id=session_id,
            sandbox_id="sb-1",
            workspace_id=first_workspace,
            volume_id="vol-1",
            volume_subpath=workspace_volume_subpath(first_workspace),
            mount_path="/home/daytona/fleet",
            provider_state="running",
        )
    )
    other_workspace = uuid4()
    with pytest.raises(ValueError, match="workspace scope"):
        await store.upsert(
            SandboxBinding(
                session_id=session_id,
                sandbox_id="sb-2",
                workspace_id=other_workspace,
                volume_id="vol-1",
                volume_subpath=workspace_volume_subpath(other_workspace),
                mount_path="/home/daytona/fleet",
                provider_state="running",
            )
        )
    retained = await store.get(session_id)
    assert retained is not None
    assert retained.sandbox_id == "sb-1"


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


# ---------------------------------------------------------------------------
# Volume mount defaults, paths, and layout contracts
# ---------------------------------------------------------------------------


def test_removed_volume_namespaces_have_no_production_references() -> None:
    source_root = Path(__file__).parents[2] / "src" / "fleet_rlm"
    forbidden = (
        "skills_root",
        "memory_root",
        "session_exports_dir",
        "session_staging_dir",
        "run_staging_dir",
        "_ensure_skill_tree",
    )
    references = [
        f"{path}:{term}" for path in source_root.rglob("*.py") for term in forbidden if term in path.read_text()
    ]
    assert references == []


def test_validate_mount_path_rejects_unsafe() -> None:
    with pytest.raises(UnsafePathError):
        validate_mount_path("/")
    with pytest.raises(UnsafePathError):
        validate_mount_path("relative/path")
    with pytest.raises(UnsafePathError):
        validate_mount_path("/home/daytona/../etc")
    with pytest.raises(UnsafePathError):
        validate_mount_path("/etc/fleet")
    with pytest.raises(UnsafePathError):
        validate_mount_path("/home//daytona/fleet")
    with pytest.raises(UnsafePathError):
        validate_mount_path("")


def test_validate_path_id_rejects_traversal_and_separators() -> None:
    with pytest.raises(UnsafePathError):
        validate_path_id("../etc")
    with pytest.raises(UnsafePathError):
        validate_path_id("a/b")
    with pytest.raises(UnsafePathError):
        validate_path_id("not-a-uuid")
    with pytest.raises(UnsafePathError):
        validate_path_id("")
    with pytest.raises(UnsafePathError):
        validate_path_id("abc\x00def")
    sid = uuid4()
    assert validate_path_id(sid) == str(sid)
    assert validate_path_id(str(sid)) == str(sid)


def test_session_workspace_root_is_session_scoped() -> None:
    paths = VolumePaths.from_mount()
    session_id = uuid4()
    workspace = paths.session_workspace_dir(session_id)
    assert workspace == paths.session_dir(session_id) / "workspace"


def test_session_and_run_container_paths_are_canonical() -> None:
    paths = VolumePaths.from_mount()
    session_id = uuid4()
    run_id = uuid4()
    assert paths.session_runs_dir(session_id) == paths.session_dir(session_id) / "runs"
    assert paths.run_attachments_dir(session_id, run_id) == paths.run_dir(session_id, run_id) / "attachments"


def test_resolve_under_root_rejects_escape() -> None:
    root = PurePosixPath("/home/daytona/fleet")
    with pytest.raises(UnsafePathError):
        resolve_under_root(root, "..")
    with pytest.raises(UnsafePathError):
        resolve_under_root(root, "sessions", "../x")
    ok = resolve_under_root(root, "sessions", str(uuid4()))
    assert str(ok).startswith("/home/daytona/fleet/sessions/")


@pytest.mark.asyncio
async def test_get_or_create_volume_id_uses_injected_client() -> None:
    class _Vol:
        id = "vid-1"

    class _Client:
        def __init__(self) -> None:
            self.calls: list[tuple[str, bool]] = []

        async def get(self, name: str, *, create: bool = False) -> _Vol:
            self.calls.append((name, create))
            return _Vol()

    client = _Client()
    vid = await get_or_create_volume_id(client, VolumeConfig(name="my-vol"))
    assert vid == "vid-1"
    assert client.calls == [("my-vol", True)]


@pytest.mark.asyncio
async def test_get_or_create_volume_id_recovers_concurrent_create_conflict() -> None:
    from daytona.common.errors import DaytonaConflictError

    class _Client:
        def __init__(self) -> None:
            self.calls: list[bool] = []

        async def get(self, name: str, *, create: bool = False) -> object:
            assert name == "my-vol"
            self.calls.append(create)
            if create:
                raise DaytonaConflictError("Volume already exists", status_code=409)
            return type("Volume", (), {"id": "vid-1"})()

    client = _Client()
    assert await get_or_create_volume_id(client, VolumeConfig(name="my-vol")) == "vid-1"
    assert client.calls == [True, False]


class _FakeLayoutInfo:
    def __init__(self, is_dir: bool = True) -> None:
        self.is_dir = is_dir


class _FakeLayoutFs:
    def __init__(self, existing: Iterable[str] = (), *, fail_once: dict[str, int] | None = None) -> None:
        self._existing: set[str] = set(existing)
        self._fail_once: dict[str, int] = dict(fail_once or {})
        self.calls: list[tuple[str, str]] = []
        self.peak_concurrency = 0
        self._active = 0
        self._lock = asyncio.Lock()

    async def _touch(self, op: str, path: str) -> None:
        async with self._lock:
            self._active += 1
            self.peak_concurrency = max(self.peak_concurrency, self._active)
            self.calls.append((op, path))
        try:
            await asyncio.sleep(0)
        finally:
            async with self._lock:
                self._active -= 1

    async def get_file_info(self, path: str) -> _FakeLayoutInfo | None:
        await self._touch("stat", path)
        return _FakeLayoutInfo() if path in self._existing else None

    async def create_folder(self, path: str, _mode: str) -> None:
        await self._touch("mkdir", path)
        remaining = self._fail_once.get(path, 0)
        if remaining:
            self._fail_once[path] = remaining - 1
            raise RuntimeError("transient create failure")
        self._existing.add(path)


def _dirs_of(fs: _FakeLayoutFs, op: str) -> list[str]:
    return [path for name, path in fs.calls if name == op]


def _sandbox(fs: _FakeLayoutFs):
    return SimpleNamespace(fs=fs)


@pytest.mark.asyncio
async def test_execution_mount_must_be_visible_to_python() -> None:
    process = SimpleNamespace(exec=AsyncMock(return_value=SimpleNamespace(exit_code=2)))
    with pytest.raises(DaytonaAdapterError) as error:
        await verify_execution_mount(SimpleNamespace(process=process))
    assert error.value.cause_type == "ExecutionMountNotVisible"
    process.exec.assert_awaited_once()
    assert SESSION_WORKSPACE_MOUNT_PATH in process.exec.await_args.args[0]

    process.exec.return_value = SimpleNamespace(exit_code=0)
    await verify_execution_mount(SimpleNamespace(process=process))


@pytest.mark.asyncio
async def test_layout_creates_every_directory_and_verifies_mount() -> None:
    fs = _FakeLayoutFs(existing={"/home/daytona/fleet"})
    paths = VolumePaths.from_mount("/home/daytona/fleet")
    session_id, run_id = uuid4(), uuid4()

    await ensure_volume_layout(_sandbox(fs), paths, session_id=session_id, run_id=run_id)

    for directory in required_volume_directories(paths, session_id=session_id, run_id=run_id):
        assert directory in fs._existing, f"missing directory: {directory}"


@pytest.mark.asyncio
async def test_layout_runs_shared_roots_concurrently() -> None:
    fs = _FakeLayoutFs(existing={"/home/daytona/fleet"})
    paths = VolumePaths.from_mount("/home/daytona/fleet")

    await ensure_volume_layout(_sandbox(fs), paths, session_id=uuid4(), run_id=uuid4())

    assert fs.peak_concurrency >= 2, "shared roots were created serially"


@pytest.mark.asyncio
async def test_layout_creates_parents_before_children() -> None:
    fs = _FakeLayoutFs(existing={"/home/daytona/fleet"})
    paths = VolumePaths.from_mount("/home/daytona/fleet")
    session_id, run_id = uuid4(), uuid4()

    await ensure_volume_layout(_sandbox(fs), paths, session_id=session_id, run_id=run_id)

    order = {path: index for index, (name, path) in enumerate(fs.calls) if name == "mkdir"}
    session_dir = str(paths.session_dir(session_id))
    runs_dir = str(paths.session_runs_dir(session_id))
    run_dir = str(paths.run_dir(session_id, run_id))
    assert order[session_dir] < order[runs_dir], "session dir must be created before its runs container"
    assert order[session_dir] < order[str(paths.session_workspace_dir(session_id))]
    assert order[runs_dir] < order[run_dir], "session runs container must precede the run directory"
    assert order[run_dir] < order[str(paths.run_artifacts_dir(session_id, run_id))]
    assert order[run_dir] < order[str(paths.run_attachments_dir(session_id, run_id))]


@pytest.mark.asyncio
async def test_layout_tolerates_concurrent_creation_by_another_writer() -> None:
    artifact_root = "/home/daytona/fleet/artifacts"
    fs = _FakeLayoutFs(existing={"/home/daytona/fleet"}, fail_once={artifact_root: 1})

    real_create_folder = fs.create_folder

    async def racing_create_folder(path: str, mode: str) -> None:
        try:
            await real_create_folder(path, mode)
        except RuntimeError:
            fs._existing.add(path)
            raise

    fs.create_folder = racing_create_folder  # type: ignore[method-assign]

    paths = VolumePaths.from_mount("/home/daytona/fleet")
    await ensure_volume_layout(_sandbox(fs), paths, session_id=uuid4(), run_id=uuid4())

    assert artifact_root in fs._existing
    assert _dirs_of(fs, "mkdir").count(artifact_root) == 1
    assert _dirs_of(fs, "stat").count(artifact_root) == 1


@pytest.mark.asyncio
async def test_missing_mount_raises_without_creating() -> None:
    fs = _FakeLayoutFs(existing=set())
    paths = VolumePaths.from_mount("/home/daytona/fleet")
    with pytest.raises(Exception, match=r"[Uu]navailable"):
        await ensure_volume_layout(_sandbox(fs), paths, session_id=uuid4(), run_id=uuid4())
    assert not _dirs_of(fs, "mkdir"), "no directories may be created when the mount is missing"


# ---------------------------------------------------------------------------
# Sibling Volume Preservation Contracts
# ---------------------------------------------------------------------------

_RECURSIVE_MOUNT = "/workspace"


@dataclass
class _PreservedVolumeFs:
    files: dict[str, bytes]
    deleted: list[str] = field(default_factory=list)
    directories: set[str] = field(default_factory=set)

    async def list_files(self, _root: str, *, depth: int | None) -> list[SimpleNamespace]:
        assert depth is None
        return [
            *[SimpleNamespace(path=path, is_dir=False) for path in sorted(self.files)],
            *[SimpleNamespace(path=path, is_dir=True) for path in sorted(self.directories)],
        ]

    async def create_folder(self, path: str, _mode: str) -> None:
        self.directories.add(path)

    async def delete_file(self, path: str, *, recursive: bool = False) -> None:
        if recursive:
            for candidate in list(self.files):
                if candidate == path or candidate.startswith(path + "/"):
                    del self.files[candidate]
            for candidate in list(self.directories):
                if candidate == path or candidate.startswith(path + "/"):
                    self.directories.discard(candidate)
        else:
            self.files.pop(path, None)
            self.directories.discard(path)
        self.deleted.append(path)


def _checksum_manifest(fs: _PreservedVolumeFs) -> dict[str, str]:
    return {path: hashlib.sha256(content).hexdigest() for path, content in sorted(fs.files.items())}


@dataclass
class _PreservedSandbox:
    id: str
    fs: _PreservedVolumeFs


class _PreservedMultiSandboxPlatform:
    def __init__(self, sandboxes: list[_PreservedSandbox]) -> None:
        self._queue = list(sandboxes)
        self.create_calls: list[dict[str, object]] = []
        self.deleted: list[str] = []

    async def create(self, **kwargs: object) -> _PreservedSandbox:
        self.create_calls.append(kwargs)
        return self._queue.pop(0)

    async def delete(self, sandbox_id: str) -> None:
        self.deleted.append(sandbox_id)

    async def get(self, _sandbox_id: str) -> None:
        return None


class _PreservedRecordingInterpreter:
    def __init__(self, *, fail_shutdown: bool = False) -> None:
        self._fail_shutdown = fail_shutdown
        self.shutdown_calls: list[bool] = []

    def bind_run_scratch(self, _run_id: object, *, call_index: int | None = None) -> None:
        del call_index

    def shutdown(self, *, strict_broker_cleanup: bool = False) -> None:
        self.shutdown_calls.append(strict_broker_cleanup)
        if self._fail_shutdown:
            raise RuntimeError("broker cleanup failed")


class _PreservedInterpreterBox:
    def __init__(self) -> None:
        self.fail_shutdown = False
        self.instances: list[_PreservedRecordingInterpreter] = []


@pytest.fixture
def interpreter_box() -> _PreservedInterpreterBox:
    return _PreservedInterpreterBox()


def _preserved_factory(
    monkeypatch: pytest.MonkeyPatch,
    platform: _PreservedMultiSandboxPlatform,
    admission: DaytonaAdmission,
    workspace_id: object,
    run_id: object,
    box: _PreservedInterpreterBox,
    *,
    is_authorized: object = None,
) -> object:
    def interpreter_factory(**_kwargs: object) -> _PreservedRecordingInterpreter:
        interpreter = _PreservedRecordingInterpreter(fail_shutdown=box.fail_shutdown)
        box.instances.append(interpreter)
        return interpreter

    monkeypatch.setattr(recursive_child_runtime, "DaytonaCodeInterpreter", interpreter_factory)
    monkeypatch.setattr(recursive_child_runtime, "sandbox_backend", lambda sandbox, **_kwargs: sandbox)
    return make_daytona_child_factory(
        loop=asyncio.get_running_loop(),
        platform=platform,
        admission=admission,
        volume_id="shared-volume",
        workspace_id=workspace_id,
        session_id=uuid4(),
        run_id=run_id,
        deadline=asyncio.get_running_loop().time() + 30,
        execution_output_cap=1000,
        is_authorized=is_authorized,
    )


@pytest.mark.asyncio
async def test_close_child_a_preserves_root_and_sibling_volume_byte_for_byte(
    monkeypatch: pytest.MonkeyPatch, interpreter_box: _PreservedInterpreterBox
) -> None:
    workspace_id = uuid4()
    run_id = uuid4()
    a_scope = f"/tmp/fleet/child-data/{run_id}/1"
    b_scope = f"/tmp/fleet/child-data/{run_id}/2"

    root_fs = _PreservedVolumeFs(
        files={
            f"{_RECURSIVE_MOUNT}/workspaces/{workspace_id}/root-marker.txt": b"root-content-1",
            f"{_RECURSIVE_MOUNT}/workspaces/{workspace_id}/projects/report.md": b"root-project-bytes",
        }
    )
    child_a_fs = _PreservedVolumeFs(
        files={
            f"{a_scope}/a-top.txt": b"child-a-top",
            f"{a_scope}/a-nested/deep/file.txt": b"child-a-deep",
        },
        directories={f"{a_scope}/a-nested", f"{a_scope}/a-nested/deep"},
    )
    child_b_fs = _PreservedVolumeFs(files={f"{b_scope}/b-marker.txt": b"child-b-bytes"})

    child_a = _PreservedSandbox("child-a-sandbox", child_a_fs)
    child_b = _PreservedSandbox("child-b-sandbox", child_b_fs)
    platform = _PreservedMultiSandboxPlatform([child_a, child_b])
    admission = DaytonaAdmission(max_active_leases=3)
    factory = _preserved_factory(monkeypatch, platform, admission, workspace_id, run_id, interpreter_box)

    lease_a = await asyncio.to_thread(factory, 1)
    lease_b = await asyncio.to_thread(factory, 2)

    assert (lease_a.volume_id, lease_b.volume_id) == ("shared-volume", "shared-volume")
    assert lease_a.volume_subpath == lease_b.volume_subpath
    assert lease_a.volume_subpath.startswith(f"workspaces/{workspace_id}/sessions/")
    assert lease_a.volume_subpath.endswith("/workspace")
    for call in platform.create_calls:
        assert call["volume_id"] == "shared-volume"
        assert call["mount_path"] == _RECURSIVE_MOUNT
        assert call["ephemeral"] is True
        assert call["labels"] == {"fleet.runtime": "recursive-child"}

    root_manifest_before = _checksum_manifest(root_fs)
    sibling_manifest_before = _checksum_manifest(child_b_fs)

    await asyncio.to_thread(lease_a.close)

    assert lease_a.state is ChildRuntimeLeaseState.CLOSED
    assert child_a_fs.files == {}
    assert child_a_fs.directories == {
        "/tmp/fleet",
        "/tmp/fleet/child-data",
        f"/tmp/fleet/child-data/{run_id}",
    }
    assert child_a_fs.deleted == [
        f"{a_scope}/a-nested/deep/file.txt",
        f"{a_scope}/a-top.txt",
        f"{a_scope}/a-nested/deep",
        f"{a_scope}/a-nested",
        a_scope,
    ]
    assert platform.deleted == ["child-a-sandbox"]
    assert interpreter_box.instances and interpreter_box.instances[0].shutdown_calls == [True]
    permit = await admission.acquire(deadline=asyncio.get_running_loop().time() + 1)
    permit.release()

    assert _checksum_manifest(root_fs) == root_manifest_before
    assert _checksum_manifest(child_b_fs) == sibling_manifest_before
    assert root_fs.deleted == []
    assert child_b_fs.deleted == []

    await asyncio.to_thread(lease_b.close)
    assert lease_b.state is ChildRuntimeLeaseState.CLOSED
    assert _checksum_manifest(root_fs) == root_manifest_before


@pytest.mark.asyncio
async def test_child_failure_cleanup_still_preserves_root_and_sibling_volume(
    monkeypatch: pytest.MonkeyPatch, interpreter_box: _PreservedInterpreterBox
) -> None:
    workspace_id = uuid4()
    run_id = uuid4()
    a_scope = f"/tmp/fleet/child-data/{run_id}/1"
    b_scope = f"/tmp/fleet/child-data/{run_id}/2"

    root_fs = _PreservedVolumeFs(
        files={f"{_RECURSIVE_MOUNT}/workspaces/{workspace_id}/root-marker.txt": b"root-content-fail"}
    )
    child_a_fs = _PreservedVolumeFs(
        files={f"{a_scope}/a-top.txt": b"child-a-fail-scope"},
        directories=set(),
    )
    child_b_fs = _PreservedVolumeFs(files={f"{b_scope}/b-marker.txt": b"child-b-fail-bytes"})

    platform = _PreservedMultiSandboxPlatform(
        [
            _PreservedSandbox("child-a-sandbox", child_a_fs),
            _PreservedSandbox("child-b-sandbox", child_b_fs),
        ]
    )
    admission = DaytonaAdmission(max_active_leases=3)
    interpreter_box.fail_shutdown = True
    factory = _preserved_factory(monkeypatch, platform, admission, workspace_id, run_id, interpreter_box)

    lease_a = await asyncio.to_thread(factory, 1)
    interpreter_box.fail_shutdown = False
    lease_b = await asyncio.to_thread(factory, 2)

    root_manifest_before = _checksum_manifest(root_fs)
    sibling_manifest_before = _checksum_manifest(child_b_fs)

    with pytest.raises(recursive_child_runtime.ChildRuntimeCleanupError, match="recursive child cleanup failed"):
        await asyncio.to_thread(lease_a.close)
    assert lease_a.state is ChildRuntimeLeaseState.FAILED
    with pytest.raises(recursive_child_runtime.ChildRuntimeCleanupError):
        await asyncio.to_thread(lease_a.close)
    assert interpreter_box.instances[0].shutdown_calls == [True]

    assert child_a_fs.files == {}
    assert child_a_fs.deleted == [f"{a_scope}/a-top.txt", a_scope]
    assert platform.deleted == ["child-a-sandbox"]
    permit = await admission.acquire(deadline=asyncio.get_running_loop().time() + 1)
    permit.release()

    assert _checksum_manifest(root_fs) == root_manifest_before
    assert _checksum_manifest(child_b_fs) == sibling_manifest_before
    assert root_fs.deleted == []
    assert child_b_fs.deleted == []

    await asyncio.to_thread(lease_b.close)
    assert lease_b.state is ChildRuntimeLeaseState.CLOSED


@pytest.mark.asyncio
async def test_cancellation_preserves_volume_state_and_allocates_nothing_further(
    monkeypatch: pytest.MonkeyPatch, interpreter_box: _PreservedInterpreterBox
) -> None:
    workspace_id = uuid4()
    run_id = uuid4()
    a_scope = f"/tmp/fleet/child-data/{run_id}/1"
    b_scope = f"/tmp/fleet/child-data/{run_id}/2"

    root_fs = _PreservedVolumeFs(
        files={f"{_RECURSIVE_MOUNT}/workspaces/{workspace_id}/root-marker.txt": b"root-content-cancel"}
    )
    child_a_fs = _PreservedVolumeFs(files={f"{a_scope}/a-top.txt": b"child-a-cancel-scope"})
    sibling_fs = _PreservedVolumeFs(files={f"{b_scope}/b-marker.txt": b"child-b-cancel-bytes"})

    platform = _PreservedMultiSandboxPlatform(
        [
            _PreservedSandbox("child-a-sandbox", child_a_fs),
            _PreservedSandbox("child-b-sandbox", sibling_fs),
        ]
    )
    admission = DaytonaAdmission(max_active_leases=3)
    authorized = True
    factory = _preserved_factory(
        monkeypatch,
        platform,
        admission,
        workspace_id,
        run_id,
        interpreter_box,
        is_authorized=lambda: authorized,
    )

    lease_a = await asyncio.to_thread(factory, 1)
    root_manifest_before = _checksum_manifest(root_fs)
    sibling_manifest_before = _checksum_manifest(sibling_fs)

    authorized = False
    with pytest.raises(recursive_child_runtime.ChildRuntimeAuthorizationError, match="no longer authorized"):
        await asyncio.to_thread(factory, 2)
    assert len(platform.create_calls) == 1
    assert sibling_fs.deleted == []

    await asyncio.to_thread(lease_a.close)
    assert lease_a.state is ChildRuntimeLeaseState.CLOSED
    assert child_a_fs.files == {}
    assert platform.deleted == ["child-a-sandbox"]
    permit = await admission.acquire(deadline=asyncio.get_running_loop().time() + 1)
    permit.release()

    assert _checksum_manifest(root_fs) == root_manifest_before
    assert _checksum_manifest(sibling_fs) == sibling_manifest_before
    assert root_fs.deleted == []
