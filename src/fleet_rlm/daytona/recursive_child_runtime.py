"""Dedicated disposable Daytona runtimes for native DSPy recursive children."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any
from uuid import UUID

from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, sandbox_backend
from fleet_rlm.daytona.provisioning import SandboxPlatform, recursive_child_volume_subpath
from fleet_rlm.daytona.session_manager import DaytonaAdmission, DaytonaAdmissionPermit
from fleet_rlm.rlm.child_runtime import (
    ChildRuntimeAuthorizationError,
    ChildRuntimeCleanupError,
    ChildRuntimeFactory,
)


@dataclass(slots=True)
class ChildRuntimeLease:
    """One synchronously usable child interpreter plus its owned cleanup action."""

    interpreter: DaytonaCodeInterpreter
    sandbox_id: str
    volume_id: str
    volume_subpath: str
    _close: Callable[[], None] = field(repr=False)
    _closed: bool = field(default=False, init=False, repr=False)

    def close(self) -> None:
        """Dispose the child runtime resources; repeated calls have no effect."""
        if self._closed:
            return
        self._closed = True
        self._close()


def build_child_runtime_factory(
    *,
    loop: asyncio.AbstractEventLoop,
    platform: SandboxPlatform,
    admission: DaytonaAdmission,
    volume_id: str,
    mount_path: str,
    workspace_id: UUID,
    run_id: UUID,
    deadline: float,
    execution_timeout_s: int,
    execution_output_cap: int,
    is_authorized: Callable[[], bool] | None = None,
) -> ChildRuntimeFactory:
    """
    Build a factory that acquires disposable child runtimes for a root turn.

    The returned factory blocks the calling worker thread until child-runtime acquisition completes on the event loop.

    Parameters:
        volume_id (str): Identifier of the volume mounted in child runtimes.
        mount_path (str): Mount path used by child runtimes.
        workspace_id (UUID): Identifier of the workspace owning the runtimes.
        run_id (UUID): Identifier of the root turn run.
        deadline (float): Acquisition deadline as a monotonic timestamp.
        execution_timeout_s (int): Maximum execution time for each child runtime.
        execution_output_cap (int): Maximum output size for each child runtime.
        is_authorized (Callable[[], bool] | None): Optional callback that determines whether
            child-runtime creation remains authorized.

    Returns:
        ChildRuntimeFactory: A factory that accepts a call index and returns a leased child runtime.
    """

    def create(call_index: int) -> ChildRuntimeLease:
        """
        Acquire a disposable child runtime lease for a recursive call.

        Parameters:
            call_index (int): Index identifying the recursive child call.

        Returns:
            ChildRuntimeLease: Lease for the acquired child runtime.
        """
        future = asyncio.run_coroutine_threadsafe(
            _acquire_child_runtime(
                loop=loop,
                platform=platform,
                admission=admission,
                volume_id=volume_id,
                mount_path=mount_path,
                workspace_id=workspace_id,
                run_id=run_id,
                call_index=call_index,
                deadline=deadline,
                execution_timeout_s=execution_timeout_s,
                execution_output_cap=execution_output_cap,
                is_authorized=is_authorized,
            ),
            loop,
        )
        try:
            return future.result(timeout=max(0.0, deadline - time.monotonic()))
        except TimeoutError:
            future.cancel()
            raise TimeoutError("recursive child runtime acquisition deadline exceeded") from None

    return create


async def _acquire_child_runtime(
    *,
    loop: asyncio.AbstractEventLoop,
    platform: SandboxPlatform,
    admission: DaytonaAdmission,
    volume_id: str,
    mount_path: str,
    workspace_id: UUID,
    run_id: UUID,
    call_index: int,
    deadline: float,
    execution_timeout_s: int,
    execution_output_cap: int,
    is_authorized: Callable[[], bool] | None = None,
) -> ChildRuntimeLease:
    """
    Acquire a disposable runtime lease for a recursive child execution.

    Parameters:
        call_index (int): The child call index used to derive its volume subpath.
        deadline (float): The time limit for acquiring admission.
        is_authorized (Callable[[], bool] | None): Optional callback used to verify authorization during acquisition.

    Returns:
        ChildRuntimeLease: The child interpreter lease and its associated sandbox and cleanup lifecycle.
    """
    _require_authorized(is_authorized)
    permit = await admission.acquire(deadline=deadline)
    sandbox: Any | None = None
    sandbox_id: str | None = None
    subpath = recursive_child_volume_subpath(workspace_id, run_id, call_index)
    try:
        _require_authorized(is_authorized)
        async with asyncio.timeout_at(deadline):
            sandbox = await platform.create(
                volume_id=volume_id,
                mount_path=mount_path,
                volume_subpath=subpath,
                labels={"fleet.runtime": "recursive-child"},
                ephemeral=True,
            )
        sandbox_id = _sandbox_id(sandbox)
        child_sandbox_id = sandbox_id
        _require_authorized(is_authorized)
        interpreter = DaytonaCodeInterpreter(
            backend=sandbox_backend(sandbox, loop=loop, timeout_s=execution_timeout_s),
            execution_output_cap=execution_output_cap,
        )

        def close() -> None:
            """Release the child runtime and its associated resources."""
            _close_child_runtime_sync(
                loop=loop,
                platform=platform,
                sandbox=sandbox,
                sandbox_id=child_sandbox_id,
                mount_path=mount_path,
                interpreter=interpreter,
                permit=permit,
            )

        return ChildRuntimeLease(interpreter, child_sandbox_id, volume_id, subpath, close)
    except BaseException:
        try:
            await _cleanup_after_failed_acquire(platform, sandbox, sandbox_id, permit)
        except BaseException as cleanup_error:
            raise ChildRuntimeCleanupError("recursive child cleanup failed") from cleanup_error
        raise


def _close_child_runtime_sync(
    *,
    loop: asyncio.AbstractEventLoop,
    platform: SandboxPlatform,
    sandbox: Any,
    sandbox_id: str,
    mount_path: str,
    interpreter: DaytonaCodeInterpreter,
    permit: DaytonaAdmissionPermit,
) -> None:
    """
    Close a child runtime and release its associated sandbox resources.

    Raises:
        ChildRuntimeCleanupError: If interpreter shutdown or resource cleanup fails.
    """
    first_error: BaseException | None = None
    try:
        interpreter.shutdown(strict_broker_cleanup=True)
    except BaseException as exc:
        first_error = exc
    try:
        asyncio.run_coroutine_threadsafe(
            _cleanup_child_runtime_async(
                platform=platform,
                sandbox=sandbox,
                sandbox_id=sandbox_id,
                mount_path=mount_path,
                permit=permit,
            ),
            loop,
        ).result()
    except BaseException as exc:
        if first_error is None:
            first_error = exc
    if first_error is not None:
        raise ChildRuntimeCleanupError("recursive child cleanup failed") from first_error


async def _cleanup_after_failed_acquire(
    platform: SandboxPlatform,
    sandbox: Any | None,
    sandbox_id: str | None,
    permit: DaytonaAdmissionPermit,
) -> None:
    """
    Clean up resources allocated during a failed child-runtime acquisition.

    Parameters:
        platform (SandboxPlatform): Platform used to delete the sandbox.
        sandbox (Any | None): Partially created sandbox, if available.
        sandbox_id (str | None): Validated sandbox identifier, if available.
        permit (DaytonaAdmissionPermit): Admission permit to release after cleanup.
    """
    try:
        if sandbox is not None:
            await platform.delete(sandbox_id if sandbox_id is not None else sandbox)
    finally:
        permit.release()


async def _cleanup_child_runtime_async(
    *,
    platform: SandboxPlatform,
    sandbox: Any,
    sandbox_id: str,
    mount_path: str,
    permit: DaytonaAdmissionPermit,
) -> None:
    """
    Clean up a child runtime's files and sandbox, then release its admission permit.

    Parameters:
        platform (SandboxPlatform): Platform used to delete the sandbox.
        sandbox (Any): Sandbox whose mounted files are purged.
        sandbox_id (str): Identifier of the sandbox to delete.
        mount_path (str): Root path under which regular files are removed.
        permit (DaytonaAdmissionPermit): Admission permit to release after cleanup.

    Raises:
        BaseException: The first error encountered while purging files or deleting the sandbox.
    """
    first_error: BaseException | None = None
    try:
        try:
            await _purge_regular_files(sandbox, mount_path)
        except BaseException as exc:
            first_error = exc
        try:
            await platform.delete(sandbox_id)
        except BaseException as exc:
            if first_error is None:
                first_error = exc
    finally:
        permit.release()
    if first_error is not None:
        raise first_error


async def _purge_regular_files(sandbox: Any, mount_path: str) -> None:
    """
    Delete regular files contained within the specified mount path.

    Parameters:
        sandbox (Any): Sandbox providing file listing and deletion operations.
        mount_path (str): Root path whose regular files should be deleted.
    """
    root = PurePosixPath(mount_path)
    entries = await sandbox.fs.list_files(str(root), depth=64)
    for entry in entries:
        path = getattr(entry, "path", None)
        if not isinstance(path, str) or bool(getattr(entry, "is_dir", False)):
            continue
        candidate = PurePosixPath(path)
        try:
            candidate.relative_to(root)
        except ValueError:
            continue
        await sandbox.fs.delete_file(path)


def _sandbox_id(sandbox: Any) -> str:
    """Extract and validate the identifier of a recursive child sandbox.

    Parameters:
        sandbox (Any): Sandbox object whose identifier is required.

    Returns:
        str: The nonempty sandbox identifier.

    Raises:
        RuntimeError: If the sandbox does not have a nonempty string identifier.
    """
    value = getattr(sandbox, "id", None)
    if not isinstance(value, str) or not value:
        raise RuntimeError("recursive child sandbox is missing an id")
    return value


def _require_authorized(is_authorized: Callable[[], bool] | None) -> None:
    """Ensure the current turn remains authorized when an authorization callback is provided.

    Parameters:
        is_authorized (Callable[[], bool] | None): Callback that reports whether the turn is still authorized.

    Raises:
        ChildRuntimeAuthorizationError: If the callback reports that the turn is no longer authorized.
    """
    if is_authorized is not None and not is_authorized():
        raise ChildRuntimeAuthorizationError("Turn is no longer authorized")


__all__ = [
    "ChildRuntimeAuthorizationError",
    "ChildRuntimeCleanupError",
    "ChildRuntimeFactory",
    "ChildRuntimeLease",
    "build_child_runtime_factory",
]
