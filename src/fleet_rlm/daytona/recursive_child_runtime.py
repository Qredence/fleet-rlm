"""Contracted owner for native DSPy recursive child runtimes (P39).

One child-runtime owner concentrates the complete lease/cleanup contract for
recursive children (P36 rows P39-REC-002..006, absorbed behind this owner):

- acquisition with admission reservation and authorization fences
  (former ``recursive_child_acquisition``);
- the explicit single-owner, joinable, re-observable lease close state
  (former ``recursive_child_lease``);
- strict interpreter/broker shutdown, scope purge, provider deletion with
  confirmed absence, and admission restoration (former
  ``recursive_child_cleanup``);
- late-acquisition adoption and quarantined cleanup ownership that survives
  owner-loop and dispatch loss (former ``recursive_child_late``);
- the factory seam that binds those obligations to one absolute deadline.

Cleanup law: interpreter close -> broker stop -> scope purge -> Sandbox
delete -> confirmed absence -> admission restore, and only then may the
parent succeed.  Any step failure fails the child and the Root (fail-closed)
while the remaining steps are still attempted.  DSPy never shuts down these
caller-owned child interpreters; only this owner does, exactly once per
lease.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable, Coroutine
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import PurePosixPath
from threading import Condition, Lock, Thread, get_ident
from typing import Any
from uuid import UUID

from fleet_rlm.daytona.dspy_sync_bridge import SyncBridgeDispatcher
from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, sandbox_backend
from fleet_rlm.daytona.lifecycle import AbsenceOutcome, confirm_absence
from fleet_rlm.daytona.provisioning import SandboxPlatform, recursive_child_volume_subpath
from fleet_rlm.daytona.sandbox_lease import SandboxLease, SandboxLeasePolicy, schedule_owned_close
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
from fleet_rlm.runtime.owned_effect import OwnedEffect

# Cleanup ownership must retain cancellation and process-level shutdown
# signals while avoiding a bare BaseException handler in each branch.
_CLEANUP_EXCEPTIONS = (Exception, asyncio.CancelledError, KeyboardInterrupt, SystemExit)

CHILD_CLEANUP_RESULT_TIMEOUT_S = 60.0
CHILD_DELETE_CONFIRM_TIMEOUT_S = 120.0
CHILD_DELETE_CONFIRM_POLL_S = 1.0
# Read at call time by the factory and close seams so fault-injection lanes
# can shorten the result timeout through this module attribute.
_CHILD_CLEANUP_RESULT_TIMEOUT_S = CHILD_CLEANUP_RESULT_TIMEOUT_S

# Absence-confirmation budget policy: distinctly larger than the close-path
# result timeout so a quarantined (retained, still-running) cleanup coroutine
# normally confirms within its own budget instead of dying unclassified with
# the loop.
_FALLBACK_CLEANUP_EXECUTOR = ThreadPoolExecutor(
    max_workers=1,
    thread_name_prefix="fleet-late-child-cleanup-fallback",
)
_QUARANTINE_FALLBACK_EXECUTOR = ThreadPoolExecutor(
    max_workers=1,
    thread_name_prefix="fleet-child-runtime-quarantine-fallback",
)


class ChildRuntimeLeaseState(StrEnum):
    """States observed by callers of a child runtime lease."""

    OPEN = "OPEN"
    CLOSING = "CLOSING"
    CLOSED = "CLOSED"
    FAILED = "FAILED"


@dataclass(slots=True)
class ChildRuntimeLease:
    """One synchronously usable child interpreter and its owned cleanup action.

    ``FAILED`` is an explicit terminal observation for the close attempt. A
    later caller re-observes the same failure rather than starting a second
    provider cleanup, while callers that arrive during ``CLOSING`` join the
    one in-flight close operation.
    """

    interpreter: Any
    sandbox_id: str
    volume_id: str
    volume_subpath: str
    _close: Callable[[], None] = field(repr=False)
    _state: ChildRuntimeLeaseState = field(default=ChildRuntimeLeaseState.OPEN, init=False, repr=False)
    _close_error: BaseException | None = field(default=None, init=False, repr=False)
    _condition: Condition = field(default_factory=Condition, init=False, repr=False)
    _closing_thread_id: int | None = field(default=None, init=False, repr=False)

    @property
    def state(self) -> ChildRuntimeLeaseState:
        """
        Expose the lease's current lifecycle state.

        Returns:
                ChildRuntimeLeaseState: The current lease state.
        """
        with self._condition:
            return self._state

    @property
    def close_error(self) -> BaseException | None:
        """Return the terminal close error, if the lease is ``FAILED``."""
        with self._condition:
            return self._close_error

    def close(self) -> None:
        """Close the child runtime lease exactly once.

        Concurrent callers wait for an in-progress close and observe its result. Cleanup
        failures are retained and re-raised by subsequent callers.

        Raises:
            RuntimeError: If cleanup is invoked recursively by the closing thread.
            BaseException: The exception raised by the cleanup callback.
        """
        with self._condition:
            if self._state is ChildRuntimeLeaseState.CLOSED:
                return
            if self._state is ChildRuntimeLeaseState.CLOSING:
                if self._closing_thread_id == get_ident():
                    raise RuntimeError("recursive child lease close is not reentrant")
                while self._state is ChildRuntimeLeaseState.CLOSING:
                    self._condition.wait()
                if self._state is ChildRuntimeLeaseState.CLOSED:
                    return
                if self._state is ChildRuntimeLeaseState.FAILED:
                    error = self._close_error
                    if error is None:
                        raise RuntimeError("recursive child lease close failed")
                    raise error
            if self._state is ChildRuntimeLeaseState.FAILED:
                error = self._close_error
                if error is None:
                    raise RuntimeError("recursive child lease close failed")
                raise error
            self._state = ChildRuntimeLeaseState.CLOSING
            self._closing_thread_id = get_ident()

        try:
            self._close()
        except BaseException as exc:
            with self._condition:
                self._close_error = exc
                self._state = ChildRuntimeLeaseState.FAILED
                self._closing_thread_id = None
                self._condition.notify_all()
            raise
        else:
            with self._condition:
                self._state = ChildRuntimeLeaseState.CLOSED
                self._closing_thread_id = None
                self._condition.notify_all()


class LateCleanupOwner:
    """Keep late provider work owned until its cleanup future settles."""

    def __init__(self, *, wait_timeout_s: float) -> None:
        """Initialize an owner for tracking late cleanup work.

        Parameters:
                wait_timeout_s (float): Maximum time to wait for owned cleanup work to finish.
        """
        self._lock = Lock()
        self._pending: set[Future[Any]] = set()
        self._error: BaseException | None = None
        self._wait_timeout_s = wait_timeout_s

    def _record_error(self, exc: BaseException) -> None:
        """Record the first cleanup error observed by this owner."""
        with self._lock:
            if self._error is None:
                self._error = exc

    def _state(self) -> tuple[BaseException | None, bool]:
        """
        Collect completed work and report the first recorded error and whether work remains pending.

        Returns:
            tuple[BaseException | None, bool]: The first cleanup error, if any, and whether unfinished work remains.
        """
        with self._lock:
            for future in tuple(self._pending):
                if not future.done():
                    continue
                try:
                    error = future.exception()
                except _CLEANUP_EXCEPTIONS as exc:
                    error = exc
                if error is not None and self._error is None:
                    self._error = error
                self._pending.discard(future)
            return self._error, any(not future.done() for future in self._pending)

    @staticmethod
    def _complete(marker: Future[None], error: BaseException | None = None) -> None:
        """
        Completes a marker future with a result or an exception.

        Parameters:
                marker (Future[None]): The future to complete.
                error (BaseException | None): The exception to assign to the
                    future, or `None` to complete it successfully.
        """
        if marker.done():
            return
        if error is None:
            marker.set_result(None)
        else:
            marker.set_exception(error)

    def retain(self, future: Future[Any]) -> None:
        """Retain one future and observe its terminal exception."""
        with self._lock:
            self._pending.add(future)

        def settled(done: Future[Any]) -> None:
            """
            Record a completed future's failure and release it from pending ownership.

            Parameters:
                done (Future[Any]): The completed future whose terminal state is observed.
            """
            try:
                error = done.exception()
            except _CLEANUP_EXCEPTIONS as exc:
                self._record_error(exc)
            else:
                if error is not None:
                    self._record_error(error)
            with self._lock:
                self._pending.discard(done)

        future.add_done_callback(settled)

    def adopt_late_acquisition(
        self,
        acquisition: Future[Any],
        close_lease: Callable[[Any], None],
    ) -> None:
        """Adopt a late lease for cleanup outside the event loop.

        Parameters:
            acquisition (Future[Any]): Future resolving to the lease to close.
            close_lease (Callable[[Any], None]): Function that closes the acquired lease.

        ChildRuntimeCleanupError and lease-closing failures are recorded for later reporting.
        """
        marker: Future[None] = Future()
        self.retain(marker)

        def close_late(done: Future[Any]) -> None:
            """Handle a completed late acquisition and arrange independent lease cleanup.

            Parameters:
                done (Future[Any]): Completed future containing the acquired lease or an acquisition error.
            """
            try:
                lease = done.result()
            except ChildRuntimeCleanupError as exc:
                self._record_error(exc)
                self._complete(marker)
                return
            except _CLEANUP_EXCEPTIONS:
                self._complete(marker)
                return

            def close() -> None:
                """
                Close the acquired lease and mark the late cleanup operation complete.
                """
                try:
                    close_lease(lease)
                except _CLEANUP_EXCEPTIONS as exc:
                    self._record_error(exc)
                finally:
                    self._complete(marker)

            thread = Thread(target=close, name="fleet-late-child-cleanup", daemon=True)
            try:
                thread.start()
            except _CLEANUP_EXCEPTIONS as exc:
                self._record_error(exc)
                try:
                    _FALLBACK_CLEANUP_EXECUTOR.submit(close)
                except _CLEANUP_EXCEPTIONS as dispatch_error:
                    self._record_error(dispatch_error)
                    self._complete(marker, dispatch_error)

        acquisition.add_done_callback(close_late)

    def raise_if_failed(self) -> None:
        """Raise an observable late-cleanup error or pending-ownership error."""
        error, pending = self._state()
        if error is not None:
            raise ChildRuntimeCleanupError("recursive child cleanup failed") from error
        if pending:
            raise ChildRuntimeCleanupError("recursive child cleanup is still pending")

    def wait_owned(self) -> None:
        """
        Wait for retained cleanup work until the bounded ownership window expires.

        Raises:
            ChildRuntimeCleanupError: If cleanup fails or remains pending after the wait.
        """
        wait_deadline = time.monotonic() + max(self._wait_timeout_s, 1.0)
        while True:
            with self._lock:
                pending = tuple(future for future in self._pending if not future.done())
            if not pending:
                break
            remaining = max(0.0, wait_deadline - time.monotonic())
            _, still_pending = wait(pending, timeout=remaining)
            if still_pending:
                self._record_error(TimeoutError("recursive child cleanup quarantine timed out"))
                break
        self.raise_if_failed()


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
                            complete_marker(error)

                        cleanup_future.add_done_callback(finish_marker)
                    except _CLEANUP_EXCEPTIONS:
                        cleanup_future.cancel()
                        if cleanup_coroutine is not None:
                            with contextlib.suppress(BaseException):
                                cleanup_coroutine.close()
                        raise
                except _CLEANUP_EXCEPTIONS as cleanup_error:
                    quarantine_error = quarantine_error or cleanup_error
                if not marker_pending:
                    complete_marker(quarantine_error)

            def complete_marker(error: BaseException | None) -> None:
                if marker is None or marker.done():
                    return
                if error is None:
                    marker.set_result(None)
                else:
                    marker.set_exception(error)

            quarantine_thread = Thread(
                target=finish_quarantine,
                name="fleet-child-runtime-quarantine",
                daemon=True,
            )
            try:
                quarantine_thread.start()
            except _CLEANUP_EXCEPTIONS:
                try:
                    _QUARANTINE_FALLBACK_EXECUTOR.submit(finish_quarantine)
                except _CLEANUP_EXCEPTIONS as dispatch_error:
                    complete_marker(dispatch_error)
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
    confirm: Callable[..., Awaitable[AbsenceOutcome]] | None = None,
    confirm_timeout_s: float = CHILD_DELETE_CONFIRM_TIMEOUT_S,
    confirm_poll_interval_s: float = CHILD_DELETE_CONFIRM_POLL_S,
    purge: Callable[[Any, str], Awaitable[None]] | None = None,
) -> None:
    """
    Purge and delete a recursive-child sandbox, confirm its provider-side absence, and release its admission permit.

    Parameters:
        sandbox_id (str): Identifier of the sandbox being cleaned up.
        mount_path (str): POSIX mount path whose regular files are purged.
        confirm (Callable | None): Function used to confirm provider-side sandbox
            absence; resolved at call time so the owner's seam stays test- and
            fault-injectable.
        confirm_timeout_s (float): Maximum time allowed for absence confirmation.
        confirm_poll_interval_s (float): Interval between absence confirmation checks.
        purge (Callable | None): Optional function used to purge files from the sandbox.

    Raises:
        ChildRuntimeCleanupError: If cleanup fails or provider-side absence is not confirmed.
    """
    purge_fn = purge if purge is not None else purge_regular_files
    confirm_fn = confirm if confirm is not None else confirm_absence
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
            confirm_fn=confirm_fn,
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


# Patchable compatibility seam: tests and live proofs monkeypatch this module-
# level name, so the acquisition call site reads it at call time.
_acquire_child_runtime = acquire_child_runtime


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
            interpreter_factory=DaytonaCodeInterpreter,
            sandbox_backend_factory=sandbox_backend,
            close_child_runtime=_close_child_runtime_sync,
            cleanup_after_failed_acquire=cleanup_after_failed_acquire,
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
    """Patchable close seam: forwards to the canonical cleanup with this
    module's result-timeout policy read at call time."""
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
    )


__all__ = [
    "CHILD_CLEANUP_RESULT_TIMEOUT_S",
    "CHILD_DELETE_CONFIRM_POLL_S",
    "CHILD_DELETE_CONFIRM_TIMEOUT_S",
    "ChildRuntimeAuthorizationError",
    "ChildRuntimeCleanupError",
    "ChildRuntimeFactory",
    "ChildRuntimeLease",
    "ChildRuntimeLeaseState",
    "LateCleanupOwner",
    "acquire_child_runtime",
    "build_child_runtime_factory",
    "cleanup_after_failed_acquire",
    "cleanup_child_runtime_async",
    "close_child_runtime_sync",
    "purge_regular_files",
    "require_authorized",
    "sandbox_id_for",
]
