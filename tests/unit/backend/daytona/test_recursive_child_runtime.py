"""Unit contracts for dedicated Daytona child RLM runtime leases."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from types import SimpleNamespace
from uuid import uuid4

import pytest

from fleet_rlm.daytona import recursive_child_runtime
from fleet_rlm.daytona.provisioning import (
    recursive_child_volume_subpath,
    require_recursive_child_volume_subpath,
    require_scoped_volume_subpath,
)
from fleet_rlm.daytona.session_manager import DaytonaAdmission


@dataclass
class _Fs:
    files: set[str]
    deleted: list[str] = field(default_factory=list)

    async def list_files(self, _root: str, *, depth: int) -> list[SimpleNamespace]:
        assert depth == 64
        return [SimpleNamespace(path=path, is_dir=False) for path in sorted(self.files)]

    async def delete_file(self, path: str) -> None:
        self.files.remove(path)
        self.deleted.append(path)


@dataclass
class _Sandbox:
    id: str
    fs: _Fs


class _Platform:
    def __init__(self, child: _Sandbox) -> None:
        self.child = child
        self.create_calls: list[dict[str, object]] = []
        self.deleted: list[str] = []

    async def create(self, **kwargs: object) -> _Sandbox:
        self.create_calls.append(kwargs)
        return self.child

    async def delete(self, sandbox_id: str) -> None:
        self.deleted.append(sandbox_id)


@pytest.mark.asyncio
async def test_child_runtime_uses_sibling_volume_scope_and_strictly_cleans_only_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid4()
    run_id = uuid4()
    root_fs = _Fs({"/home/daytona/fleet/workspaces-root.txt"})
    child_fs = _Fs({"/home/daytona/fleet/intermediate.txt"})
    root = _Sandbox("root-sandbox", root_fs)
    child = _Sandbox("child-sandbox", child_fs)
    platform = _Platform(child)
    shutdown_calls: list[bool] = []

    class Interpreter:
        def __init__(self, **_kwargs: object) -> None:
            return None

        def shutdown(self, *, strict_broker_cleanup: bool = False) -> None:
            shutdown_calls.append(strict_broker_cleanup)

    monkeypatch.setattr(recursive_child_runtime, "DaytonaCodeInterpreter", Interpreter)
    monkeypatch.setattr(recursive_child_runtime, "sandbox_backend", lambda sandbox, **_kwargs: sandbox)
    factory = recursive_child_runtime.build_child_runtime_factory(
        loop=asyncio.get_running_loop(),
        platform=platform,
        admission=DaytonaAdmission(max_active_leases=2),
        volume_id="shared-volume",
        mount_path="/home/daytona/fleet",
        workspace_id=workspace_id,
        run_id=run_id,
        deadline=asyncio.get_running_loop().time() + 30,
        execution_timeout_s=30,
        execution_output_cap=1000,
    )

    lease = await asyncio.to_thread(factory, 1)

    assert lease.sandbox_id != root.id
    assert lease.sandbox_id == child.id
    assert lease.volume_id == "shared-volume"
    assert lease.volume_subpath == recursive_child_volume_subpath(workspace_id, run_id, 1)
    assert platform.create_calls == [
        {
            "volume_id": "shared-volume",
            "mount_path": "/home/daytona/fleet",
            "volume_subpath": lease.volume_subpath,
            "labels": {"fleet.runtime": "recursive-child"},
            "ephemeral": True,
        }
    ]

    await asyncio.to_thread(lease.close)

    assert shutdown_calls == [True]
    assert child_fs.files == set()
    assert child_fs.deleted == ["/home/daytona/fleet/intermediate.txt"]
    assert root_fs.files == {"/home/daytona/fleet/workspaces-root.txt"}
    assert root_fs.deleted == []
    assert platform.deleted == ["child-sandbox"]


@pytest.mark.asyncio
async def test_child_runtime_attempts_scope_and_sandbox_cleanup_after_interpreter_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child = _Sandbox("child-sandbox", _Fs({"/home/daytona/fleet/intermediate.txt"}))
    platform = _Platform(child)

    class Interpreter:
        def __init__(self, **_kwargs: object) -> None:
            return None

        def shutdown(self, *, strict_broker_cleanup: bool = False) -> None:
            assert strict_broker_cleanup is True
            raise RuntimeError("broker cleanup failed")

    monkeypatch.setattr(recursive_child_runtime, "DaytonaCodeInterpreter", Interpreter)
    monkeypatch.setattr(recursive_child_runtime, "sandbox_backend", lambda sandbox, **_kwargs: sandbox)
    factory = recursive_child_runtime.build_child_runtime_factory(
        loop=asyncio.get_running_loop(),
        platform=platform,
        admission=DaytonaAdmission(max_active_leases=2),
        volume_id="shared-volume",
        mount_path="/home/daytona/fleet",
        workspace_id=uuid4(),
        run_id=uuid4(),
        deadline=asyncio.get_running_loop().time() + 30,
        execution_timeout_s=30,
        execution_output_cap=1000,
    )

    lease = await asyncio.to_thread(factory, 1)
    with pytest.raises(recursive_child_runtime.ChildRuntimeCleanupError, match="recursive child cleanup failed"):
        await asyncio.to_thread(lease.close)

    assert child.fs.files == set()
    assert platform.deleted == ["child-sandbox"]


@pytest.mark.asyncio
async def test_revocation_before_admission_performs_no_allocation() -> None:
    platform = _Platform(_Sandbox("child-sandbox", _Fs(set())))
    admission = DaytonaAdmission(max_active_leases=1)
    factory = recursive_child_runtime.build_child_runtime_factory(
        loop=asyncio.get_running_loop(),
        platform=platform,
        admission=admission,
        volume_id="shared-volume",
        mount_path="/home/daytona/fleet",
        workspace_id=uuid4(),
        run_id=uuid4(),
        deadline=asyncio.get_running_loop().time() + 30,
        execution_timeout_s=30,
        execution_output_cap=1000,
        is_authorized=lambda: False,
    )

    with pytest.raises(recursive_child_runtime.ChildRuntimeAuthorizationError):
        await asyncio.to_thread(factory, 1)

    assert platform.create_calls == []
    permit = await admission.acquire(deadline=asyncio.get_running_loop().time() + 1)
    permit.release()


@pytest.mark.asyncio
async def test_revocation_after_admission_releases_permit_without_sandbox_creation() -> None:
    checks = 0

    def is_authorized() -> bool:
        nonlocal checks
        checks += 1
        return checks == 1

    platform = _Platform(_Sandbox("child-sandbox", _Fs(set())))
    admission = DaytonaAdmission(max_active_leases=1)
    factory = recursive_child_runtime.build_child_runtime_factory(
        loop=asyncio.get_running_loop(),
        platform=platform,
        admission=admission,
        volume_id="shared-volume",
        mount_path="/home/daytona/fleet",
        workspace_id=uuid4(),
        run_id=uuid4(),
        deadline=asyncio.get_running_loop().time() + 30,
        execution_timeout_s=30,
        execution_output_cap=1000,
        is_authorized=is_authorized,
    )

    with pytest.raises(recursive_child_runtime.ChildRuntimeAuthorizationError):
        await asyncio.to_thread(factory, 1)

    assert platform.create_calls == []
    permit = await admission.acquire(deadline=asyncio.get_running_loop().time() + 1)
    permit.release()


@pytest.mark.asyncio
async def test_revocation_after_sandbox_creation_deletes_sandbox_and_releases_permit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checks = 0

    def is_authorized() -> bool:
        nonlocal checks
        checks += 1
        return checks < 3

    child = _Sandbox("child-sandbox", _Fs(set()))
    platform = _Platform(child)
    admission = DaytonaAdmission(max_active_leases=1)
    monkeypatch.setattr(recursive_child_runtime, "sandbox_backend", lambda sandbox, **_kwargs: sandbox)
    factory = recursive_child_runtime.build_child_runtime_factory(
        loop=asyncio.get_running_loop(),
        platform=platform,
        admission=admission,
        volume_id="shared-volume",
        mount_path="/home/daytona/fleet",
        workspace_id=uuid4(),
        run_id=uuid4(),
        deadline=asyncio.get_running_loop().time() + 30,
        execution_timeout_s=30,
        execution_output_cap=1000,
        is_authorized=is_authorized,
    )

    with pytest.raises(recursive_child_runtime.ChildRuntimeAuthorizationError):
        await asyncio.to_thread(factory, 1)

    assert platform.deleted == ["child-sandbox"]
    permit = await admission.acquire(deadline=asyncio.get_running_loop().time() + 1)
    permit.release()


def test_recursive_child_subpath_is_separate_from_root_workspace_validation() -> None:
    workspace_id = uuid4()
    run_id = uuid4()
    subpath = recursive_child_volume_subpath(workspace_id, run_id, 3)

    assert (
        require_recursive_child_volume_subpath(
            subpath,
            workspace_id=workspace_id,
            run_id=run_id,
            call_index=3,
        )
        == subpath
    )
    with pytest.raises(ValueError, match="workspaces"):
        require_scoped_volume_subpath(subpath, workspace_id=workspace_id)


@pytest.mark.asyncio
async def test_failed_child_creation_with_failed_cleanup_is_marked_fatal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child = SimpleNamespace(id=None, fs=_Fs(set()))

    class Platform:
        async def create(self, **_kwargs: object) -> object:
            return child

        async def delete(self, _sandbox_id: str) -> None:
            raise RuntimeError("provider cleanup failed")

    monkeypatch.setattr(recursive_child_runtime, "sandbox_backend", lambda sandbox, **_kwargs: sandbox)
    factory = recursive_child_runtime.build_child_runtime_factory(
        loop=asyncio.get_running_loop(),
        platform=Platform(),
        admission=DaytonaAdmission(max_active_leases=2),
        volume_id="shared-volume",
        mount_path="/home/daytona/fleet",
        workspace_id=uuid4(),
        run_id=uuid4(),
        deadline=asyncio.get_running_loop().time() + 30,
        execution_timeout_s=30,
        execution_output_cap=1000,
    )

    with pytest.raises(recursive_child_runtime.ChildRuntimeCleanupError, match="recursive child cleanup failed"):
        await asyncio.to_thread(factory, 1)
