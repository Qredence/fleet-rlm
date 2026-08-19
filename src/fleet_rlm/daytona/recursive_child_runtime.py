"""Dedicated disposable Daytona runtimes for native DSPy recursive children."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from concurrent.futures import Future
from typing import Any
from uuid import UUID

from fleet_rlm.daytona.dspy_sync_bridge import SyncBridgeDispatcher
from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, sandbox_backend
from fleet_rlm.daytona.provisioning import SandboxPlatform
from fleet_rlm.daytona.recursive_child_acquisition import (
    acquire_child_runtime,
    require_authorized,
    sandbox_id_for,
)
from fleet_rlm.daytona.recursive_child_cleanup import (
    CHILD_CLEANUP_RESULT_TIMEOUT_S,
    cleanup_after_failed_acquire,
    cleanup_child_runtime_async,
    close_child_runtime_sync,
    purge_regular_files,
)
from fleet_rlm.daytona.recursive_child_cleanup import (
    CHILD_DELETE_CONFIRM_POLL_S as _CHILD_DELETE_CONFIRM_POLL_S,
)
from fleet_rlm.daytona.recursive_child_cleanup import (
    CHILD_DELETE_CONFIRM_TIMEOUT_S as _CHILD_DELETE_CONFIRM_TIMEOUT_S,
)
from fleet_rlm.daytona.recursive_child_late import LateCleanupOwner
from fleet_rlm.daytona.recursive_child_lease import ChildRuntimeLease, ChildRuntimeLeaseState
from fleet_rlm.daytona.session_manager import (
    DaytonaAdmission,
    DaytonaAdmissionTimeoutError,
)
from fleet_rlm.rlm.child_runtime import (
    ChildRuntimeAuthorizationError,
    ChildRuntimeCleanupError,
    ChildRuntimeFactory,
)

_CHILD_CLEANUP_RESULT_TIMEOUT_S = CHILD_CLEANUP_RESULT_TIMEOUT_S


# Absence-confirmation budget for one deleted ephemeral child Sandbox. Kept
# distinctly larger than the close-path result timeout so a quarantined
# (retained, still-running) cleanup coroutine normally confirms within its own
# budget instead of dying unclassified with the loop.
def build_child_runtime_factory(
    *,
    loop: asyncio.AbstractEventLoop,
    dispatcher: SyncBridgeDispatcher | None = None,
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
    Build a factory for acquiring disposable child-runtime leases for recursive calls.
    
    The factory waits until the configured deadline for each acquisition and retains
    late acquisitions for cleanup.
    
    Parameters:
        volume_id (str): Identifier of the volume mounted in child runtimes.
        mount_path (str): Mount path used by child runtimes.
        workspace_id (UUID): Identifier of the workspace owning the runtimes.
        run_id (UUID): Identifier of the root turn run.
        deadline (float): Monotonic acquisition deadline.
        execution_timeout_s (int): Maximum execution time for each child runtime.
        execution_output_cap (int): Maximum output size for each child runtime.
        is_authorized (Callable[[], bool] | None): Optional callback that determines
            whether child-runtime creation remains authorized.
    
    Returns:
        ChildRuntimeFactory: A callable factory that accepts a recursive call index
            and returns its leased child runtime.
    """

    late_owner = LateCleanupOwner(wait_timeout_s=_CHILD_CLEANUP_RESULT_TIMEOUT_S)

    def create(call_index: int) -> ChildRuntimeLease:
        """
        Acquire a disposable child runtime lease for a recursive call.

        Parameters:
            call_index (int): Index identifying the recursive child call.

        Returns:
            ChildRuntimeLease: Lease for the acquired child runtime.
        """
        acquisition_coroutine = _acquire_child_runtime(
            loop=loop,
            dispatcher=dispatcher,
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
            retain_pending_cleanup=late_owner.retain,
        )
        try:
            acquisition = asyncio.run_coroutine_threadsafe(acquisition_coroutine, loop)
        except BaseException as exc:
            acquisition_coroutine.close()
            raise ChildRuntimeCleanupError("recursive child runtime acquisition failed") from exc
        try:
            return acquisition.result(timeout=max(0.0, deadline - time.monotonic()))
        except DaytonaAdmissionTimeoutError:
            raise TimeoutError("recursive child runtime acquisition deadline exceeded") from None
        except TimeoutError:
            # The provider future can complete in the race between result()
            # timing out and this handler.  Adopt it unconditionally: a late
            # exception is harmless, while a late lease must still be closed.
            # Do not cancel provider work that may already have crossed the
            # acquisition boundary or its Sandbox/permit could be orphaned.
            late_owner.adopt_late_acquisition(acquisition, lambda lease: lease.close())
            raise TimeoutError("recursive child runtime acquisition deadline exceeded") from None

    class Factory:
        """Callable child-runtime factory with late-acquisition ownership."""

        def __call__(self, call_index: int) -> ChildRuntimeLease:
            """
            Create a disposable child-runtime lease for a recursive call.
            
            Parameters:
                call_index (int): Index identifying the recursive call.
            
            Returns:
                ChildRuntimeLease: Lease for the acquired child runtime.
            """
            return create(call_index)

        def wait_owned(self) -> None:
            """Wait for all retained cleanup operations to finish."""
            late_owner.wait_owned()

        def raise_if_cleanup_failed(self) -> None:
            """Raise a deferred cleanup error if any late cleanup operation failed."""
            late_owner.raise_if_failed()

    return Factory()


async def _acquire_child_runtime(
    *,
    loop: asyncio.AbstractEventLoop,
    dispatcher: SyncBridgeDispatcher | None = None,
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
) -> ChildRuntimeLease:
    """Compatibility wrapper that supplies the current provider seams."""
    return await acquire_child_runtime(
        loop=loop,
        dispatcher=dispatcher,
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
        retain_pending_cleanup=retain_pending_cleanup,
        interpreter_factory=DaytonaCodeInterpreter,
        sandbox_backend_factory=sandbox_backend,
        close_child_runtime=_close_child_runtime_sync,
        cleanup_after_failed_acquire=_cleanup_after_failed_acquire,
        sandbox_id_for_fn=_sandbox_id,
        require_authorized_fn=_require_authorized,
    )


def _close_child_runtime_sync(
    *,
    loop: asyncio.AbstractEventLoop,
    platform: SandboxPlatform,
    sandbox: Any,
    sandbox_id: str,
    mount_path: str,
    interpreter: Any,
    permit: Any,
    retain_pending_cleanup: Callable[[Future[Any]], None] | None = None,
) -> None:
    """Compatibility wrapper preserving runtime-patched timeout policy."""
    close_child_runtime_sync(
        loop=loop,
        platform=platform,
        sandbox=sandbox,
        sandbox_id=sandbox_id,
        mount_path=mount_path,
        interpreter=interpreter,
        permit=permit,
        retain_pending_cleanup=retain_pending_cleanup,
        cleanup_result_timeout_s=_CHILD_CLEANUP_RESULT_TIMEOUT_S,
        cleanup_child_runtime=_cleanup_child_runtime_async,
        confirm_timeout_s=_CHILD_DELETE_CONFIRM_TIMEOUT_S,
        confirm_poll_interval_s=_CHILD_DELETE_CONFIRM_POLL_S,
    )


async def _cleanup_after_failed_acquire(
    platform: SandboxPlatform,
    sandbox: Any | None,
    sandbox_id: str | None,
    permit: Any,
) -> None:
    """Compatibility wrapper for failed-acquisition cleanup."""
    await cleanup_after_failed_acquire(platform, sandbox, sandbox_id, permit)


async def _cleanup_child_runtime_async(
    *,
    platform: SandboxPlatform,
    sandbox: Any,
    sandbox_id: str,
    mount_path: str,
    permit: Any,
    confirm: Callable[..., Any] | None = None,
    confirm_timeout_s: float = _CHILD_DELETE_CONFIRM_TIMEOUT_S,
    confirm_poll_interval_s: float = _CHILD_DELETE_CONFIRM_POLL_S,
) -> None:
    """
    Clean up a child runtime and optionally confirm its sandbox deletion.
    
    Parameters:
        sandbox_id (str): Identifier of the sandbox to clean up.
        mount_path (str): Path whose regular files are purged during cleanup.
        permit (Any): Authorization permit for the cleanup operation.
        confirm (Callable[..., Any] | None): Optional callback used to confirm sandbox deletion.
        confirm_timeout_s (float): Maximum time to wait for deletion confirmation.
        confirm_poll_interval_s (float): Interval between deletion confirmation checks.
    """
    kwargs: dict[str, Any] = {
        "platform": platform,
        "sandbox": sandbox,
        "sandbox_id": sandbox_id,
        "mount_path": mount_path,
        "permit": permit,
        "confirm_timeout_s": confirm_timeout_s,
        "confirm_poll_interval_s": confirm_poll_interval_s,
        "purge": _purge_regular_files,
    }
    if confirm is not None:
        kwargs["confirm"] = confirm
    await cleanup_child_runtime_async(**kwargs)


async def _purge_regular_files(sandbox: Any, mount_path: str) -> None:
    """Preserve the runtime-patchable regular-file purge seam."""
    await purge_regular_files(sandbox, mount_path)


def _sandbox_id(sandbox: Any) -> str:
    """
    Extracts the validated identifier from a sandbox.
    
    Parameters:
    	sandbox (Any): Sandbox whose identifier is retrieved.
    
    Returns:
    	str: The sandbox identifier.
    """
    return sandbox_id_for(sandbox)


def _require_authorized(is_authorized: Callable[[], bool] | None) -> None:
    """Preserve the runtime-patchable authorization seam."""
    require_authorized(is_authorized)


__all__ = [
    "ChildRuntimeAuthorizationError",
    "ChildRuntimeCleanupError",
    "ChildRuntimeFactory",
    "ChildRuntimeLease",
    "ChildRuntimeLeaseState",
    "build_child_runtime_factory",
]
