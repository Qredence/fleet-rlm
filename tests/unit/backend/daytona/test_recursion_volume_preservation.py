"""P39b shared sibling Volume preservation lanes.

Behavior-only evidence for VAL-REC-021: child cleanup may purge only the
closing child's mounted recursive scope. Files in the Root Workspace scope
and every sibling child scope must remain byte-for-byte present and
unmodified, proven through before/after sha256 checksum manifests. The
preservation must hold after a clean close, after a child failure, and
after cancellation revokes authority.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field
from types import SimpleNamespace
from uuid import uuid4

import pytest

from fleet_rlm.daytona import recursive_child_runtime
from fleet_rlm.daytona.provisioning import recursive_child_volume_subpath
from fleet_rlm.daytona.recursive_child_runtime import ChildRuntimeLeaseState
from fleet_rlm.daytona.session_manager import DaytonaAdmission

MOUNT = "/home/daytona/fleet"


@dataclass
class _VolumeFs:
    """Tracked Volume scope with content checksums.

    Files carry byte content so preservation can be proven through sha256
    checksum manifests, not just path presence.
    """

    files: dict[str, bytes]
    deleted: list[str] = field(default_factory=list)
    directories: set[str] = field(default_factory=set)

    async def list_files(self, _root: str, *, depth: int | None) -> list[SimpleNamespace]:
        assert depth is None
        return [
            *[SimpleNamespace(path=path, is_dir=False) for path in sorted(self.files)],
            *[SimpleNamespace(path=path, is_dir=True) for path in sorted(self.directories)],
        ]

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


def _checksum_manifest(fs: _VolumeFs) -> dict[str, str]:
    return {path: hashlib.sha256(content).hexdigest() for path, content in sorted(fs.files.items())}


@dataclass
class _Sandbox:
    id: str
    fs: _VolumeFs


class _MultiSandboxPlatform:
    """Serves one distinct sandbox per acquisition, tracking provider calls."""

    def __init__(self, sandboxes: list[_Sandbox]) -> None:
        self._queue = list(sandboxes)
        self.create_calls: list[dict[str, object]] = []
        self.deleted: list[str] = []

    async def create(self, **kwargs: object) -> _Sandbox:
        self.create_calls.append(kwargs)
        return self._queue.pop(0)

    async def delete(self, sandbox_id: str) -> None:
        self.deleted.append(sandbox_id)

    async def get(self, _sandbox_id: str) -> None:
        # Every lookup is absent: provider-side deletion is confirmed.
        return None


class _RecordingInterpreter:
    def __init__(self, *, fail_shutdown: bool = False) -> None:
        self._fail_shutdown = fail_shutdown
        self.shutdown_calls: list[bool] = []

    def shutdown(self, *, strict_broker_cleanup: bool = False) -> None:
        self.shutdown_calls.append(strict_broker_cleanup)
        if self._fail_shutdown:
            raise RuntimeError("broker cleanup failed")


class _InterpreterBox:
    """Shared fault-injection state for interpreter doubles."""

    def __init__(self) -> None:
        self.fail_shutdown = False
        self.instances: list[_RecordingInterpreter] = []


@pytest.fixture
def interpreter_box() -> _InterpreterBox:
    return _InterpreterBox()


def _factory(
    monkeypatch: pytest.MonkeyPatch,
    platform: _MultiSandboxPlatform,
    admission: DaytonaAdmission,
    workspace_id: object,
    run_id: object,
    interpreter_box: _InterpreterBox,
    *,
    is_authorized: object = None,
) -> object:
    def interpreter_factory(**_kwargs: object) -> _RecordingInterpreter:
        interpreter = _RecordingInterpreter(fail_shutdown=interpreter_box.fail_shutdown)
        interpreter_box.instances.append(interpreter)
        return interpreter

    monkeypatch.setattr(recursive_child_runtime, "DaytonaCodeInterpreter", interpreter_factory)
    monkeypatch.setattr(recursive_child_runtime, "sandbox_backend", lambda sandbox, **_kwargs: sandbox)
    return recursive_child_runtime.build_child_runtime_factory(
        loop=asyncio.get_running_loop(),
        platform=platform,
        admission=admission,
        volume_id="shared-volume",
        mount_path=MOUNT,
        workspace_id=workspace_id,
        run_id=run_id,
        deadline=asyncio.get_running_loop().time() + 30,
        execution_timeout_s=30,
        execution_output_cap=1000,
        is_authorized=is_authorized,
    )


@pytest.mark.asyncio
async def test_val_rec_021_close_child_a_preserves_root_and_sibling_volume_byte_for_byte(
    monkeypatch: pytest.MonkeyPatch, interpreter_box: _InterpreterBox
) -> None:
    workspace_id = uuid4()
    run_id = uuid4()
    a_scope = recursive_child_volume_subpath(workspace_id, run_id, 1)
    b_scope = recursive_child_volume_subpath(workspace_id, run_id, 2)

    root_fs = _VolumeFs(
        files={
            f"{MOUNT}/workspaces/{workspace_id}/root-marker.txt": b"root-content-1",
            f"{MOUNT}/workspaces/{workspace_id}/projects/report.md": b"root-project-bytes",
        }
    )
    child_a_fs = _VolumeFs(
        files={
            f"{MOUNT}/{a_scope}/a-top.txt": b"child-a-top",
            f"{MOUNT}/{a_scope}/a-nested/deep/file.txt": b"child-a-deep",
        },
        directories={f"{MOUNT}/{a_scope}/a-nested", f"{MOUNT}/{a_scope}/a-nested/deep"},
    )
    child_b_fs = _VolumeFs(files={f"{MOUNT}/{b_scope}/b-marker.txt": b"child-b-bytes"})

    child_a = _Sandbox("child-a-sandbox", child_a_fs)
    child_b = _Sandbox("child-b-sandbox", child_b_fs)
    platform = _MultiSandboxPlatform([child_a, child_b])
    admission = DaytonaAdmission(max_active_leases=3)
    factory = _factory(monkeypatch, platform, admission, workspace_id, run_id, interpreter_box)

    lease_a = await asyncio.to_thread(factory, 1)
    lease_b = await asyncio.to_thread(factory, 2)

    # Shared Volume sibling mount: identical volume id and mount path for
    # both children, distinct validated recursive subpaths.
    assert (lease_a.volume_id, lease_b.volume_id) == ("shared-volume", "shared-volume")
    assert lease_a.volume_subpath == a_scope
    assert lease_b.volume_subpath == b_scope
    for call in platform.create_calls:
        assert call["volume_id"] == "shared-volume"
        assert call["mount_path"] == MOUNT
        assert call["ephemeral"] is True
        assert call["labels"] == {"fleet.runtime": "recursive-child"}

    root_manifest_before = _checksum_manifest(root_fs)
    sibling_manifest_before = _checksum_manifest(child_b_fs)

    # Close child A: cleanup purges only A's mounted recursive scope.
    await asyncio.to_thread(lease_a.close)

    assert lease_a.state is ChildRuntimeLeaseState.CLOSED
    # A's nested regular files were deleted first, then directories in
    # deepest-first order.
    assert child_a_fs.files == {}
    assert child_a_fs.directories == set()
    assert child_a_fs.deleted == [
        f"{MOUNT}/{a_scope}/a-nested/deep/file.txt",
        f"{MOUNT}/{a_scope}/a-top.txt",
        f"{MOUNT}/{a_scope}/a-nested/deep",
        f"{MOUNT}/{a_scope}/a-nested",
    ]
    # Only A's provider sandbox was deleted; A's interpreter shut down once.
    assert platform.deleted == ["child-a-sandbox"]
    assert interpreter_box.instances and interpreter_box.instances[0].shutdown_calls == [True]
    # Admission restored: the full capacity is acquirable again.
    permit = await admission.acquire(deadline=asyncio.get_running_loop().time() + 1)
    permit.release()

    # Root and sibling B scopes are byte-for-byte unchanged.
    assert _checksum_manifest(root_fs) == root_manifest_before
    assert _checksum_manifest(child_b_fs) == sibling_manifest_before
    assert root_fs.deleted == []
    assert child_b_fs.deleted == []

    # The sibling child still owns its lease and closes cleanly afterwards
    # without disturbing the already-preserved Root scope.
    await asyncio.to_thread(lease_b.close)
    assert lease_b.state is ChildRuntimeLeaseState.CLOSED
    assert _checksum_manifest(root_fs) == root_manifest_before


@pytest.mark.asyncio
async def test_val_rec_021_child_failure_cleanup_still_preserves_root_and_sibling_volume(
    monkeypatch: pytest.MonkeyPatch, interpreter_box: _InterpreterBox
) -> None:
    workspace_id = uuid4()
    run_id = uuid4()
    a_scope = recursive_child_volume_subpath(workspace_id, run_id, 1)
    b_scope = recursive_child_volume_subpath(workspace_id, run_id, 2)

    root_fs = _VolumeFs(files={f"{MOUNT}/workspaces/{workspace_id}/root-marker.txt": b"root-content-fail"})
    child_a_fs = _VolumeFs(
        files={f"{MOUNT}/{a_scope}/a-top.txt": b"child-a-fail-scope"},
        directories=set(),
    )
    child_b_fs = _VolumeFs(files={f"{MOUNT}/{b_scope}/b-marker.txt": b"child-b-fail-bytes"})

    platform = _MultiSandboxPlatform([_Sandbox("child-a-sandbox", child_a_fs), _Sandbox("child-b-sandbox", child_b_fs)])
    admission = DaytonaAdmission(max_active_leases=3)
    # Only child A's interpreter fails its strict shutdown: the fault flag is
    # cleared before B is acquired, so B's cleanup lane stays clean.
    interpreter_box.fail_shutdown = True
    factory = _factory(monkeypatch, platform, admission, workspace_id, run_id, interpreter_box)

    lease_a = await asyncio.to_thread(factory, 1)
    interpreter_box.fail_shutdown = False
    lease_b = await asyncio.to_thread(factory, 2)

    root_manifest_before = _checksum_manifest(root_fs)
    sibling_manifest_before = _checksum_manifest(child_b_fs)

    # The failing child close raises the typed cleanup error, while the
    # remaining cleanup steps (scope purge, provider delete, admission
    # restore) still execute.
    with pytest.raises(recursive_child_runtime.ChildRuntimeCleanupError, match="recursive child cleanup failed"):
        await asyncio.to_thread(lease_a.close)
    assert lease_a.state is ChildRuntimeLeaseState.FAILED
    # The failure is re-observed without rerunning cleanup.
    with pytest.raises(recursive_child_runtime.ChildRuntimeCleanupError):
        await asyncio.to_thread(lease_a.close)
    assert interpreter_box.instances[0].shutdown_calls == [True]

    # A's scope was still purged and its sandbox deleted despite the failure.
    assert child_a_fs.files == {}
    assert child_a_fs.deleted == [f"{MOUNT}/{a_scope}/a-top.txt"]
    assert platform.deleted == ["child-a-sandbox"]
    permit = await admission.acquire(deadline=asyncio.get_running_loop().time() + 1)
    permit.release()

    # Root and sibling B remain byte-for-byte unchanged after the failure.
    assert _checksum_manifest(root_fs) == root_manifest_before
    assert _checksum_manifest(child_b_fs) == sibling_manifest_before
    assert root_fs.deleted == []
    assert child_b_fs.deleted == []

    await asyncio.to_thread(lease_b.close)
    assert lease_b.state is ChildRuntimeLeaseState.CLOSED


@pytest.mark.asyncio
async def test_val_rec_021_cancellation_preserves_volume_state_and_allocates_nothing_further(
    monkeypatch: pytest.MonkeyPatch, interpreter_box: _InterpreterBox
) -> None:
    workspace_id = uuid4()
    run_id = uuid4()
    a_scope = recursive_child_volume_subpath(workspace_id, run_id, 1)
    b_scope = recursive_child_volume_subpath(workspace_id, run_id, 2)

    root_fs = _VolumeFs(files={f"{MOUNT}/workspaces/{workspace_id}/root-marker.txt": b"root-content-cancel"})
    child_a_fs = _VolumeFs(files={f"{MOUNT}/{a_scope}/a-top.txt": b"child-a-cancel-scope"})
    # A sibling scope that is prepared but never allocated after revocation.
    sibling_fs = _VolumeFs(files={f"{MOUNT}/{b_scope}/b-marker.txt": b"child-b-cancel-bytes"})

    platform = _MultiSandboxPlatform([_Sandbox("child-a-sandbox", child_a_fs), _Sandbox("child-b-sandbox", sibling_fs)])
    admission = DaytonaAdmission(max_active_leases=3)
    authorized = True
    factory = _factory(
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

    # Claim loss/cancellation revokes authority: the next acquisition is
    # rejected before any allocation.
    authorized = False
    with pytest.raises(recursive_child_runtime.ChildRuntimeAuthorizationError, match="no longer authorized"):
        await asyncio.to_thread(factory, 2)
    assert len(platform.create_calls) == 1
    assert sibling_fs.deleted == []

    # The active child still settles through the same cleanup law.
    await asyncio.to_thread(lease_a.close)
    assert lease_a.state is ChildRuntimeLeaseState.CLOSED
    assert child_a_fs.files == {}
    assert platform.deleted == ["child-a-sandbox"]
    permit = await admission.acquire(deadline=asyncio.get_running_loop().time() + 1)
    permit.release()

    # Root and the never-allocated sibling scope are byte-for-byte preserved.
    assert _checksum_manifest(root_fs) == root_manifest_before
    assert _checksum_manifest(sibling_fs) == sibling_manifest_before
    assert root_fs.deleted == []
