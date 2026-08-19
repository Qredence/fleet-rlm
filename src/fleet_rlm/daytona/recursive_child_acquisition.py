"""Acquisition of disposable native recursive-child runtimes."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import Future
from typing import Any
from uuid import UUID

from fleet_rlm.daytona.provisioning import SandboxPlatform, recursive_child_volume_subpath
from fleet_rlm.daytona.session_manager import DaytonaAdmission
from fleet_rlm.rlm.child_runtime import ChildRuntimeAuthorizationError, ChildRuntimeCleanupError
from fleet_rlm.runtime.owned_effect import OwnedEffect

from .recursive_child_lease import ChildRuntimeLease


async def acquire_child_runtime(
    *,
    loop: asyncio.AbstractEventLoop,
    dispatcher: Any = None,
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
    retain_pending_cleanup: Callable[[Future[Any]], None] | None = None,
    interpreter_factory: Callable[..., Any],
    sandbox_backend_factory: Callable[..., Any],
    close_child_runtime: Callable[..., None],
    cleanup_after_failed_acquire: Callable[..., Any],
    sandbox_id_for_fn: Callable[[Any], str] | None = None,
    require_authorized_fn: Callable[[Callable[[], bool] | None], None] | None = None,
) -> ChildRuntimeLease:
    """
    Acquire an ephemeral runtime for executing a recursive child operation.
    
    Parameters:
        volume_id (str): Identifier of the volume mounted in the child sandbox.
        mount_path (str): Path where the volume is mounted.
        workspace_id (UUID): Workspace containing the recursive child.
        run_id (UUID): Run containing the recursive child.
        call_index (int): Index identifying the recursive child call.
        deadline (float): Absolute event-loop time by which acquisition must complete.
        execution_timeout_s (int): Maximum execution time for the child runtime.
        execution_output_cap (int): Maximum output retained from child execution.
    
    Returns:
        ChildRuntimeLease: Lease containing the child interpreter, sandbox metadata, and cleanup callback.
    
    Raises:
        ChildRuntimeAuthorizationError: If the owning turn is no longer authorized.
        ChildRuntimeCleanupError: If acquisition fails and cleanup also fails.
    """
    sandbox_id_resolver = sandbox_id_for if sandbox_id_for_fn is None else sandbox_id_for_fn
    authorization_check = require_authorized if require_authorized_fn is None else require_authorized_fn
    authorization_check(is_authorized)
    permit = await admission.acquire(deadline=deadline)
    sandbox: Any | None = None
    sandbox_id: str | None = None
    subpath = recursive_child_volume_subpath(workspace_id, run_id, call_index)
    try:
        authorization_check(is_authorized)
        async with asyncio.timeout_at(deadline):
            sandbox = await platform.create(
                volume_id=volume_id,
                mount_path=mount_path,
                volume_subpath=subpath,
                labels={"fleet.runtime": "recursive-child"},
                ephemeral=True,
            )
        sandbox_id = sandbox_id_resolver(sandbox)
        child_sandbox_id = sandbox_id
        authorization_check(is_authorized)
        interpreter = interpreter_factory(
            backend=sandbox_backend_factory(
                sandbox,
                loop=loop,
                dispatcher=dispatcher,
                timeout_s=execution_timeout_s,
            ),
            execution_output_cap=execution_output_cap,
        )

        def close() -> None:
            """Close the child runtime and release its associated resources."""
            close_child_runtime(
                loop=loop,
                platform=platform,
                sandbox=sandbox,
                sandbox_id=child_sandbox_id,
                mount_path=mount_path,
                interpreter=interpreter,
                permit=permit,
                retain_pending_cleanup=retain_pending_cleanup,
            )

        return ChildRuntimeLease(interpreter, child_sandbox_id, volume_id, subpath, close)
    except BaseException:
        try:
            cleanup = OwnedEffect.start(cleanup_after_failed_acquire(platform, sandbox, sandbox_id, permit))
            await cleanup.settle()
        except BaseException as cleanup_error:
            raise ChildRuntimeCleanupError("recursive child cleanup failed") from cleanup_error
        raise


def sandbox_id_for(sandbox: Any) -> str:
    """Extract and validate the identifier of a provider Sandbox."""
    value = getattr(sandbox, "id", None)
    if not isinstance(value, str) or not value:
        raise RuntimeError("recursive child sandbox is missing an id")
    return value


def require_authorized(is_authorized: Callable[[], bool] | None) -> None:
    """
    Ensure the owning turn remains authorized.
    
    Parameters:
    	is_authorized (Callable[[], bool] | None): Authorization callback, or None to skip the check.
    
    Raises:
    	ChildRuntimeAuthorizationError: If the callback reports that the owning turn is no longer authorized.
    """
    if is_authorized is not None and not is_authorized():
        raise ChildRuntimeAuthorizationError("Turn is no longer authorized")


__all__ = ["acquire_child_runtime", "require_authorized", "sandbox_id_for"]
