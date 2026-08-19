"""Cleanup orchestration for disposable native recursive-child runtimes."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable, Coroutine
from concurrent.futures import Future
from pathlib import PurePosixPath
from threading import Thread
from typing import Any

from fleet_rlm.daytona.lifecycle import AbsenceOutcome, confirm_absence
from fleet_rlm.daytona.provisioning import SandboxPlatform
from fleet_rlm.daytona.sandbox_lease import SandboxLease, SandboxLeasePolicy, schedule_owned_close
from fleet_rlm.daytona.session_manager import DaytonaAdmissionPermit
from fleet_rlm.rlm.child_runtime import ChildRuntimeCleanupError

CHILD_CLEANUP_RESULT_TIMEOUT_S = 60.0
CHILD_DELETE_CONFIRM_TIMEOUT_S = 120.0
CHILD_DELETE_CONFIRM_POLL_S = 1.0
# Cleanup ownership must retain cancellation and process-level shutdown
# signals while avoiding a bare BaseException handler in each branch.
_CLEANUP_EXCEPTIONS = (Exception, asyncio.CancelledError, KeyboardInterrupt, SystemExit)


def close_child_runtime_sync(
    *,
    loop: Any,
    platform: SandboxPlatform,
    sandbox: Any,
    sandbox_id: str,
    mount_path: str,
    interpreter: Any,
    permit: DaytonaAdmissionPermit,
    retain_pending_cleanup: Callable[[Future[Any]], None] | None = None,
    cleanup_result_timeout_s: float = CHILD_CLEANUP_RESULT_TIMEOUT_S,
    cleanup_child_runtime: Callable[..., Coroutine[Any, Any, None]] | None = None,
    confirm_timeout_s: float = CHILD_DELETE_CONFIRM_TIMEOUT_S,
    confirm_poll_interval_s: float = CHILD_DELETE_CONFIRM_POLL_S,
) -> None:
    """
    Shutdown the interpreter and complete provider cleanup for a recursive child runtime.

    Parameters:
        loop (Any): Event loop used to schedule asynchronous cleanup.
        platform (SandboxPlatform): Sandbox provider platform.
        sandbox (Any): Sandbox instance to clean up.
        sandbox_id (str): Identifier of the sandbox.
        mount_path (str): POSIX mount path whose files are purged during cleanup.
        interpreter (Any): Interpreter to shut down.
        permit (DaytonaAdmissionPermit): Admission permit released after cleanup.
        retain_pending_cleanup (Callable[[Future[Any]], None] | None): Callback
            for retaining cleanup that exceeds its timeout.
        cleanup_result_timeout_s (float): Maximum time to wait for shutdown or cleanup results.
        cleanup_child_runtime (Callable[..., Coroutine[Any, Any, None]] | None): Optional cleanup implementation.
        confirm_timeout_s (float): Maximum time to wait for provider deletion confirmation.
        confirm_poll_interval_s (float): Interval between provider deletion checks.

    Raises:
        ChildRuntimeCleanupError: If shutdown or cleanup fails, or cleanup cannot be completed within the timeout.
    """
    first_error: BaseException | None = None
    cleanup_fn = cleanup_child_runtime if cleanup_child_runtime is not None else cleanup_child_runtime_async

    def schedule_cleanup() -> tuple[Future[None], Any | None]:
        """
        Schedule asynchronous child-runtime cleanup.

        Returns:
                tuple[Future[None], Any | None]: The cleanup future and an optional coroutine handle.
        """
        execution = schedule_owned_close(
            loop=loop,
            build=lambda: cleanup_fn(
                platform=platform,
                sandbox=sandbox,
                sandbox_id=sandbox_id,
                mount_path=mount_path,
                permit=permit,
                confirm_timeout_s=confirm_timeout_s,
                confirm_poll_interval_s=confirm_poll_interval_s,
            ),
            fallback_owner_release=permit.release,
            thread_name="fleet-child-cleanup-fallback",
        )
        return execution.future, execution.coroutine

    shutdown_result: Future[None] = Future()
    deferred_cleanup = False

    def run_shutdown() -> None:
        try:
            interpreter.shutdown(strict_broker_cleanup=True)
        except _CLEANUP_EXCEPTIONS as exc:
            shutdown_result.set_exception(exc)
        else:
            shutdown_result.set_result(None)

    shutdown_thread = Thread(target=run_shutdown, name="fleet-child-interpreter-shutdown", daemon=True)
    try:
        shutdown_thread.start()
    except _CLEANUP_EXCEPTIONS as exc:
        first_error = exc
    else:
        try:
            shutdown_result.result(timeout=cleanup_result_timeout_s)
        except TimeoutError as exc:
            # A synchronous broker/provider shutdown cannot be force-cancelled.
            # Quarantine the remainder under the factory owner instead of
            # blocking the child worker or releasing its permit early.
            marker: Future[None] | None = None
            if retain_pending_cleanup is not None:
                marker = Future()
                retain_pending_cleanup(marker)

            def finish_quarantine() -> None:
                """
                Completes quarantined runtime shutdown and cleanup, signaling the
                pending completion marker when all work finishes.
                """
                quarantine_error: BaseException | None = None
                marker_pending = False
                try:
                    shutdown_result.result()
                except _CLEANUP_EXCEPTIONS as shutdown_error:
                    quarantine_error = shutdown_error
                try:
                    cleanup_future, cleanup_coroutine = schedule_cleanup()
                    try:
                        cleanup_future.result(timeout=cleanup_result_timeout_s)
                    except TimeoutError:
                        marker_pending = marker is not None

                        def finish_marker(done: Future[None]) -> None:
                            """
                            Completes the cleanup marker with the quarantine or cleanup error, if any.

                            Parameters:
                                done (Future[None]): Future whose completion status determines the cleanup result.
                            """
                            error = quarantine_error
                            try:
                                cleanup_error = done.exception()
                            except _CLEANUP_EXCEPTIONS as done_error:
                                cleanup_error = done_error
                            if error is None:
                                error = cleanup_error
                            if marker is not None:
                                if error is None:
                                    marker.set_result(None)
                                else:
                                    marker.set_exception(error)

                        cleanup_future.add_done_callback(finish_marker)
                    except _CLEANUP_EXCEPTIONS:
                        cleanup_future.cancel()
                        if cleanup_coroutine is not None:
                            with contextlib.suppress(BaseException):
                                cleanup_coroutine.close()
                        raise
                except _CLEANUP_EXCEPTIONS as cleanup_error:
                    quarantine_error = quarantine_error or cleanup_error
                if marker is not None and not marker_pending:
                    if quarantine_error is None:
                        marker.set_result(None)
                    else:
                        marker.set_exception(quarantine_error)

            quarantine_thread = Thread(
                target=finish_quarantine,
                name="fleet-child-runtime-quarantine",
                daemon=True,
            )
            try:
                quarantine_thread.start()
            except _CLEANUP_EXCEPTIONS as thread_error:
                if marker is not None:
                    marker.set_exception(thread_error)
                deferred_cleanup = True
                first_error = exc
            else:
                deferred_cleanup = True
                first_error = exc
        except _CLEANUP_EXCEPTIONS as exc:
            first_error = exc

    if not deferred_cleanup:
        future: Future[None] | None = None
        cleanup_coroutine: Any | None = None
        try:
            future, cleanup_coroutine = schedule_cleanup()
            future.result(timeout=cleanup_result_timeout_s)
        except TimeoutError as exc:
            if future is not None and retain_pending_cleanup is not None:
                retain_pending_cleanup(future)
            elif future is not None:
                future.cancel()
                if cleanup_coroutine is not None:
                    with contextlib.suppress(BaseException):
                        cleanup_coroutine.close()
            first_error = first_error or exc
        except _CLEANUP_EXCEPTIONS as exc:
            first_error = first_error or exc
            if future is not None:
                future.cancel()
            if cleanup_coroutine is not None:
                with contextlib.suppress(BaseException):
                    cleanup_coroutine.close()

    if first_error is not None:
        raise ChildRuntimeCleanupError("recursive child cleanup failed") from first_error


async def cleanup_after_failed_acquire(
    platform: SandboxPlatform,
    sandbox: Any | None,
    sandbox_id: str | None,
    permit: DaytonaAdmissionPermit,
) -> None:
    """Delete any partially acquired sandbox and release the admission permit."""
    try:
        if sandbox is not None:
            await platform.delete(sandbox_id if sandbox_id is not None else sandbox)
    finally:
        permit.release()


async def cleanup_child_runtime_async(
    *,
    platform: SandboxPlatform,
    sandbox: Any,
    sandbox_id: str,
    mount_path: str,
    permit: DaytonaAdmissionPermit,
    confirm: Callable[..., Awaitable[AbsenceOutcome]] = confirm_absence,
    confirm_timeout_s: float = CHILD_DELETE_CONFIRM_TIMEOUT_S,
    confirm_poll_interval_s: float = CHILD_DELETE_CONFIRM_POLL_S,
    purge: Callable[[Any, str], Awaitable[None]] | None = None,
) -> None:
    """
    Purge and delete a recursive-child sandbox, confirm its provider-side absence, and release its admission permit.

    Parameters:
        sandbox_id (str): Identifier of the sandbox being cleaned up.
        mount_path (str): POSIX mount path whose regular files are purged.
        confirm (Callable): Function used to confirm provider-side sandbox absence.
        confirm_timeout_s (float): Maximum time allowed for absence confirmation.
        confirm_poll_interval_s (float): Interval between absence confirmation checks.
        purge (Callable | None): Optional function used to purge files from the sandbox.

    Raises:
        ChildRuntimeCleanupError: If cleanup fails or provider-side absence is not confirmed.
    """
    purge_fn = purge if purge is not None else purge_regular_files
    lease = SandboxLease(
        kind="recursive_child",
        sandbox=sandbox,
        sandbox_id=sandbox_id,
        platform=platform,
        permit=permit,
        purge=lambda sandbox: purge_fn(sandbox, mount_path),
        policy=SandboxLeasePolicy(
            kind="recursive_child",
            interpreter_shutdown=False,
            confirm_timeout_s=confirm_timeout_s,
            confirm_poll_interval_s=confirm_poll_interval_s,
            confirm_fn=confirm,
        ),
    )
    receipt = await lease.aclose()
    if receipt.first_error is not None or not receipt.provider.confirmed_absent:
        raise ChildRuntimeCleanupError(
            "recursive child Sandbox cleanup failed "
            f"(sandbox_id={sandbox_id!r}, provider_error={receipt.provider.error!r}, "
            f"first_error={receipt.first_error!r}, quarantined={receipt.quarantine.quarantined})"
        )


async def purge_regular_files(sandbox: Any, mount_path: str) -> None:
    """
    Delete contained files and directories under a POSIX mount path.

    Parameters:
        sandbox (Any): Sandbox whose filesystem is being cleaned.
        mount_path (str): Root path whose contents should be deleted.

    Files are deleted before directories, and entries outside the mount path or without a valid path are ignored.
    """
    root = PurePosixPath(mount_path)
    entries = await sandbox.fs.list_files(str(root), depth=None)
    files: list[PurePosixPath] = []
    directories: list[PurePosixPath] = []
    for entry in entries:
        path = getattr(entry, "path", None)
        if not isinstance(path, str):
            continue
        candidate = PurePosixPath(path)
        try:
            relative = candidate.relative_to(root)
        except ValueError:
            continue
        if not relative.parts:
            continue
        if bool(getattr(entry, "is_dir", False)):
            directories.append(candidate)
        else:
            files.append(candidate)

    for path in files:
        await sandbox.fs.delete_file(str(path))
    for path in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        await sandbox.fs.delete_file(str(path), recursive=True)


__all__ = [
    "CHILD_CLEANUP_RESULT_TIMEOUT_S",
    "CHILD_DELETE_CONFIRM_POLL_S",
    "CHILD_DELETE_CONFIRM_TIMEOUT_S",
    "cleanup_after_failed_acquire",
    "cleanup_child_runtime_async",
    "close_child_runtime_sync",
    "purge_regular_files",
]
