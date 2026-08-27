"""impl-08: DaytonaSessionManager lifecycle and leases (fake platform, no network)."""

from __future__ import annotations

import asyncio
import threading
from typing import Any
from uuid import UUID, uuid4

import pytest

from fleet_rlm.daytona.errors import DaytonaAdapterError, ProviderRequestError
from fleet_rlm.daytona.provisioning import DaytonaSandboxSpec, VolumeConfig
from fleet_rlm.daytona.session_manager import (
    ActiveLeaseConflictError,
    ActiveLeaseRegistry,
    DaytonaAdmission,
    DaytonaSessionManager,
    LeaseRequest,
)
from fleet_rlm.runtime.bindings import InMemorySandboxBindingStore as InMemoryBindingStore
from fleet_rlm.runtime.bindings import SandboxBinding

_SPEC = DaytonaSandboxSpec("fleet-test-v1")


def test_active_lease_registry_is_scoped_by_workspace_and_session() -> None:
    registry = ActiveLeaseRegistry()
    session_id = uuid4()
    workspace_a = uuid4()
    workspace_b = uuid4()
    run_a = uuid4()
    run_b = uuid4()

    registry.acquire(session_id, run_a, workspace_id=workspace_a)
    registry.acquire(session_id, run_b, workspace_id=workspace_b)

    assert registry.holder(session_id, workspace_id=workspace_a) == run_a
    assert registry.holder(session_id, workspace_id=workspace_b) == run_b
    with pytest.raises(ActiveLeaseConflictError):
        registry.acquire(session_id, uuid4(), workspace_id=workspace_a)

    registry.release(session_id, run_a, workspace_id=workspace_a)
    assert registry.holder(session_id, workspace_id=workspace_a) is None
    assert registry.holder(session_id, workspace_id=workspace_b) == run_b
    registry.release(session_id, run_b, workspace_id=workspace_b)


class _FakeVolume:
    def __init__(self, volume_id: str = "vol-1") -> None:
        self.id = volume_id


class _FakeVolumeClient:
    def __init__(self) -> None:
        self.gets: list[tuple[str, bool]] = []
        self.failures: list[BaseException] = []

    async def get(self, name: str, *, create: bool = False) -> _FakeVolume:
        self.gets.append((name, create))
        if self.failures:
            raise self.failures.pop(0)
        return _FakeVolume(f"vol-{name}")


class _FakeFileInfo:
    def __init__(self, *, is_dir: bool) -> None:
        self.is_dir = is_dir


class _FakeFilesystem:
    def __init__(self, mount_path: str | None) -> None:
        self.directories = {mount_path} if mount_path else set()
        self.files: set[str] = set()
        self.uploaded: dict[str, bytes] = {}
        self.created: list[tuple[str, str]] = []
        self.info_failures: dict[str, BaseException] = {}

    async def get_file_info(self, path: str) -> _FakeFileInfo:
        failure = self.info_failures.pop(path, None)
        if failure is not None:
            raise failure
        if path in self.directories:
            return _FakeFileInfo(is_dir=True)
        if path in self.files:
            return _FakeFileInfo(is_dir=False)
        raise FileNotFoundError(path)

    async def create_folder(self, path: str, mode: str) -> None:
        self.directories.add(path)
        self.created.append((path, mode))

    async def upload_file(self, data: bytes, path: str) -> None:
        self.files.add(path)
        self.uploaded[path] = bytes(data)


class _FakeSandbox:
    def __init__(
        self,
        sandbox_id: str,
        state: str = "running",
        *,
        volume_id: str | None = None,
        mount_path: str | None = None,
        volume_subpath: str | None = None,
        labels: dict[str, str] | None = None,
        snapshot: str = _SPEC.snapshot,
    ) -> None:
        self.id = sandbox_id
        self.state = state
        self.ops: list[str] = []
        self.backend = None  # interpreter will tolerate missing backend until execute
        self.volume_id = volume_id
        self.mount_path = mount_path
        self.volume_subpath = volume_subpath
        self.labels = labels or {}
        self.snapshot = snapshot
        self.fs = _FakeFilesystem(mount_path)
        self.volumes = (
            [
                {
                    "volume_id": volume_id,
                    "mount_path": mount_path,
                    "subpath": volume_subpath,
                }
            ]
            if volume_id and mount_path and volume_subpath
            else []
        )

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
        if not volume_subpath:
            raise ValueError("VolumeMount without workspace subpath is rejected")
        self._n += 1
        sid = f"sb-{self._n}"
        sb = _FakeSandbox(
            sid,
            state="running",
            volume_id=volume_id,
            mount_path=mount_path,
            volume_subpath=volume_subpath,
            labels=labels or {},
        )
        self.sandboxes[sid] = sb
        self.created.append(
            {
                "volume_id": volume_id,
                "mount_path": mount_path,
                "volume_subpath": volume_subpath,
                "labels": labels or {},
                "id": sid,
            }
        )
        return sb

    async def delete(self, sandbox_id: str) -> None:
        self.deleted.append(sandbox_id)
        self.sandboxes.pop(sandbox_id, None)

    async def start(self, sandbox_id: str) -> None:
        self.sandboxes[sandbox_id].start()

    async def stop(self, sandbox_id: str, *, timeout: float = 60, force: bool = False) -> None:
        del timeout, force
        self.sandboxes[sandbox_id].stop()


class _FailingStopPlatform(_FakePlatform):
    async def stop(self, sandbox_id: str, *, timeout: float = 60, force: bool = False) -> None:
        del sandbox_id, timeout, force
        raise RuntimeError("provider stop failed")


class _CountingBackend:
    def __init__(self) -> None:
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1


class _BlockingCreatePlatform(_FakePlatform):
    def __init__(self, expected_entries: int) -> None:
        super().__init__()
        self.expected_entries = expected_entries
        self.entered = 0
        self.entered_lock = threading.Lock()
        self.all_entered = threading.Event()
        self.release_creates = threading.Event()
        self.backends: list[_CountingBackend] = []

    async def create(self, **kwargs: Any) -> _FakeSandbox:
        sandbox = await super().create(**kwargs)
        backend = _CountingBackend()
        sandbox.backend = backend
        self.backends.append(backend)
        with self.entered_lock:
            self.entered += 1
            if self.entered >= self.expected_entries:
                self.all_entered.set()
        if not await asyncio.to_thread(self.release_creates.wait, 5):
            raise TimeoutError("test provider create gate timed out")
        return sandbox


class _FailingLayoutPlatform(_FakePlatform):
    def __init__(self) -> None:
        super().__init__()
        self.fail_layout = True

    async def create(self, **kwargs: Any) -> _FakeSandbox:
        sandbox = await super().create(**kwargs)
        if self.fail_layout:
            sandbox.fs.info_failures["/home/daytona/fleet/artifacts"] = RuntimeError(
                "provider failed at /home/daytona/private"
            )
        return sandbox


class _RacingFilesystem(_FakeFilesystem):
    def __init__(self, mount_path: str) -> None:
        super().__init__(mount_path)
        self._artifacts_barrier = threading.Barrier(2)
        self._artifacts_lock = threading.Lock()

    async def create_folder(self, path: str, mode: str) -> None:
        if path != "/home/daytona/fleet/artifacts":
            return await super().create_folder(path, mode)
        await asyncio.to_thread(self._artifacts_barrier.wait, 5)
        with self._artifacts_lock:
            if path in self.directories:
                raise FileExistsError(path)
            await super().create_folder(path, mode)


class _SharedFilesystemPlatform(_FakePlatform):
    def __init__(self) -> None:
        super().__init__()
        self.filesystem = _RacingFilesystem("/home/daytona/fleet")

    async def create(self, **kwargs: Any) -> _FakeSandbox:
        sandbox = await super().create(**kwargs)
        sandbox.fs = self.filesystem
        return sandbox


def _manager(
    platform: _FakePlatform | None = None,
    bindings: InMemoryBindingStore | None = None,
    admission: DaytonaAdmission | None = None,
    idle_stop_seconds: float | None = None,
) -> tuple[DaytonaSessionManager, _FakePlatform, InMemoryBindingStore, _FakeVolumeClient]:
    plat = platform or _FakePlatform()
    store = bindings or InMemoryBindingStore()
    volumes = _FakeVolumeClient()
    mgr = DaytonaSessionManager(
        platform=plat,
        volume_client=volumes,
        volume_config=VolumeConfig(),
        bindings=store,
        admission=admission,
        sandbox_spec=_SPEC,
        idle_stop_seconds=idle_stop_seconds,
    )
    return mgr, plat, store, volumes


def _request() -> LeaseRequest:
    return LeaseRequest(session_id=uuid4(), user_id=uuid4(), workspace_id=uuid4())


async def _acquire(mgr: DaytonaSessionManager, request: LeaseRequest):
    return await mgr.acquire(request, deadline=asyncio.get_running_loop().time() + 10)


@pytest.mark.asyncio
async def test_acquire_creates_running_sandbox_and_lease() -> None:
    mgr, plat, store, volumes = _manager()
    req = _request()
    lease = await _acquire(mgr, req)

    assert lease.sandbox_id in plat.sandboxes
    assert lease.volume_id.startswith("vol-")
    assert lease.mount_path == "/home/daytona/fleet"
    assert lease.volume_subpath == f"workspaces/{req.workspace_id}"
    assert volumes.gets  # volume resolved
    assert plat.created[0]["volume_subpath"] == lease.volume_subpath
    binding = await store.get(req.session_id)
    assert binding is not None
    assert binding.provider_state == "running"
    assert binding.sandbox_id == lease.sandbox_id
    assert binding.workspace_id == req.workspace_id
    assert binding.volume_subpath == lease.volume_subpath


@pytest.mark.asyncio
async def test_acquire_provisions_complete_workspace_volume_layout() -> None:
    mgr, plat, _store, _volumes = _manager()
    session_id = uuid4()
    run_id = uuid4()
    request = LeaseRequest(
        session_id=session_id,
        user_id=uuid4(),
        workspace_id=uuid4(),
        run_id=run_id,
    )

    lease = await _acquire(mgr, request)

    sandbox = plat.sandboxes[lease.sandbox_id]
    root = "/home/daytona/fleet"
    session = f"{root}/sessions/{session_id}"
    run = f"{session}/runs/{run_id}"
    expected_layout = {
        root,
        f"{root}/artifacts",
        f"{root}/attachments",
        f"{root}/sessions",
        session,
        f"{session}/workspace",
        f"{session}/runs",
        run,
        f"{run}/artifacts",
        f"{run}/attachments",
    }
    assert expected_layout <= sandbox.fs.directories
    assert {path for path, _mode in sandbox.fs.created} == sandbox.fs.directories - {root}
    assert {mode for _path, mode in sandbox.fs.created} == {"700"}
    created_paths = [path for path, _mode in sandbox.fs.created]
    assert created_paths.index(session) < created_paths.index(f"{session}/workspace")
    assert created_paths.index(run) < created_paths.index(f"{run}/attachments")
    assert sandbox.fs.uploaded == {}


@pytest.mark.asyncio
async def test_reacquire_repairs_missing_containers_without_touching_files() -> None:
    mgr, plat, _store, _volumes = _manager()
    session_id = uuid4()
    workspace_id = uuid4()
    first = await _acquire(
        mgr,
        LeaseRequest(
            session_id=session_id,
            user_id=uuid4(),
            workspace_id=workspace_id,
            run_id=uuid4(),
        ),
    )
    await mgr.release(first)
    sandbox = plat.sandboxes[first.sandbox_id]
    workspace_path = f"/home/daytona/fleet/sessions/{session_id}/workspace"
    durable_file = f"{workspace_path}/decision.md"
    sandbox.fs.files.add(durable_file)

    second_run_id = uuid4()
    second = await _acquire(
        mgr,
        LeaseRequest(
            session_id=session_id,
            user_id=uuid4(),
            workspace_id=workspace_id,
            run_id=second_run_id,
        ),
    )

    assert second.sandbox_id == first.sandbox_id
    assert durable_file in sandbox.fs.files
    assert f"/home/daytona/fleet/sessions/{session_id}/runs/{second_run_id}/attachments" in (sandbox.fs.directories)


@pytest.mark.asyncio
async def test_reacquire_repairs_stopped_sandbox_after_restart() -> None:
    mgr, plat, _store, _volumes = _manager()
    request = LeaseRequest(
        session_id=uuid4(),
        user_id=uuid4(),
        workspace_id=uuid4(),
        run_id=uuid4(),
    )
    first = await _acquire(mgr, request)
    await mgr.release(first)
    sandbox = plat.sandboxes[first.sandbox_id]
    attachments_path = "/home/daytona/fleet/attachments"
    sandbox.fs.directories.remove(attachments_path)
    sandbox.stop()

    second = await _acquire(
        mgr,
        LeaseRequest(
            session_id=request.session_id,
            user_id=request.user_id,
            workspace_id=request.workspace_id,
            run_id=uuid4(),
        ),
    )

    assert second.sandbox_id == first.sandbox_id
    assert sandbox.state == "running"
    assert attachments_path in sandbox.fs.directories


@pytest.mark.asyncio
async def test_fenced_sandbox_is_quarantined_and_replaced_with_volume_scope_preserved() -> None:
    mgr, _plat, store, _volumes = _manager()
    request = LeaseRequest(
        session_id=uuid4(),
        user_id=uuid4(),
        workspace_id=uuid4(),
        run_id=uuid4(),
    )
    first = await _acquire(mgr, request)

    await mgr.fence_session(request.session_id)
    await mgr.release(first)
    quarantined = await store.get(request.session_id)
    assert quarantined is not None
    assert quarantined.provider_state == "quarantined"

    replacement = await _acquire(
        mgr,
        LeaseRequest(
            session_id=request.session_id,
            user_id=request.user_id,
            workspace_id=request.workspace_id,
            run_id=uuid4(),
        ),
    )
    assert replacement.sandbox_id != first.sandbox_id
    assert replacement.volume_id == first.volume_id
    assert replacement.volume_subpath == first.volume_subpath


@pytest.mark.asyncio
async def test_unconfirmed_fence_keeps_session_unavailable() -> None:
    mgr, _plat, store, _volumes = _manager(_FailingStopPlatform())
    request = LeaseRequest(
        session_id=uuid4(),
        user_id=uuid4(),
        workspace_id=uuid4(),
        run_id=uuid4(),
    )
    first = await _acquire(mgr, request)

    with pytest.raises(RuntimeError, match="provider stop failed"):
        await mgr.fence_session(request.session_id)
    await mgr.release(first)

    fencing = await store.get(request.session_id)
    assert fencing is not None
    assert fencing.provider_state == "fencing"
    with pytest.raises(DaytonaAdapterError) as exc_info:
        await _acquire(
            mgr,
            LeaseRequest(
                session_id=request.session_id,
                user_id=request.user_id,
                workspace_id=request.workspace_id,
                run_id=uuid4(),
            ),
        )
    assert exc_info.value.cause_type == "SandboxFenceUnconfirmed"


@pytest.mark.asyncio
async def test_acquire_fails_closed_when_required_directory_is_a_file() -> None:
    mgr, plat, store, _volumes = _manager()
    request = LeaseRequest(
        session_id=uuid4(),
        user_id=uuid4(),
        workspace_id=uuid4(),
        run_id=uuid4(),
    )
    first = await _acquire(mgr, request)
    await mgr.release(first)
    sandbox = plat.sandboxes[first.sandbox_id]
    artifacts_path = "/home/daytona/fleet/artifacts"
    sandbox.fs.directories.remove(artifacts_path)
    sandbox.fs.files.add(artifacts_path)

    with pytest.raises(DaytonaAdapterError) as captured:
        await _acquire(mgr, request)

    assert captured.value.cause_type == "VolumeLayoutConflict"
    binding = await store.get(request.session_id)
    assert binding is not None
    assert binding.sandbox_id == first.sandbox_id


@pytest.mark.asyncio
async def test_layout_provider_failure_prevents_lease_and_releases_admission() -> None:
    platform = _FailingLayoutPlatform()
    admission = DaytonaAdmission(max_active_leases=1)
    mgr, _plat, store, _volumes = _manager(platform=platform, admission=admission)
    failed_request = _request()

    with pytest.raises(ProviderRequestError) as captured:
        await _acquire(mgr, failed_request)

    assert "/home/daytona" not in str(captured.value)
    assert await store.get(failed_request.session_id) is None
    assert platform.deleted == ["sb-1"]
    assert platform.sandboxes == {}
    platform.fail_layout = False
    replacement = await _acquire(mgr, _request())
    await mgr.release(replacement)


@pytest.mark.asyncio
async def test_sibling_session_acquisitions_tolerate_shared_root_creation_race() -> None:
    platform = _SharedFilesystemPlatform()
    mgr, _plat, _store, _volumes = _manager(platform=platform)
    workspace_id = uuid4()
    requests = [
        LeaseRequest(
            session_id=uuid4(),
            user_id=uuid4(),
            workspace_id=workspace_id,
            run_id=uuid4(),
        )
        for _ in range(2)
    ]

    leases = await asyncio.gather(*(_acquire(mgr, request) for request in requests))

    assert "/home/daytona/fleet/artifacts" in platform.filesystem.directories
    for request in requests:
        assert f"/home/daytona/fleet/sessions/{request.session_id}/workspace" in (platform.filesystem.directories)
    for lease in leases:
        await mgr.release(lease)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "expected_calls"),
    [
        (ProviderRequestError("timeout", cause_type="TimeoutError"), 2),
        (ProviderRequestError("network", cause_type="ConnectionError"), 2),
        (ProviderRequestError("server", cause_type="ProviderError", status_code=503), 2),
        (ProviderRequestError("auth", cause_type="AuthError", status_code=401), 1),
        (ProviderRequestError("quota", cause_type="QuotaError", status_code=429), 1),
        (ProviderRequestError("validation", cause_type="ValidationError", status_code=422), 1),
        (ProviderRequestError("mount", cause_type="WorkspaceMountMismatch"), 1),
    ],
)
async def test_acquire_retries_only_safe_pre_creation_failures(
    failure: ProviderRequestError,
    expected_calls: int,
) -> None:
    mgr, plat, _store, volumes = _manager()
    volumes.failures = [failure]

    if expected_calls == 2:
        lease = await _acquire(mgr, _request())
        assert lease.sandbox_id in plat.sandboxes
    else:
        with pytest.raises(ProviderRequestError):
            await _acquire(mgr, _request())

    assert len(volumes.gets) == expected_calls


@pytest.mark.asyncio
async def test_acquire_never_retries_ambiguous_sandbox_creation_failure() -> None:
    class _FailingCreatePlatform(_FakePlatform):
        async def create(self, **kwargs: Any) -> _FakeSandbox:
            del kwargs
            self._n += 1
            raise ProviderRequestError("provider unavailable", cause_type="ProviderError", status_code=503)

    platform = _FailingCreatePlatform()
    mgr, _plat, _store, _volumes = _manager(platform=platform)

    with pytest.raises(ProviderRequestError):
        await _acquire(mgr, _request())

    assert platform._n == 1


@pytest.mark.asyncio
async def test_acquire_retries_a_transient_pre_creation_failure_at_most_once() -> None:
    mgr, _plat, _store, volumes = _manager()
    volumes.failures = [TimeoutError("one"), TimeoutError("two"), TimeoutError("three")]

    with pytest.raises(ProviderRequestError):
        await _acquire(mgr, _request())

    assert len(volumes.gets) == 2
    assert len(volumes.failures) == 1


@pytest.mark.asyncio
async def test_acquire_rejects_zero_workspace_id() -> None:
    mgr, _plat, _store, _volumes = _manager()
    with pytest.raises(ValueError, match="zero UUID"):
        await _acquire(mgr, LeaseRequest(session_id=uuid4(), user_id=uuid4(), workspace_id=UUID(int=0)))


@pytest.mark.asyncio
async def test_acquire_rejects_binding_from_another_workspace_without_overwrite() -> None:
    mgr, _plat, store, _volumes = _manager()
    first_request = _request()
    first = await _acquire(mgr, first_request)
    await mgr.release(first)
    binding_before = await store.get(first_request.session_id)
    assert binding_before is not None

    with pytest.raises(DaytonaAdapterError, match="workspace scope"):
        await mgr.acquire(
            LeaseRequest(
                session_id=first_request.session_id,
                user_id=first_request.user_id,
                workspace_id=uuid4(),
            ),
            deadline=asyncio.get_running_loop().time() + 2,
        )

    assert await store.get(first_request.session_id) == binding_before


@pytest.mark.asyncio
async def test_acquire_reuses_running_sandbox() -> None:
    mgr, plat, _store, _volumes = _manager()
    req = _request()
    first = await _acquire(mgr, req)
    await mgr.release(first)  # release active lease before re-acquire
    second = await _acquire(mgr, req)
    assert first.sandbox_id == second.sandbox_id
    assert len(plat.created) == 1


@pytest.mark.asyncio
async def test_acquire_replaces_sandbox_with_mismatched_snapshot() -> None:
    mgr, plat, _store, _volumes = _manager()
    req = _request()
    first = await _acquire(mgr, req)
    await mgr.release(first)
    plat.sandboxes[first.sandbox_id].snapshot = "fleet-test-v2"

    replacement = await _acquire(mgr, req)

    assert replacement.sandbox_id != first.sandbox_id
    assert replacement.volume_id == first.volume_id
    assert replacement.volume_subpath == first.volume_subpath


@pytest.mark.asyncio
async def test_release_never_deletes_sandbox() -> None:
    mgr, plat, _store, _volumes = _manager()
    req = _request()
    lease = await _acquire(mgr, req)
    sid = lease.sandbox_id
    await mgr.release(lease)
    await mgr.release(lease)  # idempotent
    assert sid in plat.sandboxes
    assert plat.deleted == []


@pytest.mark.asyncio
async def test_release_stops_retained_sandbox_after_explicit_idle_timeout() -> None:
    mgr, plat, store, _volumes = _manager(idle_stop_seconds=0.01)
    req = _request()
    lease = await _acquire(mgr, req)

    await mgr.release(lease)
    await asyncio.sleep(0.05)

    assert plat.sandboxes[lease.sandbox_id].state == "stopped"
    binding = await store.get(req.session_id)
    assert binding is not None
    assert binding.provider_state == "stopped"
    assert plat.deleted == []
    await mgr.aclose()


@pytest.mark.asyncio
async def test_replacement_deadline_retains_late_created_sandbox_cleanup() -> None:
    """A timed-out replacement owns a late-created Sandbox until deletion settles."""
    platform = _BlockingCreatePlatform(expected_entries=1)
    mgr, _platform, store, _volumes = _manager(platform=platform)
    session_id = uuid4()
    workspace_id = uuid4()
    binding = SandboxBinding(
        session_id=session_id,
        sandbox_id="old-sandbox",
        workspace_id=workspace_id,
        volume_id="volume-1",
        volume_subpath=f"workspaces/{workspace_id}",
        mount_path="/home/daytona/fleet",
        provider_state="unrecoverable",
    )
    await store.upsert(binding)
    replacement = asyncio.create_task(
        mgr.replace(
            binding,
            user_id=uuid4(),
            deadline=asyncio.get_running_loop().time() + 0.05,
        )
    )
    assert await asyncio.to_thread(platform.all_entered.wait, 2)
    with pytest.raises(RuntimeError, match="timed out"):
        await asyncio.wait_for(replacement, timeout=0.2)
    assert any(not task.done() for task in mgr._provider_tasks)

    platform.release_creates.set()
    await asyncio.gather(*tuple(mgr._provider_tasks), return_exceptions=True)
    assert "old-sandbox" in platform.deleted
    assert "sb-1" in platform.deleted
    await mgr.aclose()


@pytest.mark.asyncio
async def test_fence_session_deadline_detaches_hanging_binding_lookup() -> None:
    """A bounded fence must return while its provider lookup remains owned."""
    mgr, _platform, store, _volumes = _manager()
    release_lookup = asyncio.Event()

    async def hanging_scoped(_session_id: UUID, *, workspace_id: UUID) -> None:
        del workspace_id
        await release_lookup.wait()
        return None

    store.get_scoped = hanging_scoped  # type: ignore[method-assign]
    request = _request()
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(
            mgr.fence_session(
                request.session_id,
                workspace_id=request.workspace_id,
                deadline=asyncio.get_running_loop().time() + 0.05,
            ),
            timeout=0.2,
        )
    assert any(not task.done() for task in mgr._provider_tasks)

    release_lookup.set()
    await asyncio.gather(*tuple(mgr._provider_tasks), return_exceptions=True)
    await mgr.aclose()


@pytest.mark.asyncio
async def test_acquisition_error_restores_admission_capacity() -> None:
    admission = DaytonaAdmission(max_active_leases=1)
    mgr, plat, _store, volumes = _manager(admission=admission)
    request = _request()
    volumes.failures = [ProviderRequestError("quota", cause_type="QuotaError", status_code=429)]

    with pytest.raises(ProviderRequestError):
        await mgr.acquire(request, deadline=asyncio.get_running_loop().time() + 10)

    lease = await mgr.acquire(request, deadline=asyncio.get_running_loop().time() + 10)
    await mgr.release(lease)
    assert lease.sandbox_id in plat.sandboxes


@pytest.mark.asyncio
async def test_eight_provider_acquisitions_hold_admission_and_ninth_waits() -> None:
    platform = _BlockingCreatePlatform(expected_entries=8)
    admission = DaytonaAdmission(max_active_leases=8)
    mgr, _plat, _store, _volumes = _manager(platform=platform, admission=admission)
    deadline = asyncio.get_running_loop().time() + 10
    acquisitions = [asyncio.create_task(mgr.acquire(_request(), deadline=deadline)) for _ in range(8)]
    assert await asyncio.to_thread(platform.all_entered.wait, 2)

    ninth = asyncio.create_task(mgr.acquire(_request(), deadline=deadline))
    await asyncio.sleep(0)
    assert platform.entered == 8
    assert not ninth.done()

    platform.release_creates.set()
    leases = list(await asyncio.gather(*acquisitions))
    await asyncio.sleep(0)
    assert platform.entered == 8
    assert not ninth.done()

    await mgr.release(leases.pop())
    ninth_lease = await asyncio.wait_for(ninth, timeout=2)
    assert platform.entered == 9
    await mgr.release(ninth_lease)
    for lease in leases:
        await mgr.release(lease)


@pytest.mark.asyncio
async def test_cancellation_during_provider_create_transfers_owned_cleanup() -> None:
    platform = _BlockingCreatePlatform(expected_entries=1)
    admission = DaytonaAdmission(max_active_leases=1)
    mgr, _plat, _store, _volumes = _manager(platform=platform, admission=admission)
    deadline = asyncio.get_running_loop().time() + 10
    cancelled_request = _request()
    cancelled = asyncio.create_task(mgr.acquire(cancelled_request, deadline=deadline))
    assert await asyncio.to_thread(platform.all_entered.wait, 2)

    cancelled.cancel()
    replacement = asyncio.create_task(mgr.acquire(_request(), deadline=deadline))
    await asyncio.sleep(0)
    with pytest.raises(asyncio.CancelledError):
        await cancelled
    assert not replacement.done()
    assert platform.entered == 1

    platform.release_creates.set()
    replacement_lease = await asyncio.wait_for(replacement, timeout=2)
    await mgr._cleanup.shutdown(drain_seconds=2)

    from fleet_rlm.daytona.session_manager import get_active_lease_registry

    assert get_active_lease_registry().holder(cancelled_request.session_id) is None
    assert platform.backends[0].close_calls == 1
    assert platform.deleted == ["sb-1"]
    await mgr.release(replacement_lease)


@pytest.mark.asyncio
async def test_provider_acquisition_deadline_returns_before_late_owned_cleanup() -> None:
    from fleet_rlm.daytona.session_manager import DaytonaLeaseAcquisitionTimeoutError, get_active_lease_registry

    platform = _BlockingCreatePlatform(expected_entries=1)
    admission = DaytonaAdmission(max_active_leases=1)
    mgr, _plat, _store, _volumes = _manager(platform=platform, admission=admission)
    request = _request()
    acquisition = asyncio.create_task(mgr.acquire(request, deadline=asyncio.get_running_loop().time() + 0.05))
    assert await asyncio.to_thread(platform.all_entered.wait, 2)
    await asyncio.sleep(0.1)

    with pytest.raises(DaytonaLeaseAcquisitionTimeoutError):
        await acquisition
    assert get_active_lease_registry().holder(request.session_id) is not None

    platform.release_creates.set()
    await mgr._cleanup.shutdown(drain_seconds=2)
    assert get_active_lease_registry().holder(request.session_id) is None
    assert platform.backends[0].close_calls == 1

    replacement = await mgr.acquire(_request(), deadline=asyncio.get_running_loop().time() + 2)
    await mgr.release(replacement)


@pytest.mark.asyncio
async def test_cancelled_admission_wait_restores_session_claim() -> None:
    admission = DaytonaAdmission(max_active_leases=1)
    held = await admission.acquire(deadline=asyncio.get_running_loop().time() + 10)
    mgr, _plat, _store, _volumes = _manager(admission=admission)
    request = _request()
    waiter = asyncio.create_task(mgr.acquire(request, deadline=asyncio.get_running_loop().time() + 10))
    await asyncio.sleep(0)

    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    held.release()

    lease = await mgr.acquire(request, deadline=asyncio.get_running_loop().time() + 10)
    await mgr.release(lease)


@pytest.mark.asyncio
async def test_admission_timeout_restores_session_claim() -> None:
    from fleet_rlm.daytona.session_manager import DaytonaAdmissionTimeoutError

    admission = DaytonaAdmission(max_active_leases=1)
    held = await admission.acquire(deadline=asyncio.get_running_loop().time() + 10)
    mgr, _plat, _store, _volumes = _manager(admission=admission)
    request = _request()

    with pytest.raises(DaytonaAdmissionTimeoutError):
        await mgr.acquire(request, deadline=asyncio.get_running_loop().time())
    held.release()

    lease = await mgr.acquire(request, deadline=asyncio.get_running_loop().time() + 10)
    await mgr.release(lease)


@pytest.mark.asyncio
async def test_session_claim_precedes_admission_wait() -> None:
    admission = DaytonaAdmission(max_active_leases=1)
    mgr, _plat, _store, _volumes = _manager(admission=admission)
    first = _request()
    lease = await mgr.acquire(first, deadline=asyncio.get_running_loop().time() + 10)
    duplicate = LeaseRequest(
        session_id=first.session_id,
        user_id=first.user_id,
        workspace_id=first.workspace_id,
        run_id=uuid4(),
    )

    try:
        with pytest.raises(ActiveLeaseConflictError):
            await asyncio.wait_for(
                mgr.acquire(duplicate, deadline=asyncio.get_running_loop().time() + 10),
                timeout=0.1,
            )
    finally:
        await mgr.release(lease)


@pytest.mark.asyncio
async def test_acquire_starts_stopped_sandbox() -> None:
    mgr, plat, store, _volumes = _manager()
    req = _request()
    lease = await _acquire(mgr, req)
    plat.sandboxes[lease.sandbox_id].state = "stopped"
    await store.upsert(
        SandboxBinding(
            session_id=req.session_id,
            sandbox_id=lease.sandbox_id,
            workspace_id=req.workspace_id,
            volume_id=lease.volume_id,
            volume_subpath=lease.volume_subpath or f"workspaces/{req.workspace_id}",
            mount_path=lease.mount_path,
            provider_state="stopped",
        )
    )
    await mgr.release(lease)
    again = await _acquire(mgr, req)
    assert again.sandbox_id == lease.sandbox_id
    assert "start" in plat.sandboxes[lease.sandbox_id].ops


@pytest.mark.asyncio
async def test_acquire_maps_lifecycle_provider_failure_without_replacement() -> None:
    mgr, plat, _store, _volumes = _manager()
    req = _request()
    lease = await _acquire(mgr, req)
    await mgr.release(lease)
    sandbox = plat.sandboxes[lease.sandbox_id]
    sandbox.state = "stopped"

    class _ProviderError(Exception):
        status_code = 503

    def fail_start() -> None:
        raise _ProviderError("provider failed api_key=private")

    sandbox.start = fail_start  # type: ignore[method-assign]

    with pytest.raises(ProviderRequestError) as caught:
        await _acquire(mgr, req)

    assert caught.value.status_code == 503
    assert "private" not in str(caught.value)
    assert len(plat.created) == 1
    assert plat.deleted == []


@pytest.mark.asyncio
async def test_replace_keeps_volume_id() -> None:
    mgr, plat, store, _volumes = _manager()
    req = _request()
    lease = await _acquire(mgr, req)
    old_sid = lease.sandbox_id
    volume_id = lease.volume_id
    binding = await store.get(req.session_id)
    assert binding is not None
    new_binding = await mgr.replace(binding, workspace_id=req.workspace_id, user_id=req.user_id)
    assert new_binding.volume_id == volume_id
    assert new_binding.sandbox_id != old_sid
    assert new_binding.workspace_id == req.workspace_id
    assert new_binding.volume_subpath == f"workspaces/{req.workspace_id}"
    assert old_sid in plat.deleted
    assert new_binding.sandbox_id in plat.sandboxes
    labels = plat.created[-1]["labels"]
    assert labels["user_id"] == str(req.user_id)
    assert labels["workspace_id"] == str(req.workspace_id)
    assert labels["user_id"] != str(UUID(int=0))


@pytest.mark.asyncio
async def test_replace_rejects_zero_user_id() -> None:
    mgr, _plat, store, _volumes = _manager()
    req = _request()
    await _acquire(mgr, req)
    binding = await store.get(req.session_id)
    assert binding is not None
    with pytest.raises(DaytonaAdapterError, match="user_id"):
        await mgr.replace(binding, workspace_id=req.workspace_id, user_id=UUID(int=0))
    with pytest.raises(DaytonaAdapterError, match="user_id"):
        await mgr.replace(binding, workspace_id=req.workspace_id)


@pytest.mark.asyncio
async def test_acquire_replaces_unrecoverable_provider_state() -> None:
    mgr, plat, store, _volumes = _manager()
    req = _request()
    lease = await _acquire(mgr, req)
    old_sid = lease.sandbox_id
    plat.sandboxes[old_sid].state = "booting"
    await store.upsert(
        SandboxBinding(
            session_id=req.session_id,
            sandbox_id=old_sid,
            workspace_id=req.workspace_id,
            volume_id=lease.volume_id,
            volume_subpath=lease.volume_subpath or f"workspaces/{req.workspace_id}",
            mount_path=lease.mount_path,
            provider_state="running",
        )
    )
    await mgr.release(lease)
    again = await _acquire(mgr, req)
    assert again.sandbox_id != old_sid
    assert old_sid in plat.deleted
    assert plat.created[-1]["labels"]["user_id"] == str(req.user_id)

    mgr, plat, store, _volumes = _manager()
    req = _request()
    first = await _acquire(mgr, req)
    await mgr.release(first)
    # Simulate gone sandbox
    plat.sandboxes.clear()
    second = await _acquire(mgr, req)
    assert second.sandbox_id != first.sandbox_id
    assert len(plat.created) == 2
