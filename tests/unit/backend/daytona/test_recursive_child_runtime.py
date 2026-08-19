"""Unit contracts for dedicated Daytona child RLM runtime leases."""

from __future__ import annotations

import asyncio
import threading
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
from fleet_rlm.daytona.recursive_child_lease import ChildRuntimeLease, ChildRuntimeLeaseState
from fleet_rlm.daytona.session_manager import DaytonaAdmission


@dataclass
class _Fs:
    files: set[str]
    deleted: list[str] = field(default_factory=list)
    directories: set[str] = field(default_factory=set)

    async def list_files(self, _root: str, *, depth: int | None) -> list[SimpleNamespace]:
        """
        List all tracked files and directories using unbounded traversal.

        Parameters:
            _root (str): Root path for the listing.
            depth (int | None): Must be `None` to request unbounded traversal.

        Returns:
            list[SimpleNamespace]: File entries followed by directory entries, with each group sorted by path.
        """
        assert depth is None
        return [
            *[SimpleNamespace(path=path, is_dir=False) for path in sorted(self.files)],
            *[SimpleNamespace(path=path, is_dir=True) for path in sorted(self.directories)],
        ]

    async def delete_file(self, path: str, *, recursive: bool = False) -> None:
        """
        Remove a tracked file or directory and record its path.

        Parameters:
            path (str): Path of the file or directory to remove.
            recursive (bool): Whether to remove the directory and its descendants.
        """
        if recursive:
            self.files.difference_update(
                {candidate for candidate in self.files if candidate == path or candidate.startswith(path + "/")}
            )
            self.directories.difference_update(
                {candidate for candidate in self.directories if candidate == path or candidate.startswith(path + "/")}
            )
        else:
            self.files.discard(path)
            self.directories.discard(path)
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
        """
        Create a child sandbox using the supplied options.

        Parameters:
            **kwargs: Sandbox creation options.

        Returns:
            _Sandbox: The configured child sandbox.
        """
        self.create_calls.append(kwargs)
        return self.child

    async def delete(self, sandbox_id: str) -> None:
        """Record a sandbox deletion request for the specified sandbox."""
        self.deleted.append(sandbox_id)

    async def get(self, _sandbox_id: str) -> None:
        """Treat every sandbox lookup as absent."""
        return None


def test_child_runtime_lease_concurrent_close_joins_one_cleanup() -> None:
    started = threading.Event()
    release = threading.Event()
    calls: list[str] = []

    def close() -> None:
        calls.append("close")
        started.set()
        assert release.wait(2)

    lease = ChildRuntimeLease(SimpleNamespace(), "sandbox", "volume", "subpath", close)
    errors: list[BaseException] = []

    def run_close() -> None:
        try:
            lease.close()
        except BaseException as exc:  # pragma: no cover - assertion below reports unexpected failures
            errors.append(exc)

    first = threading.Thread(target=run_close)
    second_entered = threading.Event()

    def run_second_close() -> None:
        second_entered.set()
        run_close()

    second = threading.Thread(target=run_second_close)
    first.start()
    assert started.wait(2)
    assert lease.state is ChildRuntimeLeaseState.CLOSING
    second.start()
    assert second_entered.wait(2)
    assert second.is_alive()
    release.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert errors == []
    assert calls == ["close"]
    assert lease.state is ChildRuntimeLeaseState.CLOSED
    lease.close()
    assert calls == ["close"]


@pytest.mark.asyncio
async def test_child_scope_purge_removes_nested_files_and_directories() -> None:
    from fleet_rlm.daytona.recursive_child_cleanup import purge_regular_files

    root = "/home/daytona/fleet"
    fs = _Fs(
        files={f"{root}/top.txt", f"{root}/nested/deep/file.txt"},
        directories={f"{root}/nested", f"{root}/nested/deep"},
    )

    await purge_regular_files(SimpleNamespace(fs=fs), root)

    assert fs.files == set()
    assert fs.directories == set()
    assert fs.deleted == [
        f"{root}/nested/deep/file.txt",
        f"{root}/top.txt",
        f"{root}/nested/deep",
        f"{root}/nested",
    ]


def test_child_runtime_lease_failure_is_failed_and_reobserved() -> None:
    calls = 0

    def close() -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError("cleanup failed")

    lease = ChildRuntimeLease(SimpleNamespace(), "sandbox", "volume", "subpath", close)
    with pytest.raises(RuntimeError, match="cleanup failed"):
        lease.close()
    assert lease.state is ChildRuntimeLeaseState.FAILED
    assert isinstance(lease.close_error, RuntimeError)

    with pytest.raises(RuntimeError, match="cleanup failed"):
        lease.close()
    assert calls == 1


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
            """
            Record an interpreter shutdown request and whether strict broker cleanup was requested.

            Parameters:
                strict_broker_cleanup (bool): Whether strict broker cleanup was requested.
            """
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
async def test_child_cleanup_timeout_retains_provider_future_until_it_settles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child = _Sandbox("child-sandbox", _Fs({"/home/daytona/fleet/intermediate.txt"}))
    release_delete = asyncio.Event()

    class HangingDeletePlatform(_Platform):
        async def delete(self, sandbox_id: str) -> None:
            self.deleted.append(sandbox_id)
            await release_delete.wait()

    platform = HangingDeletePlatform(child)

    class Interpreter:
        def __init__(self, **_kwargs: object) -> None:
            return None

        def shutdown(self, *, strict_broker_cleanup: bool = False) -> None:
            assert strict_broker_cleanup is True

    monkeypatch.setattr(recursive_child_runtime, "DaytonaCodeInterpreter", Interpreter)
    monkeypatch.setattr(recursive_child_runtime, "sandbox_backend", lambda sandbox, **_kwargs: sandbox)
    monkeypatch.setattr(recursive_child_runtime, "_CHILD_CLEANUP_RESULT_TIMEOUT_S", 0.05)
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
    )
    lease = await asyncio.to_thread(factory, 1)

    with pytest.raises(recursive_child_runtime.ChildRuntimeCleanupError, match="recursive child cleanup failed"):
        await asyncio.to_thread(lease.close)
    wait_owned = asyncio.create_task(asyncio.to_thread(factory.wait_owned))
    await asyncio.sleep(0.05)
    assert not wait_owned.done()

    release_delete.set()
    await asyncio.wait_for(wait_owned, timeout=2)
    assert platform.deleted == ["child-sandbox"]
    permit = await admission.acquire(deadline=asyncio.get_running_loop().time() + 1)
    permit.release()


@pytest.mark.asyncio
async def test_interpreter_shutdown_timeout_quarantines_provider_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child = _Sandbox("child-sandbox", _Fs({"/home/daytona/fleet/intermediate.txt"}))
    release_shutdown = threading.Event()
    platform = _Platform(child)

    class HangingInterpreter:
        def __init__(self, **_kwargs: object) -> None:
            return None

        def shutdown(self, *, strict_broker_cleanup: bool = False) -> None:
            assert strict_broker_cleanup is True
            release_shutdown.wait(2)

    monkeypatch.setattr(recursive_child_runtime, "DaytonaCodeInterpreter", HangingInterpreter)
    monkeypatch.setattr(recursive_child_runtime, "sandbox_backend", lambda sandbox, **_kwargs: sandbox)
    monkeypatch.setattr(recursive_child_runtime, "_CHILD_CLEANUP_RESULT_TIMEOUT_S", 0.05)
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
    )
    lease = await asyncio.to_thread(factory, 1)

    started = asyncio.get_running_loop().time()
    with pytest.raises(recursive_child_runtime.ChildRuntimeCleanupError, match="recursive child cleanup failed"):
        await asyncio.to_thread(lease.close)
    assert asyncio.get_running_loop().time() - started < 0.5

    release_shutdown.set()
    await asyncio.to_thread(factory.wait_owned)
    assert platform.deleted == ["child-sandbox"]
    permit = await admission.acquire(deadline=asyncio.get_running_loop().time() + 1)
    permit.release()


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
            """
            Simulate a failed strict broker cleanup.

            Parameters:
                strict_broker_cleanup (bool): Must be set to `True`.

            Raises:
                RuntimeError: Always raised to represent broker cleanup failure.
            """
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
async def test_child_factory_times_out_when_real_admission_is_saturated() -> None:
    platform = _Platform(_Sandbox("child-sandbox", _Fs(set())))
    admission = DaytonaAdmission(max_active_leases=1)
    held = await admission.acquire(deadline=asyncio.get_running_loop().time() + 1)
    loop = asyncio.get_running_loop()
    factory = recursive_child_runtime.build_child_runtime_factory(
        loop=loop,
        platform=platform,
        admission=admission,
        volume_id="shared-volume",
        mount_path="/home/daytona/fleet",
        workspace_id=uuid4(),
        run_id=uuid4(),
        deadline=loop.time() + 0.05,
        execution_timeout_s=30,
        execution_output_cap=1000,
    )

    try:
        with pytest.raises(TimeoutError, match="acquisition deadline exceeded"):
            await asyncio.to_thread(factory, 1)
    finally:
        held.release()
    await asyncio.to_thread(factory.wait_owned)

    assert platform.create_calls == []


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
        """Determine whether authorization is granted on the first check.

        Returns:
                bool: `True` on the first call and `False` on subsequent calls.
        """
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
            """
            Create and return the configured child sandbox.

            Returns:
                object: The child sandbox.
            """
            return child

        async def delete(self, _sandbox_id: str) -> None:
            """Raise an error to simulate a failed provider cleanup operation."""
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


@pytest.mark.asyncio
async def test_child_factory_adopts_provider_acquisition_that_finishes_after_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A late provider result is closed instead of orphaning its Sandbox or permit."""
    child = _Sandbox("child-sandbox", _Fs(set()))
    release_provider = asyncio.Event()

    class LatePlatform(_Platform):
        async def create(self, **kwargs: object) -> _Sandbox:
            self.create_calls.append(kwargs)
            try:
                await release_provider.wait()
            except asyncio.CancelledError:
                # Simulate a provider SDK call that ignores cancellation and
                # returns a Sandbox after the caller's deadline.
                await release_provider.wait()
            return child

    platform = LatePlatform(child)
    admission = DaytonaAdmission(max_active_leases=1)
    shutdown_calls: list[bool] = []

    class Interpreter:
        def __init__(self, **_kwargs: object) -> None:
            return None

        def shutdown(self, *, strict_broker_cleanup: bool = False) -> None:
            shutdown_calls.append(strict_broker_cleanup)

    monkeypatch.setattr(recursive_child_runtime, "DaytonaCodeInterpreter", Interpreter)
    monkeypatch.setattr(recursive_child_runtime, "sandbox_backend", lambda sandbox, **_kwargs: sandbox)
    loop = asyncio.get_running_loop()
    factory = recursive_child_runtime.build_child_runtime_factory(
        loop=loop,
        platform=platform,
        admission=admission,
        volume_id="shared-volume",
        mount_path="/home/daytona/fleet",
        workspace_id=uuid4(),
        run_id=uuid4(),
        deadline=loop.time() + 0.05,
        execution_timeout_s=30,
        execution_output_cap=1000,
    )

    with pytest.raises(TimeoutError, match="acquisition deadline exceeded"):
        await asyncio.to_thread(factory, 1)
    assert platform.create_calls
    assert platform.deleted == []

    release_provider.set()
    await asyncio.to_thread(factory.wait_owned)

    assert shutdown_calls == [True]
    assert platform.deleted == ["child-sandbox"]
    permit = await admission.acquire(deadline=loop.time() + 1)
    permit.release()


@pytest.mark.asyncio
async def test_child_cleanup_falls_back_to_disposable_loop_when_owner_loop_closed() -> None:
    """Provider cleanup and permit release must not depend on the owner loop."""
    child = _Sandbox("child-sandbox", _Fs({"/home/daytona/fleet/intermediate.txt"}))
    platform = _Platform(child)
    admission = DaytonaAdmission(max_active_leases=1)
    permit = await admission.acquire(deadline=asyncio.get_running_loop().time() + 1)
    shutdown_calls: list[bool] = []

    class Interpreter:
        def shutdown(self, *, strict_broker_cleanup: bool = False) -> None:
            shutdown_calls.append(strict_broker_cleanup)

    class ClosedLoop:
        def call_soon_threadsafe(self, *_args: object, **_kwargs: object):
            raise RuntimeError("Event loop is closed")

    recursive_child_runtime._close_child_runtime_sync(
        loop=ClosedLoop(),  # type: ignore[arg-type]
        platform=platform,
        sandbox=child,
        sandbox_id="child-sandbox",
        mount_path="/home/daytona/fleet",
        interpreter=Interpreter(),  # type: ignore[arg-type]
        permit=permit,
    )

    assert shutdown_calls == [True]
    assert platform.deleted == ["child-sandbox"]
    assert child.fs.deleted == ["/home/daytona/fleet/intermediate.txt"]
    permit = await admission.acquire(deadline=asyncio.get_running_loop().time() + 1)
    permit.release()


@pytest.mark.asyncio
async def test_factory_wait_owned_bounds_never_completing_provider_acquisition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child = _Sandbox("child-sandbox", _Fs(set()))
    release_provider = asyncio.Event()

    class HangingPlatform(_Platform):
        async def create(self, **kwargs: object) -> _Sandbox:
            self.create_calls.append(kwargs)
            try:
                await release_provider.wait()
            except asyncio.CancelledError:
                await release_provider.wait()
            return child

    class Interpreter:
        def __init__(self, **_kwargs: object) -> None:
            return None

        def shutdown(self, *, strict_broker_cleanup: bool = False) -> None:
            assert strict_broker_cleanup is True

    monkeypatch.setattr(recursive_child_runtime, "DaytonaCodeInterpreter", Interpreter)
    monkeypatch.setattr(recursive_child_runtime, "sandbox_backend", lambda sandbox, **_kwargs: sandbox)
    monkeypatch.setattr(recursive_child_runtime, "_CHILD_CLEANUP_RESULT_TIMEOUT_S", 0.05)
    platform = HangingPlatform(child)
    admission = DaytonaAdmission(max_active_leases=1)
    loop = asyncio.get_running_loop()
    factory = recursive_child_runtime.build_child_runtime_factory(
        loop=loop,
        platform=platform,
        admission=admission,
        volume_id="shared-volume",
        mount_path="/home/daytona/fleet",
        workspace_id=uuid4(),
        run_id=uuid4(),
        deadline=loop.time() + 0.05,
        execution_timeout_s=30,
        execution_output_cap=1000,
    )

    with pytest.raises(TimeoutError, match="acquisition deadline exceeded"):
        await asyncio.to_thread(factory, 1)

    started = asyncio.get_running_loop().time()
    with pytest.raises(recursive_child_runtime.ChildRuntimeCleanupError, match="recursive child cleanup failed"):
        await asyncio.to_thread(factory.wait_owned)
    assert asyncio.get_running_loop().time() - started < 5

    # The late provider result is still adopted and closed once it resolves.
    release_provider.set()
    for _ in range(200):
        if platform.deleted:
            break
        await asyncio.sleep(0.01)
    assert platform.deleted == ["child-sandbox"]
    permit = await admission.acquire(deadline=loop.time() + 1)
    permit.release()
