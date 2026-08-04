"""Dedicated disposable Daytona runtimes for native DSPy recursive children."""

from __future__ import annotations

import asyncio
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
        """Strictly dispose the interpreter, mounted child scope, sandbox, and permit."""
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
    """Build the worker-thread bridge for one Root Turn's child sandboxes."""

    def create(call_index: int) -> ChildRuntimeLease:
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
        return future.result()

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
    _require_authorized(is_authorized)
    permit = await admission.acquire(deadline=deadline)
    sandbox: Any | None = None
    sandbox_id: str | None = None
    subpath = recursive_child_volume_subpath(workspace_id, run_id, call_index)
    try:
        _require_authorized(is_authorized)
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
    value = getattr(sandbox, "id", None)
    if not isinstance(value, str) or not value:
        raise RuntimeError("recursive child sandbox is missing an id")
    return value


def _require_authorized(is_authorized: Callable[[], bool] | None) -> None:
    if is_authorized is not None and not is_authorized():
        raise ChildRuntimeAuthorizationError("Turn is no longer authorized")


__all__ = [
    "ChildRuntimeAuthorizationError",
    "ChildRuntimeCleanupError",
    "ChildRuntimeFactory",
    "ChildRuntimeLease",
    "build_child_runtime_factory",
]
