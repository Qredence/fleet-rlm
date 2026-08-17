"""Dedicated disposable Daytona runtimes for native DSPy recursive children."""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable
from concurrent.futures import Future, wait
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from threading import Lock, Thread
from typing import Any
from uuid import UUID

from fleet_rlm.daytona.dspy_sync_bridge import SyncBridgeDispatcher
from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, sandbox_backend
from fleet_rlm.daytona.lifecycle import AbsenceOutcome, confirm_absence
from fleet_rlm.daytona.provisioning import SandboxPlatform, recursive_child_volume_subpath
from fleet_rlm.daytona.session_manager import (
    DaytonaAdmission,
    DaytonaAdmissionPermit,
    DaytonaAdmissionTimeoutError,
)
from fleet_rlm.rlm.child_runtime import (
    ChildRuntimeAuthorizationError,
    ChildRuntimeCleanupError,
    ChildRuntimeFactory,
)

_CHILD_CLEANUP_RESULT_TIMEOUT_S = 60.0
# Absence-confirmation budget for one deleted ephemeral child Sandbox. Kept
# distinctly larger than the close-path result timeout so a quarantined
# (retained, still-running) cleanup coroutine normally confirms within its own
# budget instead of dying unclassified with the loop.
_CHILD_DELETE_CONFIRM_TIMEOUT_S = 120.0
_CHILD_DELETE_CONFIRM_POLL_S = 1.0


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

    late_lock = Lock()
    late_cleanup: set[Future[Any]] = set()
    late_cleanup_error: BaseException | None = None

    def record_late_cleanup_error(exc: BaseException) -> None:
        nonlocal late_cleanup_error
        with late_lock:
            if late_cleanup_error is None:
                late_cleanup_error = exc

    def _late_cleanup_state() -> tuple[BaseException | None, bool]:
        """Observe completed futures before exposing cleanup ownership as settled."""
        nonlocal late_cleanup_error
        with late_lock:
            for future in tuple(late_cleanup):
                if not future.done():
                    continue
                try:
                    error = future.exception()
                except BaseException as exc:
                    error = exc
                if error is not None and late_cleanup_error is None:
                    late_cleanup_error = error
                late_cleanup.discard(future)
            return late_cleanup_error, any(not future.done() for future in late_cleanup)

    def retain_late_cleanup(cleanup: Future[Any]) -> None:
        with late_lock:
            late_cleanup.add(cleanup)

        def settled(done: Future[Any]) -> None:
            try:
                error = done.exception()
            except BaseException as exc:
                record_late_cleanup_error(exc)
            else:
                if error is not None:
                    record_late_cleanup_error(error)
            with late_lock:
                late_cleanup.discard(done)

        cleanup.add_done_callback(settled)

    def adopt_late_acquisition(acquisition: Future[ChildRuntimeLease]) -> None:
        """Close a late lease from a synchronous callback, independent of loop lifetime."""
        marker: Future[None] = Future()
        retain_late_cleanup(marker)

        def close_late(done: Future[ChildRuntimeLease]) -> None:
            try:
                lease = done.result()
            except ChildRuntimeCleanupError as exc:
                record_late_cleanup_error(exc)
                marker.set_result(None)
                return
            except BaseException:
                marker.set_result(None)
                return

            def close() -> None:
                try:
                    lease.close()
                except BaseException as close_error:
                    record_late_cleanup_error(close_error)
                finally:
                    marker.set_result(None)

            thread = Thread(target=close, name="fleet-late-child-cleanup", daemon=True)
            try:
                thread.start()
            except BaseException as thread_error:
                record_late_cleanup_error(thread_error)
                # A thread-start failure has no safe asynchronous owner left;
                # make one best-effort synchronous close before recording the
                # marker so admission cleanup is still attempted.
                close()

        acquisition.add_done_callback(close_late)

    def raise_if_cleanup_failed() -> None:
        error, pending = _late_cleanup_state()
        if error is not None:
            raise ChildRuntimeCleanupError("recursive child cleanup failed") from error
        if pending:
            raise ChildRuntimeCleanupError("recursive child cleanup is still pending")

    def wait_owned() -> None:
        """Wait for late acquisition cleanup under a bounded quarantine window."""
        wait_deadline = time.monotonic() + max(_CHILD_CLEANUP_RESULT_TIMEOUT_S, 1.0)
        while True:
            with late_lock:
                pending = tuple(future for future in late_cleanup if not future.done())
            if not pending:
                break
            remaining = max(0.0, wait_deadline - time.monotonic())
            _, still_pending = wait(pending, timeout=remaining)
            if still_pending:
                record_late_cleanup_error(TimeoutError("recursive child cleanup quarantine timed out"))
                break
        raise_if_cleanup_failed()

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
            retain_pending_cleanup=retain_late_cleanup,
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
            adopt_late_acquisition(acquisition)
            raise TimeoutError("recursive child runtime acquisition deadline exceeded") from None

    class Factory:
        """Callable child-runtime factory with late-acquisition ownership."""

        def __call__(self, call_index: int) -> ChildRuntimeLease:
            return create(call_index)

        def wait_owned(self) -> None:
            wait_owned()

        def raise_if_cleanup_failed(self) -> None:
            raise_if_cleanup_failed()

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
            backend=sandbox_backend(sandbox, loop=loop, dispatcher=dispatcher, timeout_s=execution_timeout_s),
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
                retain_pending_cleanup=retain_pending_cleanup,
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
    retain_pending_cleanup: Callable[[Future[Any]], None] | None = None,
) -> None:
    """
    Close a child runtime and release its associated sandbox resources.

    Raises:
        ChildRuntimeCleanupError: If interpreter shutdown or resource cleanup fails.
    """
    first_error: BaseException | None = None

    def schedule_cleanup() -> tuple[Future[None], Any | None]:
        cleanup = _cleanup_child_runtime_async(
            platform=platform,
            sandbox=sandbox,
            sandbox_id=sandbox_id,
            mount_path=mount_path,
            permit=permit,
        )
        try:
            return asyncio.run_coroutine_threadsafe(cleanup, loop), cleanup
        except BaseException:
            cleanup.close()

        # The owner loop may close during late acquisition handoff. Run the
        # provider cleanup on a disposable loop so the permit's ``finally``
        # still executes even when run_coroutine_threadsafe is unavailable.
        fallback: Future[None] = Future()

        def run_fallback_cleanup() -> None:
            try:
                asyncio.run(
                    _cleanup_child_runtime_async(
                        platform=platform,
                        sandbox=sandbox,
                        sandbox_id=sandbox_id,
                        mount_path=mount_path,
                        permit=permit,
                    )
                )
            except BaseException as exc:
                if not fallback.done():
                    fallback.set_exception(exc)
            else:
                if not fallback.done():
                    fallback.set_result(None)

        fallback_thread = Thread(target=run_fallback_cleanup, name="fleet-child-cleanup-fallback", daemon=True)
        try:
            fallback_thread.start()
        except BaseException as exc:
            # A thread-start failure cannot safely run provider I/O. Release
            # admission synchronously and surface the unresolved deletion.
            with contextlib.suppress(BaseException):
                permit.release()
            fallback.set_exception(exc)
        return fallback, None

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
            shutdown_result.result(timeout=_CHILD_CLEANUP_RESULT_TIMEOUT_S)
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
                        cleanup_future.result(timeout=_CHILD_CLEANUP_RESULT_TIMEOUT_S)
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
            future.result(timeout=_CHILD_CLEANUP_RESULT_TIMEOUT_S)
        except TimeoutError as exc:
            if future is not None and retain_pending_cleanup is not None:
                retain_pending_cleanup(future)
            elif future is not None:
                future.cancel()
                if cleanup_coroutine is not None:
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
    confirm: Callable[..., Awaitable[AbsenceOutcome]] = confirm_absence,
    confirm_timeout_s: float = _CHILD_DELETE_CONFIRM_TIMEOUT_S,
    confirm_poll_interval_s: float = _CHILD_DELETE_CONFIRM_POLL_S,
) -> None:
    """
    Clean up a child runtime's files and sandbox, then release its admission permit.

    The admission slot stays owned by this coroutine until the provider
    confirms the ephemeral Sandbox is absent, not merely deletion-requested
    (QRE-151). Confirmation runs even when the delete request itself failed:
    the Sandbox may be absent already, or deletion may have been accepted
    provider-side despite the client error. A non-absent classified outcome is
    an explicit quarantine failure surfaced as :class:`ChildRuntimeCleanupError`
    rather than a silently released permit; close-path timeouts retain the
    still-running coroutine, so late confirmation keeps ownership until it
    settles.

    Parameters:
        platform (SandboxPlatform): Platform used to delete the sandbox and to
            probe confirmed absence (``platform.get`` returns ``None`` on
            explicit not-found).
        sandbox (Any): Sandbox whose mounted files are purged.
        sandbox_id (str): Identifier of the sandbox to delete.
        mount_path (str): Root path under which regular files are removed.
        permit (DaytonaAdmissionPermit): Admission permit to release after cleanup.
        confirm (Callable[..., Awaitable[AbsenceOutcome]]): Absence confirmation
            policy seam; defaults to :func:`fleet_rlm.daytona.lifecycle.confirm_absence`.
        confirm_timeout_s (float): Confirmation budget in seconds.
        confirm_poll_interval_s (float): Delay between absence probes in seconds.

    Raises:
        BaseException: The first error encountered while purging files,
        deleting the sandbox, or confirming its absence.
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
        outcome = await confirm(
            probe=platform.get,
            sandbox_id=sandbox_id,
            timeout_s=confirm_timeout_s,
            poll_interval_s=confirm_poll_interval_s,
        )
        if not outcome.absent and first_error is None:
            first_error = ChildRuntimeCleanupError(
                "recursive child Sandbox deletion was not confirmed absent "
                f"(sandbox_id={sandbox_id!r}, outcome={outcome!r})"
            )
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
