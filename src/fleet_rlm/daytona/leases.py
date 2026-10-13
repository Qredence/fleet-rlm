"""Existing Daytona admission, lease records, and confirmed cleanup."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
import time
from collections.abc import Awaitable, Callable, Coroutine
from concurrent.futures import Future
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import PurePosixPath
from threading import Lock
from typing import TYPE_CHECKING, Any, Literal, Protocol, TypeAlias
from uuid import UUID

from fleet_rlm.daytona.errors import (
    ChildRuntimeCleanupError,
    sanitize_failure_text,
)
from fleet_rlm.daytona.specs import (
    PREWARM_RUN_ID,
    SandboxPlatform,
    sandbox_state,
    state_text,
)
from fleet_rlm.rlm.ownership import OwnedEffect

if TYPE_CHECKING:
    from fleet_rlm.daytona.runtime import DaytonaRuntime


logger = logging.getLogger(__name__)

CHILD_CLEANUP_RESULT_TIMEOUT_S = 60.0


CHILD_DELETE_CONFIRM_TIMEOUT_S = 120.0


CHILD_DELETE_CONFIRM_POLL_S = 1.0


_CLEANUP_EXCEPTIONS = (Exception, asyncio.CancelledError, KeyboardInterrupt, SystemExit)


def _invoke_interpreter_shutdown(interpreter: Any, *, strict_broker_cleanup: bool) -> None:
    """Use the supported interpreter shutdown contract exactly once."""
    interpreter.shutdown(strict_broker_cleanup=strict_broker_cleanup)


class DaytonaAdmissionTimeoutError(RuntimeError):
    """The Turn deadline elapsed before Daytona capacity became available."""


@dataclass(slots=True)
class DaytonaAdmissionPermit:
    """One idempotently releasable slot in Daytona admission."""

    _semaphore: asyncio.BoundedSemaphore
    _loop: asyncio.AbstractEventLoop | None = None
    _execution_semaphore: asyncio.BoundedSemaphore | None = None
    _released: bool = field(default=False, init=False)
    _release_lock: Lock = field(default_factory=Lock, init=False, repr=False)

    def release(self) -> None:
        """Release on the semaphore's owning loop, safely from worker threads."""
        with self._release_lock:
            if self._released:
                return
            self._released = True
        loop = self._loop or getattr(self._semaphore, "_loop", None)
        if loop is None or loop.is_closed() or not loop.is_running():
            self._semaphore.release()
            if self._execution_semaphore is not None:
                self._execution_semaphore.release()
            return
        try:
            current = asyncio.get_running_loop()
        except RuntimeError:
            current = None
        if current is loop:
            self._semaphore.release()
            if self._execution_semaphore is not None:
                self._execution_semaphore.release()
            return
        try:
            loop.call_soon_threadsafe(self._semaphore.release)
            if self._execution_semaphore is not None:
                loop.call_soon_threadsafe(self._execution_semaphore.release)
        except RuntimeError:
            self._semaphore.release()
            if self._execution_semaphore is not None:
                self._execution_semaphore.release()


class DaytonaAdmission:
    """Bound acquiring plus active Interpreter Leases for one process."""

    def __init__(self, *, max_active_leases: int = 8) -> None:
        if max_active_leases <= 0:
            raise ValueError("max_active_leases must be positive")
        if max_active_leases > 8:
            raise ValueError("max_active_leases must be at most 8")
        self.max_active_leases = max_active_leases
        self._semaphore = asyncio.BoundedSemaphore(max_active_leases)
        self._execution_semaphore = asyncio.BoundedSemaphore(max(1, max_active_leases - 1))

    async def acquire(self, *, deadline: float, host_io: bool = False) -> DaytonaAdmissionPermit:
        execution_acquired = False
        try:
            async with asyncio.timeout_at(deadline):
                if not host_io:
                    await self._execution_semaphore.acquire()
                    execution_acquired = True
                await self._semaphore.acquire()
        except TimeoutError:
            if execution_acquired:
                self._execution_semaphore.release()
            raise DaytonaAdmissionTimeoutError("Daytona admission unavailable") from None
        except BaseException:
            if execution_acquired:
                self._execution_semaphore.release()
            raise
        return DaytonaAdmissionPermit(
            self._semaphore,
            asyncio.get_running_loop(),
            self._execution_semaphore if execution_acquired else None,
        )


DeletionPhase = Literal["requested", "deleting", "absent", "failed"]


_ABSENT_STATES = frozenset({"destroyed", "deleted"})


_DELETING_STATES = frozenset({"destroying", "deleting", "archiving", "stopping"})


_FAILED_STATES = frozenset({"error", "build_failed"})


DEFAULT_CONFIRM_TIMEOUT_S = 60.0


DEFAULT_POLL_INTERVAL_S = 1.0


class DeletionStateProbe(Protocol):
    """Caller-owned one-Sandbox lookup; returns ``None`` on explicit not-found."""

    def __call__(self, sandbox_id: str) -> Awaitable[Any | None]: ...


def _lifecycle_raw_state(target: Any) -> str:
    raw = getattr(target, "state", None)
    if raw is None:
        raw = getattr(target, "status", None)
    return state_text(raw)


def classify_deletion_phase(raw_state: Any) -> DeletionPhase:
    """Map one raw provider state string (or enum) onto the public phase model."""
    text = state_text(raw_state)
    if text in _ABSENT_STATES:
        return "absent"
    if text in _DELETING_STATES:
        return "deleting"
    if text in _FAILED_STATES:
        return "failed"
    return "requested"


@dataclass(frozen=True, slots=True)
class AbsenceConfirmation:
    """Confirmed: the provider reports the Sandbox absent (not-found/destroyed)."""

    sandbox_id: str
    observations: tuple[str, ...]
    duration_s: float
    absent: Literal[True] = True


@dataclass(frozen=True, slots=True)
class AbsenceTimeout:
    """The confirmation budget elapsed without an absent observation."""

    sandbox_id: str
    last_state: str
    observations: tuple[str, ...]
    duration_s: float
    absent: Literal[False] = False


@dataclass(frozen=True, slots=True)
class AbsenceProbeError:
    """A probe call raised, or the provider surfaced a terminal error state."""

    sandbox_id: str
    error: str
    observations: tuple[str, ...]
    duration_s: float
    absent: Literal[False] = False


AbsenceOutcome: TypeAlias = AbsenceConfirmation | AbsenceTimeout | AbsenceProbeError


async def confirm_absence(
    *,
    probe: DeletionStateProbe,
    sandbox_id: str,
    timeout_s: float = DEFAULT_CONFIRM_TIMEOUT_S,
    poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[None]] | None = None,
) -> AbsenceOutcome:
    """Poll ``probe`` until the Sandbox is confirmed absent or the budget closes."""
    if sleep is None:
        sleep = asyncio.sleep
    started = clock()
    observations: list[str] = []

    def note(state: str) -> None:
        if not observations or observations[-1] != state:
            observations.append(state)

    while True:
        try:
            target = await probe(sandbox_id)
        except BaseException as exc:
            if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                raise
            note("probe_error")
            return AbsenceProbeError(
                sandbox_id,
                sanitize_failure_text(exc, max_chars=160),
                tuple(observations),
                clock() - started,
            )
        if target is None:
            note("not_found")
            return AbsenceConfirmation(sandbox_id, tuple(observations), clock() - started)
        state = _lifecycle_raw_state(target) or "unknown"
        note(state)
        phase = classify_deletion_phase(state)
        if phase == "absent":
            return AbsenceConfirmation(sandbox_id, tuple(observations), clock() - started)
        if phase == "failed":
            return AbsenceProbeError(
                sandbox_id,
                f"provider error state: {state}",
                tuple(observations),
                clock() - started,
            )
        if clock() - started >= timeout_s:
            return AbsenceTimeout(sandbox_id, state, tuple(observations), clock() - started)
        await sleep(poll_interval_s)


class LeaseState(StrEnum):
    OPEN = "OPEN"
    CLOSING = "CLOSING"
    CLOSED = "CLOSED"
    FAILED = "FAILED"


class ActiveLeaseConflictError(RuntimeError):
    def __init__(self, session_id: UUID, holder_run_id: UUID | None = None) -> None:
        self.session_id = session_id
        self.holder_run_id = holder_run_id
        super().__init__(f"active lease conflict for session {session_id}")


class DaytonaLeaseAcquisitionTimeoutError(RuntimeError):
    pass


class ActiveLeaseRegistry:
    """Thread-safe mapping of (workspace_id, session_id) to active run_id."""

    def __init__(self) -> None:
        self._holders: dict[tuple[UUID, UUID], UUID] = {}
        self._lock = Lock()

    @staticmethod
    def _key(session_id: UUID, workspace_id: UUID | None) -> tuple[UUID, UUID]:
        return (workspace_id or UUID(int=0), session_id)

    def acquire(self, session_id: UUID, run_id: UUID, *, workspace_id: UUID | None = None) -> None:
        with self._lock:
            key = self._key(session_id, workspace_id)
            existing = self._holders.get(key)
            if existing is not None and (existing != run_id or run_id == PREWARM_RUN_ID):
                raise ActiveLeaseConflictError(session_id, holder_run_id=existing)
            self._holders[key] = run_id

    def release(self, session_id: UUID, run_id: UUID, *, workspace_id: UUID | None = None) -> None:
        with self._lock:
            key = self._key(session_id, workspace_id)
            if self._holders.get(key) == run_id:
                del self._holders[key]

    def holder(self, session_id: UUID, *, workspace_id: UUID | None = None) -> UUID | None:
        with self._lock:
            if workspace_id is not None:
                return self._holders.get(self._key(session_id, workspace_id))
            matches = [run for (ws, sid), run in self._holders.items() if sid == session_id]
            return matches[0] if len(matches) == 1 else None

    def has_session(self, session_id: UUID) -> bool:
        with self._lock:
            return any(sid == session_id for (_ws, sid) in self._holders)


@dataclass(frozen=True, slots=True)
class LeaseRequest:
    session_id: UUID
    user_id: UUID
    workspace_id: UUID
    run_id: UUID | None = None


@dataclass(slots=True)
class InterpreterLease:
    """Handle for an acquired interpreter and its underlying sandbox."""

    sandbox_id: str
    interpreter_id: str
    volume_id: str
    mount_path: str
    interpreter: Any
    session_id: str | None = None
    user_id: str | None = None
    run_id: str | None = None
    workspace_id: str | None = None
    volume_subpath: str | None = None
    created_sandbox: bool = False
    sandbox: Any | None = None
    requires_sandbox_deletion: bool = False
    binding_generation: int = 1
    context_fingerprint: object | None = None
    _provider_retired: bool = False
    _defer_owner_release: bool = False
    _defer_idle_cleanup: bool = False
    _state: LeaseState = LeaseState.OPEN
    _on_release: Callable[[], None] | None = None
    _release_task: asyncio.Task[None] | None = field(default=None, repr=False)
    _release_lock: Lock = field(default_factory=Lock, repr=False)

    @property
    def state(self) -> LeaseState:
        return self._state

    @property
    def closed(self) -> bool:
        return self._state is LeaseState.CLOSED

    @property
    def closing(self) -> bool:
        return self._state is LeaseState.CLOSING

    @property
    def failed(self) -> bool:
        return self._state is LeaseState.FAILED

    def release(self) -> None:
        """Close the interpreter, requesting strict broker cleanup when supported.

        Closed leases are unchanged. Shutdown failures propagate and mark the
        lease failed, allowing a later call to retry. On success, mark it closed
        and invoke the owner callback unless deferred; callback errors are
        suppressed. This method does not retire the underlying Sandbox.
        """
        with self._release_lock:
            if self.closed:
                return
            self._state = LeaseState.CLOSING
            try:
                _invoke_interpreter_shutdown(self.interpreter, strict_broker_cleanup=True)
            except BaseException:
                self._state = LeaseState.FAILED
                raise
            self._state = LeaseState.CLOSED
            if self._on_release is not None and not self._defer_owner_release:
                with contextlib.suppress(BaseException):
                    self._on_release()


class SandboxResource:
    """One remote resource, one retained cleanup task, and retryable failure.

    Cleanup is owned by the application loop. A caller deadline only limits
    its wait; the task continues to own the resource and admission permit.
    """

    def __init__(
        self,
        *,
        owner: DaytonaRuntime,
        sandbox: Any | None,
        sandbox_id: str | None = None,
        platform: SandboxPlatform | None = None,
        permit: DaytonaAdmissionPermit | None = None,
        interpreter: Any | None = None,
        role: Literal["root", "host_io", "fence"] = "root",
        timeout_s: float = 60.0,
    ) -> None:
        self._owner = owner
        self._sandbox = sandbox
        self._sandbox_id = sandbox_id or getattr(sandbox, "id", None)
        self._platform = platform if platform is not None else owner._platform
        self._permit = permit
        self._interpreter = interpreter
        self.role = role
        self.timeout_s = timeout_s
        self._state = LeaseState.OPEN
        self.failure: BaseException | None = None
        self._close_task: asyncio.Task[bool] | None = None
        self._loop = asyncio.get_running_loop()
        owner._retain_sandbox_resource(self)

    @property
    def state(self) -> LeaseState:
        return self._state

    @property
    def closing(self) -> bool:
        return self._state is LeaseState.CLOSING

    @property
    def failed(self) -> bool:
        return self._state is LeaseState.FAILED

    @property
    def has_pending_ownership(self) -> bool:
        return self._state is not LeaseState.CLOSED

    async def _retire(self) -> bool:
        self._state = LeaseState.CLOSING
        self.failure = None
        try:
            if self._interpreter is not None:
                await asyncio.to_thread(_invoke_interpreter_shutdown, self._interpreter, strict_broker_cleanup=True)
                self._interpreter = None
            if self._platform is not None and self._sandbox_id is not None:
                if self.role == "fence":
                    try:
                        await self._platform.stop(self._sandbox_id, timeout=self.timeout_s, force=True)
                    except Exception:
                        target = await self._platform.get(self._sandbox_id)
                        if target is not None and sandbox_state(target) not in {"stopped", "paused", "archived"}:
                            raise
                    target = await self._platform.get(self._sandbox_id)
                    if target is not None and sandbox_state(target) not in {"stopped", "paused", "archived"}:
                        raise RuntimeError("Sandbox fencing is unconfirmed")
                else:
                    await self._platform.delete(self._sandbox_id, wait=True, timeout=self.timeout_s)
                    # Keep the protocol honest for provider implementations:
                    # request acceptance alone never releases capacity.
                    target = await self._platform.get(self._sandbox_id)
                    if target is not None and sandbox_state(target) not in {"destroyed", "deleted"}:
                        raise RuntimeError("Sandbox deletion is unconfirmed")
            if self._permit is not None:
                self._permit.release()
                self._permit = None
            self._state = LeaseState.CLOSED
            self._owner._release_settled_sandbox_resource(self)
            return True
        except BaseException as exc:
            self.failure = exc
            self._state = LeaseState.FAILED
            return False

    async def aclose(self, *, deadline: float | None = None) -> bool:
        if asyncio.get_running_loop() is not self._loop:
            raise RuntimeError("Sandbox cleanup requires its application loop")
        if self._state is LeaseState.CLOSED:
            return True
        task = self._close_task
        if task is None or task.done():
            task = self._loop.create_task(self._retire(), name="fleet-daytona-resource-retirement")
            self._close_task = task
            _retain_provider_task(task, self._owner._provider_tasks)
        async with asyncio.timeout_at(deadline):
            return await asyncio.shield(task)

    async def wait_ownership(self, *, timeout: float | None = None) -> bool:
        deadline = None if timeout is None else self._loop.time() + timeout
        try:
            return await self.aclose(deadline=deadline)
        except TimeoutError:
            return False


@dataclass(slots=True)
class OwnedCloseExecution:
    future: Future[Any]
    coroutine: Any | None = None


def schedule_owned_close(
    *,
    loop: asyncio.AbstractEventLoop,
    build: Callable[[], Coroutine[Any, Any, Any]],
) -> OwnedCloseExecution:
    coroutine = build()
    try:
        future = asyncio.run_coroutine_threadsafe(coroutine, loop)
        return OwnedCloseExecution(future=future, coroutine=coroutine)
    except BaseException as exc:
        if inspect.iscoroutine(coroutine):
            coroutine.close()
        failure: Future[Any] = Future()
        failure.set_exception(exc)
        return OwnedCloseExecution(future=failure)


async def purge_regular_files(sandbox: Any, mount_path: str) -> None:
    """Delete the scope's top-level entries; directories are removed recursively."""
    root = PurePosixPath(mount_path)
    # Only direct children are needed: each directory is deleted recursively.
    entries = await sandbox.fs.list_files(str(root), depth=1)
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
    for path in directories:
        await sandbox.fs.delete_file(str(path), recursive=True)


async def _request_child_deletion(
    platform: SandboxPlatform,
    sandbox: Any,
    sandbox_id: str | None,
    *,
    confirm: Callable[..., Awaitable[AbsenceOutcome]] | None,
    timeout_s: float,
    poll_interval_s: float,
) -> tuple[AbsenceOutcome, Exception | None]:
    """Request deletion once, then independently establish provider absence."""
    delete_error: Exception | None = None
    try:
        # An identified child's absence is confirmed by polling below, so the
        # request does not also block on the SDK's own destroyed-state wait.
        await platform.delete(sandbox_id if sandbox_id is not None else sandbox, wait=sandbox_id is None)
    except Exception as exc:
        delete_error = exc
    if sandbox_id is None:
        # Opaque sandbox handles cannot be polled for absence. A successful
        # delete request is the best-effort confirmation available; a failed
        # delete keeps the admission permit held for retry.
        if delete_error is not None:
            raise ChildRuntimeCleanupError(
                f"failed to delete unidentified child sandbox: {delete_error}"
            ) from delete_error
        outcome: AbsenceOutcome = AbsenceConfirmation(
            sandbox_id="<unidentified>",
            observations=("delete-accepted",),
            duration_s=0.0,
        )
        logger.info("Daytona child deletion: confirmed_absent=True request_failed=False unidentified=True")
        return outcome, None
    outcome = await (confirm or confirm_absence)(
        probe=platform.get, sandbox_id=sandbox_id, timeout_s=timeout_s, poll_interval_s=poll_interval_s
    )
    logger.info(
        "Daytona child deletion: confirmed_absent=%s request_failed=%s", outcome.absent, delete_error is not None
    )
    return outcome, delete_error


async def cleanup_after_failed_acquire(
    platform: SandboxPlatform,
    sandbox: Any | None,
    sandbox_id: str | None,
    permit: DaytonaAdmissionPermit,
    *,
    confirm: Callable[..., Awaitable[AbsenceOutcome]] | None = None,
    confirm_timeout_s: float = CHILD_DELETE_CONFIRM_TIMEOUT_S,
    confirm_poll_interval_s: float = CHILD_DELETE_CONFIRM_POLL_S,
) -> None:
    confirmed_absent = sandbox is None

    async def cleanup() -> None:
        nonlocal confirmed_absent
        if sandbox is None:
            return

        outcome, delete_error = await _request_child_deletion(
            platform,
            sandbox,
            sandbox_id,
            confirm=confirm,
            timeout_s=confirm_timeout_s,
            poll_interval_s=confirm_poll_interval_s,
        )
        is_absent = outcome.absent
        confirmed_absent = is_absent
        if delete_error is not None:
            raise ChildRuntimeCleanupError(
                f"failed to delete child sandbox {sandbox_id}: {delete_error}"
            ) from delete_error
        if not is_absent:
            raise ChildRuntimeCleanupError(f"absence unconfirmed: failed-acquire child sandbox cleanup: {sandbox_id}")

    cleanup_effect = OwnedEffect.start(cleanup())
    try:
        await cleanup_effect.settle()
    finally:
        if confirmed_absent:
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
    confirmed_absent = False
    try:
        if mount_path:
            await purge_fn(sandbox, mount_path)
            if mount_path.startswith("/tmp/fleet/"):
                await sandbox.fs.delete_file(mount_path, recursive=True)
        outcome, delete_error = await _request_child_deletion(
            platform,
            sandbox,
            sandbox_id,
            confirm=confirm,
            timeout_s=confirm_timeout_s,
            poll_interval_s=confirm_poll_interval_s,
        )
        is_absent = outcome.absent
        confirmed_absent = is_absent
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
        if confirmed_absent:
            permit.release()


def close_child_runtime_sync(
    *,
    loop: Any,
    platform: SandboxPlatform,
    sandbox: Any,
    sandbox_id: str,
    mount_path: str | None,
    interpreter: Any,
    permit: DaytonaAdmissionPermit,
    retain_pending_cleanup: Callable[[Future[Any]], None],
    cleanup_result_timeout_s: float = CHILD_CLEANUP_RESULT_TIMEOUT_S,
    cleanup_child_runtime: Callable[..., Coroutine[Any, Any, None]] | None = None,
    confirm_timeout_s: float = CHILD_DELETE_CONFIRM_TIMEOUT_S,
    confirm_poll_interval_s: float = CHILD_DELETE_CONFIRM_POLL_S,
) -> None:
    cleanup_fn = cleanup_child_runtime or cleanup_child_runtime_async

    async def close_on_owner_loop() -> None:
        shutdown_error: BaseException | None = None
        try:
            shutdown = OwnedEffect.start(
                asyncio.to_thread(_invoke_interpreter_shutdown, interpreter, strict_broker_cleanup=True)
            )
            await shutdown.settle()
        except BaseException as exc:
            shutdown_error = exc
        try:
            await cleanup_fn(
                platform=platform,
                sandbox=sandbox,
                sandbox_id=sandbox_id,
                mount_path=mount_path,
                permit=permit,
                confirm_timeout_s=confirm_timeout_s,
                confirm_poll_interval_s=confirm_poll_interval_s,
            )
        except BaseException as exc:
            raise ChildRuntimeCleanupError("recursive child cleanup failed") from exc
        if shutdown_error is not None:
            raise ChildRuntimeCleanupError("recursive child interpreter shutdown failed") from shutdown_error

    execution = schedule_owned_close(loop=loop, build=close_on_owner_loop)
    retain_pending_cleanup(execution.future)
    try:
        execution.future.result(timeout=cleanup_result_timeout_s)
    except _CLEANUP_EXCEPTIONS as exc:
        raise ChildRuntimeCleanupError("recursive child cleanup failed or remains unresolved") from exc


def _retain_provider_task(task: asyncio.Future[Any], owner: set[asyncio.Future[Any]]) -> None:
    owner.add(task)

    def settled(completed: asyncio.Future[Any]) -> None:
        owner.discard(completed)
        if not completed.cancelled():
            with contextlib.suppress(BaseException):
                completed.exception()

    task.add_done_callback(settled)


async def _provider_call(
    awaitable: Awaitable[Any],
    *,
    deadline: float | None,
    operation: str,
    owner: set[asyncio.Future[Any]] | None = None,
) -> Any:
    loop = asyncio.get_running_loop()
    if deadline is not None and deadline <= loop.time():
        close = getattr(awaitable, "close", None)
        if callable(close):
            close()
        raise DaytonaLeaseAcquisitionTimeoutError(f"Daytona {operation} timed out") from None
    task = asyncio.ensure_future(awaitable)
    if deadline is None:
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if owner is not None and not task.done():
                _retain_provider_task(task, owner)
            raise
    try:
        remaining = deadline - loop.time()
        if remaining <= 0:
            if owner is not None and not task.done():
                _retain_provider_task(task, owner)
            raise _ProviderCallDeadlineError(task, operation)
        return await asyncio.wait_for(asyncio.shield(task), timeout=remaining)
    except TimeoutError:
        if task.done():
            return task.result()
        if owner is not None:
            _retain_provider_task(task, owner)
        raise _ProviderCallDeadlineError(task, operation) from None
    except asyncio.CancelledError:
        if owner is not None and not task.done():
            _retain_provider_task(task, owner)
        raise


async def _settle_provider_task(task: asyncio.Future[Any]) -> Any:
    await OwnedEffect.from_task(task).settle()
    return task.result()


class _ProviderCallDeadlineError(TimeoutError):
    def __init__(self, task: asyncio.Future[Any], operation: str) -> None:
        self.task = task
        self.operation = operation
        super().__init__(f"Daytona {operation} timed out")
