"""Contracted owner for native DSPy recursive child runtimes (P39)."""

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

from fleet_rlm.daytona.admission import (
    DaytonaAdmission,
    DaytonaAdmissionPermit,
    DaytonaAdmissionTimeoutError,
)
from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, sandbox_backend
from fleet_rlm.daytona.lifecycle import AbsenceOutcome, confirm_absence
from fleet_rlm.daytona.provisioning import (
    DaytonaEnvironmentProfile,
    SandboxPlatform,
    recursive_child_volume_subpath,
)
from fleet_rlm.daytona.sandbox import schedule_owned_close
from fleet_rlm.daytona.sync_bridge import SyncBridgeDispatcher
from fleet_rlm.rlm.recursion import (
    ChildRuntimeAuthorizationError,
    ChildRuntimeCleanupError,
    ChildRuntimeFactory,
)
from fleet_rlm.runtime.owned_effect import OwnedEffect

_CLEANUP_EXCEPTIONS = (Exception, asyncio.CancelledError, KeyboardInterrupt, SystemExit)

CHILD_CLEANUP_RESULT_TIMEOUT_S = 60.0
CHILD_DELETE_CONFIRM_TIMEOUT_S = 120.0
CHILD_DELETE_CONFIRM_POLL_S = 1.0
_CHILD_CLEANUP_RESULT_TIMEOUT_S = CHILD_CLEANUP_RESULT_TIMEOUT_S

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
    """One synchronously usable child interpreter and its owned cleanup action."""

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
        with self._condition:
            return self._state

    @property
    def close_error(self) -> BaseException | None:
        with self._condition:
            return self._close_error

    def close(self) -> None:
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
        self._lock = Lock()
        self._pending: set[Future[Any]] = set()
        self._error: BaseException | None = None
        self._wait_timeout_s = wait_timeout_s

    def _record_error(self, exc: BaseException) -> None:
        with self._lock:
            if self._error is None:
                self._error = exc

    def _state(self) -> tuple[BaseException | None, bool]:
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
        if marker.done():
            return
        if error is None:
            marker.set_result(None)
        else:
            marker.set_exception(error)

    def retain(self, future: Future[Any]) -> None:
        with self._lock:
            self._pending.add(future)

        def settled(done: Future[Any]) -> None:
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
        marker: Future[None] = Future()
        self.retain(marker)

        def close_late(done: Future[Any]) -> None:
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
        error, pending = self._state()
        if error is not None:
            raise ChildRuntimeCleanupError("recursive child cleanup failed") from error
        if pending:
            raise ChildRuntimeCleanupError("recursive child cleanup is still pending")

    def wait_owned(self) -> None:
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
    mount_path: str | None,
    interpreter: Any,
    permit: DaytonaAdmissionPermit,
    retain_pending_cleanup: Callable[[Future[Any]], None] | None = None,
    cleanup_result_timeout_s: float = CHILD_CLEANUP_RESULT_TIMEOUT_S,
    cleanup_child_runtime: Callable[..., Coroutine[Any, Any, None]] | None = None,
    confirm_timeout_s: float = CHILD_DELETE_CONFIRM_TIMEOUT_S,
    confirm_poll_interval_s: float = CHILD_DELETE_CONFIRM_POLL_S,
) -> None:
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
            marker: Future[None] | None = None
            if retain_pending_cleanup is not None:
                marker = Future()
                retain_pending_cleanup(marker)

            def complete_marker(error: BaseException | None) -> None:
                if marker is None or marker.done():
                    return
                if error is None:
                    marker.set_result(None)
                else:
                    marker.set_exception(error)

            def finish_quarantine() -> None:
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
                            err = quarantine_error
                            try:
                                cleanup_err = done.exception()
                            except _CLEANUP_EXCEPTIONS as done_err:
                                cleanup_err = done_err
                            complete_marker(err or cleanup_err)

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
        raise ChildRuntimeCleanupError(f"recursive child cleanup failed: {first_error!r}") from first_error


async def cleanup_after_failed_acquire(
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


async def cleanup_child_runtime_async(
    *,
    platform: SandboxPlatform,
    sandbox: Any,
    sandbox_id: str,
    mount_path: str | None,
    permit: DaytonaAdmissionPermit,
    confirm: Callable[..., Awaitable[AbsenceOutcome]] | None = None,
    confirm_timeout_s: float = CHILD_DELETE_CONFIRM_TIMEOUT_S,
    confirm_poll_interval_s: float = CHILD_DELETE_CONFIRM_POLL_S,
    purge: Callable[[Any, str], Awaitable[None]] | None = None,
) -> None:
    purge_fn = purge or purge_regular_files
    try:
        if mount_path:
            await purge_fn(sandbox, mount_path)
        delete_error: Exception | None = None
        try:
            await platform.delete(sandbox_id)
        except Exception as exc:
            delete_error = exc
        confirm_fn: Any = confirm or confirm_absence
        try:
            outcome = await confirm_fn(
                probe=platform.get,
                sandbox_id=sandbox_id,
                timeout_s=confirm_timeout_s,
                poll_interval_s=confirm_poll_interval_s,
            )
        except TypeError:
            outcome = await confirm_fn(
                platform=platform,
                sandbox_id=sandbox_id,
                timeout_s=confirm_timeout_s,
                poll_interval_s=confirm_poll_interval_s,
            )
        is_absent = bool(getattr(outcome, "confirmed_absent", False) or getattr(outcome, "absent", False))
        if delete_error is not None:
            raise ChildRuntimeCleanupError(
                f"failed to delete child sandbox {sandbox_id}: {delete_error}"
            ) from delete_error
        if not is_absent:
            raise ChildRuntimeCleanupError(
                f"absence unconfirmed: recursive child sandbox deletion not confirmed absent: {sandbox_id}"
            )
    except ChildRuntimeCleanupError:
        raise
    except Exception as exc:
        raise ChildRuntimeCleanupError(f"cleanup failed: {exc}") from exc
    finally:
        permit.release()


async def purge_regular_files(sandbox: Any, mount_path: str) -> None:
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
    volume_id: str | None,
    mount_path: str | None,
    profile: DaytonaEnvironmentProfile = DaytonaEnvironmentProfile.WORKSPACE_CHILD,
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
    sandbox_id_resolver = sandbox_id_for_fn or sandbox_id_for
    authorization_check = require_authorized_fn or require_authorized
    authorization_check(is_authorized)
    if not isinstance(profile, DaytonaEnvironmentProfile):
        profile = DaytonaEnvironmentProfile(str(profile))
    semantic = profile is DaytonaEnvironmentProfile.SEMANTIC_CHILD
    if not semantic and (not volume_id or not mount_path):
        raise ValueError("WorkspaceChild requires a Volume binding")
    permit = await admission.acquire(deadline=deadline)
    sandbox: Any | None = None
    sandbox_id: str | None = None
    subpath = "" if semantic else recursive_child_volume_subpath(workspace_id, run_id, call_index)
    try:
        authorization_check(is_authorized)
        async with asyncio.timeout_at(deadline):
            labels = {"fleet.runtime": "recursive-child"}
            if semantic:
                labels["fleet.profile"] = profile.value
            create_kwargs: dict[str, Any] = {
                "profile": profile,
                "volume_id": None if semantic else volume_id,
                "mount_path": None if semantic else mount_path,
                "volume_subpath": None if semantic else subpath,
                "labels": labels,
                "with_volume": not semantic,
                "ephemeral": True,
            }
            if semantic:
                create_kwargs["network_block_all"] = True
            sandbox = await platform.create(**create_kwargs)
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
            close_child_runtime(
                loop=loop,
                platform=platform,
                sandbox=sandbox,
                sandbox_id=child_sandbox_id,
                mount_path=None if semantic else (mount_path or ""),
                interpreter=interpreter,
                permit=permit,
                retain_pending_cleanup=retain_pending_cleanup,
            )

        return ChildRuntimeLease(
            interpreter,
            child_sandbox_id,
            "" if semantic else (volume_id or ""),
            subpath,
            close,
        )
    except BaseException:
        try:
            cleanup = OwnedEffect.start(cleanup_after_failed_acquire(platform, sandbox, sandbox_id, permit))
            await cleanup.settle()
        except BaseException as cleanup_error:
            raise ChildRuntimeCleanupError("recursive child cleanup failed") from cleanup_error
        raise


def sandbox_id_for(sandbox: Any) -> str:
    value = getattr(sandbox, "id", None)
    if not isinstance(value, str) or not value:
        raise RuntimeError("recursive child sandbox is missing an id")
    return value


def require_authorized(is_authorized: Callable[[], bool] | None) -> None:
    if is_authorized is not None and not is_authorized():
        raise ChildRuntimeAuthorizationError("Turn is no longer authorized")


_acquire_child_runtime = acquire_child_runtime


def build_child_runtime_factory(
    *,
    loop: asyncio.AbstractEventLoop,
    dispatcher: SyncBridgeDispatcher | None = None,
    platform: SandboxPlatform,
    admission: DaytonaAdmission,
    volume_id: str | None,
    mount_path: str | None,
    workspace_id: UUID,
    run_id: UUID,
    deadline: float,
    execution_timeout_s: int,
    execution_output_cap: int,
    is_authorized: Callable[[], bool] | None = None,
    profile: DaytonaEnvironmentProfile = DaytonaEnvironmentProfile.WORKSPACE_CHILD,
    semantic_child_available: bool = True,
    semantic_child_fallback: bool = False,
) -> ChildRuntimeFactory:
    late_owner = LateCleanupOwner(wait_timeout_s=_CHILD_CLEANUP_RESULT_TIMEOUT_S)

    def create(
        call_index: int,
        *,
        selected_profile: DaytonaEnvironmentProfile | str | None = None,
    ) -> ChildRuntimeLease:
        chosen_profile = selected_profile if selected_profile is not None else profile
        if not isinstance(chosen_profile, DaytonaEnvironmentProfile):
            chosen_profile = DaytonaEnvironmentProfile(str(chosen_profile))
        if chosen_profile is DaytonaEnvironmentProfile.SEMANTIC_CHILD and not semantic_child_available:
            if not semantic_child_fallback:
                raise ValueError("SemanticChild requires FLEET_DAYTONA_CHILD_SNAPSHOT")
            chosen_profile = DaytonaEnvironmentProfile.WORKSPACE_CHILD
        acquisition_coroutine = _acquire_child_runtime(
            loop=loop,
            dispatcher=dispatcher,
            platform=platform,
            admission=admission,
            volume_id=volume_id,
            mount_path=mount_path,
            profile=chosen_profile,
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
            late_owner.adopt_late_acquisition(acquisition, lambda lease: lease.close())
            raise TimeoutError("recursive child runtime acquisition deadline exceeded") from None

    class Factory:
        def __call__(
            self,
            call_index: int,
            *,
            profile: DaytonaEnvironmentProfile | str | None = None,
        ) -> ChildRuntimeLease:
            return create(call_index, selected_profile=profile)

        def wait_owned(self) -> None:
            late_owner.wait_owned()

        def raise_if_cleanup_failed(self) -> None:
            late_owner.raise_if_failed()

    return Factory()


def _close_child_runtime_sync(
    *,
    loop: asyncio.AbstractEventLoop,
    platform: SandboxPlatform,
    sandbox: Any,
    sandbox_id: str,
    mount_path: str | None,
    interpreter: Any,
    permit: Any,
    retain_pending_cleanup: Callable[[Future[Any]], None] | None = None,
) -> None:
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
