"""Cleanup orchestration for disposable native recursive-child runtimes."""

from __future__ import annotations

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
    """Shutdown the interpreter, then settle provider cleanup and admission."""
    first_error: BaseException | None = None
    cleanup_fn = cleanup_child_runtime if cleanup_child_runtime is not None else cleanup_child_runtime_async

    def schedule_cleanup() -> tuple[Future[None], Any | None]:
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
        except BaseException as exc:
            shutdown_result.set_exception(exc)
        else:
            shutdown_result.set_result(None)

    shutdown_thread = Thread(target=run_shutdown, name="fleet-child-interpreter-shutdown", daemon=True)
    try:
        shutdown_thread.start()
    except BaseException as exc:
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
                quarantine_error: BaseException | None = None
                marker_pending = False
                try:
                    shutdown_result.result()
                except BaseException as shutdown_error:
                    quarantine_error = shutdown_error
                try:
                    cleanup_future, cleanup_coroutine = schedule_cleanup()
                    try:
                        cleanup_future.result(timeout=cleanup_result_timeout_s)
                    except TimeoutError:
                        marker_pending = marker is not None

                        def finish_marker(done: Future[None]) -> None:
                            error = quarantine_error
                            try:
                                cleanup_error = done.exception()
                            except BaseException as done_error:
                                cleanup_error = done_error
                            if error is None:
                                error = cleanup_error
                            if marker is not None:
                                if error is None:
                                    marker.set_result(None)
                                else:
                                    marker.set_exception(error)

                        cleanup_future.add_done_callback(finish_marker)
                    except BaseException:
                        cleanup_future.cancel()
                        if cleanup_coroutine is not None:
                            with contextlib.suppress(BaseException):
                                cleanup_coroutine.close()
                        raise
                except BaseException as cleanup_error:
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
            except BaseException as thread_error:
                if marker is not None:
                    marker.set_exception(thread_error)
                deferred_cleanup = True
                first_error = exc
            else:
                deferred_cleanup = True
                first_error = exc
        except BaseException as exc:
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
        except BaseException as exc:
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
    """Delete any partially acquired Sandbox before releasing admission."""
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
    """Purge, delete, confirm absence, and release one child lease."""
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
    """Delete all files and child directories contained within one subpath."""
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
