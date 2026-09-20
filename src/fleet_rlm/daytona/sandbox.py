"""Direct Daytona sandbox lifecycle management and session leasing.

Provides clean, robust AsyncDaytona sandbox acquisition, idle-stop lifecycle,
and concurrency control without artificial enterprise leasing layers.
"""

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
from threading import Lock, Thread
from typing import Any, Literal, Protocol, TypeAlias
from uuid import UUID

from fleet_rlm.daytona.admission import DaytonaAdmissionPermit
from fleet_rlm.daytona.errors import sanitize_failure_text
from fleet_rlm.daytona.lifecycle import AbsenceConfirmation, AbsenceOutcome, confirm_absence
from fleet_rlm.daytona.provisioning import SandboxPlatform

logger = logging.getLogger(__name__)

PREWARM_RUN_ID = UUID("00000000-0000-4000-8000-000000000000")
DEFAULT_IDLE_STOP_SECONDS = 300.0
_PREWARM_CLAIM_WAIT_SECONDS = 60.0
DEFAULT_CLOSE_RESULT_TIMEOUT_S = 60.0

LeaseKind: TypeAlias = Literal[
    "interactive_turn",
    "background_batch",
    "retained_session",
    "recovery_fence",
    "volume_io",
]


def _cleanup_failed(value: Any) -> bool:
    if value is None:
        return False
    if value is False:
        return True
    if bool(getattr(value, "failed", False)):
        return True
    if getattr(value, "first_error", None) is not None:
        return True
    quarantine = getattr(value, "quarantine", None)
    return bool(getattr(quarantine, "quarantined", False))


async def await_cleanup(callback: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Invoke sync or async cleanup without changing exception identity."""
    result = callback(*args, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


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


class LeaseCleanupError(RuntimeError):
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
    _released: bool = False
    _provider_retired: bool = False
    _defer_owner_release: bool = False
    _defer_idle_cleanup: bool = False
    _state: LeaseState = LeaseState.OPEN
    _on_release: Callable[[], None] | None = None
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
        with self._release_lock:
            if self._released:
                return
            self._state = LeaseState.CLOSING
            try:
                if hasattr(self.interpreter, "shutdown"):
                    try:
                        self.interpreter.shutdown(strict_broker_cleanup=True)
                    except TypeError:
                        self.interpreter.shutdown()
            except BaseException:
                self._state = LeaseState.FAILED
                raise
            self._released = True
            self._state = LeaseState.CLOSED
            if self._on_release is not None and not self._defer_owner_release:
                with contextlib.suppress(BaseException):
                    self._on_release()


class RootSessionLease:
    """Cancellation-safe handle for a session's root sandbox lease."""

    def __init__(
        self,
        key: Any,
        lease: Any,
        release_callback: Callable[[Any], Awaitable[Any] | Any],
        on_closed: Callable[[RootSessionLease], Awaitable[Any] | Any] | None = None,
        *,
        spec: Any | None = None,
        sandbox: Any | None = None,
        interpreter: Any | None = None,
        broker: Any | None = None,
        volume: Any | None = None,
        volume_id: str | None = None,
        mount_path: str | None = None,
        volume_subpath: str | None = None,
    ) -> None:
        self.key = key
        self.spec = spec
        self.lease = lease
        self.release_callback = release_callback
        self.on_closed = on_closed
        self.sandbox = sandbox if sandbox is not None else getattr(lease, "sandbox", None)
        self.interpreter = interpreter if interpreter is not None else getattr(lease, "interpreter", None)
        self.broker = broker
        self.volume = volume if volume is not None else getattr(lease, "volume", None)
        sandbox_id = getattr(lease, "sandbox_id", None) or getattr(self.sandbox, "id", None)
        self.sandbox_id = str(sandbox_id or "")
        vol_id = getattr(lease, "volume_id", None)
        self.volume_id = volume_id or (str(vol_id) if vol_id else None)
        mp = getattr(lease, "mount_path", None)
        self.mount_path = mount_path or (str(mp) if mp else None)
        vsub = getattr(lease, "volume_subpath", None)
        self.volume_subpath = volume_subpath or (str(vsub) if vsub else None)
        self._state = LeaseState.OPEN
        self._close_lock = asyncio.Lock()
        self._close_task: asyncio.Task[None] | None = None
        self._close_error: BaseException | None = None
        self._notify_on_close = False
        self._close_barrier: Callable[[], Awaitable[Any] | Any] | None = None

    @property
    def state(self) -> LeaseState:
        return self._state

    @property
    def status(self) -> LeaseState:
        return self.state

    @property
    def closed(self) -> bool:
        return self._state is LeaseState.CLOSED

    @property
    def closing(self) -> bool:
        return self._state is LeaseState.CLOSING

    @property
    def failed(self) -> bool:
        return self._state is LeaseState.FAILED

    @property
    def close_error(self) -> BaseException | None:
        return self._close_error

    def set_close_barrier(self, barrier: Callable[[], Awaitable[Any] | Any] | None) -> None:
        self._close_barrier = barrier

    async def try_set_close_barrier(self, barrier: Callable[[], Awaitable[Any] | Any] | None) -> bool:
        async with self._close_lock:
            if self._state is not LeaseState.OPEN:
                return False
            self._close_barrier = barrier
            return True

    async def close(self, *, notify: bool = True, deadline: float | None = None) -> None:
        async with self._close_lock:
            if self._state is LeaseState.CLOSED:
                return
            self._notify_on_close = self._notify_on_close or notify
            task = self._close_task
            if task is None:
                self._state = LeaseState.CLOSING
                task = asyncio.create_task(self._perform_close(), name="fleet-daytona-root-lease-close")
                self._close_task = task
        if deadline is None:
            await asyncio.shield(task)
            return
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise TimeoutError("root Session lease close timed out")
        await asyncio.wait_for(asyncio.shield(task), timeout=remaining)

    async def _perform_close(self) -> None:
        current = asyncio.current_task()
        try:
            if self._close_barrier is not None:
                await await_cleanup(self._close_barrier)
            result = await await_cleanup(self.release_callback, self.lease)
            if _cleanup_failed(self.lease) or _cleanup_failed(result):
                raise RuntimeError("root Session cleanup failed")
        except BaseException as exc:
            async with self._close_lock:
                if self._close_task is current:
                    self._state = LeaseState.FAILED
                    self._close_error = exc
                    self._close_task = None
            raise

        async with self._close_lock:
            if self._close_task is not current:
                return
            self._state = LeaseState.CLOSED
            self._close_error = None
            self._close_task = None
            notify = self._notify_on_close
            self._notify_on_close = False
        if notify and self.on_closed is not None:
            with contextlib.suppress(BaseException):
                await await_cleanup(self.on_closed, self)

    async def release(self) -> None:
        await self.close()


@dataclass(frozen=True, slots=True)
class CloseComponentOutcome:
    status: str
    error: str | None = None


@dataclass(frozen=True, slots=True)
class InterpreterCloseOutcome:
    status: str
    broker: str = "not_present"
    backend: str = "not_present"
    error: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderCleanupOutcome:
    action: str = "none"
    requested: bool = False
    confirmed_absent: bool = False
    plateau: tuple[str, ...] = ()
    duration_s: float = 0.0
    error: str | None = None


@dataclass(frozen=True, slots=True)
class AdmissionOutcome:
    held: bool = False
    released: bool = False
    released_after: str = "not_held"


@dataclass(frozen=True, slots=True)
class QuarantineOutcome:
    quarantined: bool = False
    lane: str = "none"
    error: str | None = None


@dataclass(frozen=True, slots=True)
class SandboxLeaseReceipt:
    kind: LeaseKind
    sandbox_id: str | None
    interpreter: InterpreterCloseOutcome
    provider: ProviderCleanupOutcome
    admission: AdmissionOutcome
    quarantine: QuarantineOutcome
    duration_s: float
    first_error: str | None = None

    @property
    def clean(self) -> bool:
        if self.first_error is not None:
            return False
        if self.quarantine.quarantined:
            return False
        if self.provider.error is not None:
            return False
        return self.interpreter.error is None


class LeasePurgeHook(Protocol):
    def __call__(self, sandbox: Any) -> Awaitable[None]: ...


@dataclass(frozen=True, slots=True)
class SandboxLeasePolicy:
    kind: LeaseKind
    interpreter_shutdown: bool = True
    strict_broker_cleanup: bool = True
    provider_action: Literal["none", "stop", "delete"] = "none"
    stop_force: bool = False
    confirm_absence: bool = False
    confirm_timeout_s: float = 120.0
    confirm_poll_interval_s: float = 0.5
    confirm_fn: Callable[..., Awaitable[AbsenceOutcome]] | None = None
    provider_request_timeout_s: float | None = 30.0
    close_result_timeout_s: float = DEFAULT_CLOSE_RESULT_TIMEOUT_S

    def __post_init__(self) -> None:
        if self.kind == "volume_io":
            object.__setattr__(self, "interpreter_shutdown", False)
            object.__setattr__(self, "provider_action", "delete")
            object.__setattr__(self, "confirm_absence", True)
            object.__setattr__(self, "confirm_timeout_s", 60.0)
            object.__setattr__(self, "confirm_poll_interval_s", 0.1)
        elif self.kind == "recovery_fence":
            object.__setattr__(self, "interpreter_shutdown", False)
            object.__setattr__(self, "provider_action", "stop")
            object.__setattr__(self, "stop_force", True)


def _receipt_state(receipt: SandboxLeaseReceipt) -> LeaseState:
    if receipt.first_error is not None or receipt.quarantine.quarantined:
        return LeaseState.FAILED
    return LeaseState.CLOSED


def _sandbox_id_or_none(sandbox: Any) -> str | None:
    value = getattr(sandbox, "id", None)
    return value if isinstance(value, str) and value else None


_DEFERRED_CLOSE_TASKS: set[asyncio.Task[None]] = set()
_PROVIDER_REQUEST_OWNERS: set[tuple[asyncio.Future[Any], SandboxLease]] = set()
_CLOSE_TASK_OWNERS: set[tuple[asyncio.Future[Any], SandboxLease]] = set()
_FAILED_LEASE_OWNERS: dict[int, SandboxLease] = {}


def _retain_close_task(task: asyncio.Future[Any], lease: SandboxLease) -> None:
    entry = (task, lease)
    _CLOSE_TASK_OWNERS.add(entry)

    def settled(completed: asyncio.Future[Any]) -> None:
        _CLOSE_TASK_OWNERS.discard(entry)
        if not completed.cancelled():
            with contextlib.suppress(BaseException):
                completed.exception()

    task.add_done_callback(settled)


def has_pending_lease_ownership() -> bool:
    """Return True if any lease task, provider request, or quarantined lease is unsettled."""
    return bool(
        any(not task.done() for task in _DEFERRED_CLOSE_TASKS)
        or any(not task.done() for task, _ in _PROVIDER_REQUEST_OWNERS)
        or any(not task.done() for task, _ in _CLOSE_TASK_OWNERS)
        or _FAILED_LEASE_OWNERS
    )


async def wait_lease_ownership(*, timeout: float | None = None) -> bool:
    tasks = tuple(
        task
        for task in (
            *tuple(task for task in _DEFERRED_CLOSE_TASKS if not task.done()),
            *tuple(task for task, _lease in _PROVIDER_REQUEST_OWNERS if not task.done()),
            *tuple(task for task, _lease in _CLOSE_TASK_OWNERS if not task.done()),
        )
    )
    if not tasks:
        return not has_pending_lease_ownership()
    if timeout is None:
        await asyncio.gather(*(asyncio.shield(task) for task in tasks), return_exceptions=True)
        return not has_pending_lease_ownership()
    _, pending = await asyncio.wait(tasks, timeout=timeout)
    return not pending and not has_pending_lease_ownership()


class SandboxLease:
    """Owns one Sandbox handle and its confirmed, idempotent close."""

    def __init__(
        self,
        *,
        kind: LeaseKind,
        sandbox: Any | None,
        sandbox_id: str | None = None,
        platform: SandboxPlatform | None = None,
        permit: DaytonaAdmissionPermit | None = None,
        interpreter: Any | None = None,
        purge: LeasePurgeHook | None = None,
        policy: SandboxLeasePolicy | None = None,
    ) -> None:
        self._policy = policy or SandboxLeasePolicy(kind=kind)
        self._sandbox = sandbox
        self._sandbox_id = sandbox_id or _sandbox_id_or_none(sandbox)
        self._platform = platform
        self._permit = permit
        self._interpreter = interpreter
        self._purge = purge
        self._closed = False
        self._state = LeaseState.OPEN
        self._receipt: SandboxLeaseReceipt | None = None
        self._close_lock = asyncio.Lock()
        self._close_task: asyncio.Future[SandboxLeaseReceipt] | None = None
        self._interpreter_task: asyncio.Task[InterpreterCloseOutcome] | None = None
        self._deferred_close_task: asyncio.Task[None] | None = None
        self._provider_tasks: set[asyncio.Future[Any]] = set()

    @property
    def state(self) -> LeaseState:
        return self._state

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def closing(self) -> bool:
        return self._state is LeaseState.CLOSING

    @property
    def failed(self) -> bool:
        return self._state is LeaseState.FAILED

    @property
    def has_pending_ownership(self) -> bool:
        return bool(
            any(not task.done() for task in self._provider_tasks)
            or (self._deferred_close_task is not None and not self._deferred_close_task.done())
        )

    def _shutdown_interpreter(self) -> InterpreterCloseOutcome:
        interpreter = self._interpreter
        policy = self._policy
        has_broker = (
            bool(getattr(interpreter, "broker", None) or getattr(interpreter, "_http_broker", None))
            if interpreter is not None
            else False
        )
        has_backend = bool(getattr(interpreter, "_backend", None)) if interpreter is not None else False
        if interpreter is None or not policy.interpreter_shutdown:
            return InterpreterCloseOutcome(
                status="not_present" if interpreter is None else "skipped",
                broker="not_present" if not has_broker else "skipped",
                backend="not_present" if not has_backend else "skipped",
            )
        try:
            if hasattr(interpreter, "shutdown"):
                try:
                    interpreter.shutdown(strict_broker_cleanup=policy.strict_broker_cleanup)
                except TypeError:
                    interpreter.shutdown()
        except BaseException as exc:
            error = sanitize_failure_text(exc)
            return InterpreterCloseOutcome(
                status="failed",
                broker="failed" if has_broker else "not_present",
                backend="failed" if has_backend else "not_present",
                error=error,
            )
        return InterpreterCloseOutcome(
            status="clean",
            broker="stopped" if has_broker else "not_present",
            backend="closed" if has_backend else "not_present",
        )

    async def _shutdown_interpreter_owned(self, *, bounded: bool = True) -> InterpreterCloseOutcome:
        task = asyncio.create_task(asyncio.to_thread(self._shutdown_interpreter))
        self._interpreter_task = task
        try:
            if not bounded:
                return await task
            return await asyncio.wait_for(asyncio.shield(task), timeout=max(self._policy.close_result_timeout_s, 1.0))
        except TimeoutError:
            return InterpreterCloseOutcome(
                status="quarantined",
                broker="quarantined" if self._interpreter is not None else "not_present",
                backend="quarantined" if self._interpreter is not None else "not_present",
                error="interpreter shutdown quarantined past close bound",
            )

    def _retain_provider_task(self, task: asyncio.Future[Any]) -> None:
        self._provider_tasks.add(task)
        _PROVIDER_REQUEST_OWNERS.add((task, self))

        def settled(completed: asyncio.Future[Any]) -> None:
            self._provider_tasks.discard(completed)
            _PROVIDER_REQUEST_OWNERS.discard((completed, self))
            if completed.cancelled():
                return
            with contextlib.suppress(BaseException):
                completed.exception()

        task.add_done_callback(settled)

    async def _run_provider_request(
        self,
        request: Awaitable[Any],
        *,
        timeout_s: float | None,
    ) -> str | None:
        task = asyncio.ensure_future(request)
        self._retain_provider_task(task)
        try:
            if timeout_s is None:
                await task
            else:
                await asyncio.wait_for(asyncio.shield(task), timeout=max(0.0, timeout_s))
        except TimeoutError:
            if self._policy.kind == "volume_io":
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            return "provider request TimeoutError"
        except BaseException as exc:
            return sanitize_failure_text(exc)
        return None

    async def _bounded_probe(self, sandbox_id: str) -> Any | None:
        assert self._platform is not None
        probe = getattr(self._platform, "get", None)
        if not callable(probe):
            raise RuntimeError("absence probe unavailable: platform lacks get")
        task = asyncio.ensure_future(probe(sandbox_id))
        self._retain_provider_task(task)
        timeout_s = min(
            max(0.1, self._policy.confirm_poll_interval_s * 2),
            max(0.1, self._policy.confirm_timeout_s),
        )
        try:
            return await asyncio.wait_for(asyncio.shield(task), timeout=timeout_s)
        except TimeoutError:
            if self._policy.kind == "volume_io":
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            raise

    async def _provider_close(self) -> ProviderCleanupOutcome:
        policy = self._policy
        platform = self._platform
        action = policy.provider_action
        if action == "none" or platform is None or self._sandbox_id is None:
            return ProviderCleanupOutcome(action="none", requested=False, confirmed_absent=False)
        started = time.monotonic()
        request_error: str | None = None
        if action == "delete":
            try:
                request = platform.delete(self._sandbox_id)
                request_error = await self._run_provider_request(request, timeout_s=policy.provider_request_timeout_s)
            except BaseException as exc:
                request_error = sanitize_failure_text(exc)
            plateau: tuple[str, ...] = ()
            absent = False
            confirm_error: str | None = None
            probe = getattr(platform, "get", None)
            if policy.confirm_absence and not callable(probe):
                return ProviderCleanupOutcome(
                    action="delete",
                    requested=True,
                    confirmed_absent=False,
                    duration_s=time.monotonic() - started,
                    error=request_error or "absence probe unavailable: platform lacks get",
                )
            if policy.confirm_absence:
                confirm_fn = policy.confirm_fn or confirm_absence
                try:
                    absence: AbsenceOutcome = await confirm_fn(
                        probe=self._bounded_probe,
                        sandbox_id=self._sandbox_id,
                        timeout_s=policy.confirm_timeout_s,
                        poll_interval_s=policy.confirm_poll_interval_s,
                    )
                except BaseException as exc:
                    if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                        raise
                    confirm_error = sanitize_failure_text(exc)
                else:
                    plateau = absence.observations
                    absent = isinstance(absence, AbsenceConfirmation)
                    if not absent:
                        confirm_error = f"absence unconfirmed: {absence!r}"[:240]
            return ProviderCleanupOutcome(
                action="delete",
                requested=True,
                confirmed_absent=absent,
                plateau=plateau,
                duration_s=time.monotonic() - started,
                error=request_error or confirm_error,
            )
        stop_error: str | None = None
        try:
            stop_request = platform.stop(self._sandbox_id, timeout=60, force=self._policy.stop_force)
            stop_error = await self._run_provider_request(stop_request, timeout_s=policy.provider_request_timeout_s)
        except BaseException as exc:
            stop_error = sanitize_failure_text(exc)
            if not policy.stop_force or not policy.confirm_absence:
                return ProviderCleanupOutcome(
                    action="stop",
                    requested=True,
                    confirmed_absent=False,
                    duration_s=time.monotonic() - started,
                    error=stop_error,
                )
        if stop_error is not None and policy.stop_force and policy.confirm_absence:
            probe = getattr(platform, "get", None)
            if not callable(probe):
                return ProviderCleanupOutcome(
                    action="stop",
                    requested=True,
                    confirmed_absent=False,
                    duration_s=time.monotonic() - started,
                    error=stop_error or "absence probe unavailable: platform lacks get",
                )
            confirm_fn = policy.confirm_fn or confirm_absence
            try:
                absence = await confirm_fn(
                    probe=self._bounded_probe,
                    sandbox_id=self._sandbox_id,
                    timeout_s=min(policy.confirm_timeout_s, 1.0),
                    poll_interval_s=min(policy.confirm_poll_interval_s, 0.1),
                )
            except BaseException as exc:
                if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                    raise
                return ProviderCleanupOutcome(
                    action="stop",
                    requested=True,
                    confirmed_absent=False,
                    duration_s=time.monotonic() - started,
                    error=stop_error or sanitize_failure_text(exc),
                )
            absent = isinstance(absence, AbsenceConfirmation)
            return ProviderCleanupOutcome(
                action="stop",
                requested=True,
                confirmed_absent=absent,
                plateau=absence.observations,
                duration_s=time.monotonic() - started,
                error=stop_error or (None if absent else f"absence unconfirmed: {absence!r}"[:240]),
            )
        return ProviderCleanupOutcome(
            action="stop",
            requested=True,
            confirmed_absent=False,
            duration_s=time.monotonic() - started,
            error=stop_error,
        )

    async def _finish_retained_provider_close(self) -> None:
        retry_delay = min(max(self._policy.confirm_poll_interval_s, 0.05), 1.0)
        while True:
            pending = tuple(task for task in self._provider_tasks if not task.done())
            if pending:
                await asyncio.wait(pending, timeout=retry_delay)
                if any(not task.done() for task in self._provider_tasks):
                    continue
            provider = await self._provider_close()
            confirmed = (
                (provider.action == "delete" and provider.requested and provider.confirmed_absent)
                or (provider.action in {"stop", "none"} and provider.error is None)
            ) and not self._provider_tasks
            if confirmed:
                if self._permit is not None:
                    self._permit.release()
                    self._permit = None
                return
            await asyncio.sleep(retry_delay)

    async def _finish_deferred_close(
        self,
        interpreter_task: asyncio.Task[InterpreterCloseOutcome],
    ) -> None:
        try:
            interpreter = await interpreter_task
        except BaseException as exc:
            interpreter = InterpreterCloseOutcome(
                status="failed",
                broker="failed",
                backend="failed",
                error=sanitize_failure_text(exc),
            )
        while interpreter.status in {"failed", "quarantined"}:
            interpreter = await self._shutdown_interpreter_owned(bounded=False)
            if interpreter.status in {"failed", "quarantined"}:
                await asyncio.sleep(min(max(self._policy.confirm_poll_interval_s, 0.05), 1.0))
        if self._purge is not None and self._sandbox is not None:
            with contextlib.suppress(BaseException):
                await self._purge(self._sandbox)
        provider = await self._provider_close()
        retained_provider_pending = (
            self._policy.kind == "retained_session"
            and (
                bool(self._provider_tasks)
                or provider.error is not None
                or (
                    self._policy.confirm_absence
                    and provider.action == "delete"
                    and provider.requested
                    and not provider.confirmed_absent
                )
            )
        ) or (self._policy.kind == "recovery_fence" and bool(self._provider_tasks))
        if retained_provider_pending:
            await self._finish_retained_provider_close()
            return
        if self._permit is not None:
            self._permit.release()
            self._permit = None

    def _retain_deferred_close(self, task: asyncio.Task[None]) -> None:
        self._deferred_close_task = task
        _DEFERRED_CLOSE_TASKS.add(task)

        def settled(completed: asyncio.Task[None]) -> None:
            _DEFERRED_CLOSE_TASKS.discard(completed)
            if completed.cancelled():
                _FAILED_LEASE_OWNERS[id(self)] = self
                return
            with contextlib.suppress(BaseException):
                error = completed.exception()
            if error is None:
                _FAILED_LEASE_OWNERS.pop(id(self), None)
            else:
                _FAILED_LEASE_OWNERS[id(self)] = self

        task.add_done_callback(settled)

    async def _close_core(self, *, bounded_interpreter: bool = True) -> SandboxLeaseReceipt:
        started = time.monotonic()
        policy = self._policy
        first_error: str | None = None

        interpreter = await self._shutdown_interpreter_owned(bounded=bounded_interpreter)
        if interpreter.status in {"failed", "quarantined"} and first_error is None:
            first_error = interpreter.error

        if interpreter.status in {"failed", "quarantined"}:
            interpreter_task = self._interpreter_task
            if interpreter_task is None:
                raise RuntimeError("interpreter quarantine has no owned task")
            if not bounded_interpreter:
                interpreter = await self._shutdown_interpreter_owned(bounded=False)
                if interpreter.status in {"failed", "quarantined"}:
                    held = self._permit is not None
                    return SandboxLeaseReceipt(
                        kind=policy.kind,
                        sandbox_id=self._sandbox_id,
                        interpreter=interpreter,
                        provider=ProviderCleanupOutcome(
                            action=policy.provider_action,
                            requested=False,
                            confirmed_absent=False,
                            error="provider cleanup deferred until interpreter shutdown settles",
                        ),
                        admission=AdmissionOutcome(
                            held=held,
                            released=False,
                            released_after="quarantine_failure" if held else "not_held",
                        ),
                        quarantine=QuarantineOutcome(
                            quarantined=True,
                            lane="fallback_thread",
                            error=interpreter.error,
                        ),
                        duration_s=time.monotonic() - started,
                        first_error=interpreter.error or "interpreter shutdown quarantined",
                    )
            else:
                deferred = asyncio.create_task(
                    self._finish_deferred_close(interpreter_task),
                    name="fleet-sandbox-lease-deferred-close",
                )
                self._retain_deferred_close(deferred)
                held = self._permit is not None
                return SandboxLeaseReceipt(
                    kind=policy.kind,
                    sandbox_id=self._sandbox_id,
                    interpreter=interpreter,
                    provider=ProviderCleanupOutcome(
                        action=policy.provider_action,
                        requested=False,
                        confirmed_absent=False,
                        error="provider cleanup deferred until interpreter shutdown settles",
                    ),
                    admission=AdmissionOutcome(
                        held=held,
                        released=False,
                        released_after="quarantine_failure" if held else "not_held",
                    ),
                    quarantine=QuarantineOutcome(
                        quarantined=True,
                        lane="owner_loop",
                        error=interpreter.error,
                    ),
                    duration_s=time.monotonic() - started,
                    first_error=interpreter.error or "interpreter shutdown quarantined",
                )

        if self._purge is not None and self._sandbox is not None:
            try:
                await self._purge(self._sandbox)
            except BaseException as exc:
                if first_error is None:
                    first_error = sanitize_failure_text(exc)

        provider = await self._provider_close()
        if provider.error is not None and first_error is None:
            first_error = provider.error

        retained_provider_pending = (
            policy.kind == "retained_session"
            and (
                bool(self._provider_tasks)
                or provider.error is not None
                or (
                    policy.confirm_absence
                    and provider.action == "delete"
                    and provider.requested
                    and not provider.confirmed_absent
                )
            )
        ) or (policy.kind == "recovery_fence" and bool(self._provider_tasks))
        if retained_provider_pending:
            deferred = asyncio.create_task(
                self._finish_retained_provider_close(),
                name="fleet-sandbox-lease-retained-provider-close",
            )
            self._retain_deferred_close(deferred)
            held = self._permit is not None
            return SandboxLeaseReceipt(
                kind=policy.kind,
                sandbox_id=self._sandbox_id,
                interpreter=interpreter,
                provider=provider,
                admission=AdmissionOutcome(
                    held=held,
                    released=False,
                    released_after="quarantine_failure" if held else "not_held",
                ),
                quarantine=QuarantineOutcome(
                    quarantined=True,
                    lane="owner_loop",
                    error=provider.error or "provider request remains owned",
                ),
                duration_s=time.monotonic() - started,
                first_error=first_error or "provider request remains owned",
            )

        quarantined = interpreter.status == "quarantined"
        quarantine_error: str | None = interpreter.error if quarantined else None
        if provider.error is not None:
            quarantined = True
            quarantine_error = quarantine_error or provider.error
        if (
            self._policy.confirm_absence
            and provider.action == "delete"
            and provider.requested
            and not provider.confirmed_absent
        ):
            quarantined = True
            quarantine_error = provider.error or "absence unconfirmed"

        held = self._permit is not None
        if self._permit is not None:
            self._permit.release()
            self._permit = None
        if not held:
            released_after = "not_held"
        elif not quarantined and first_error is None:
            released_after = "confirmed_cleanup"
        else:
            released_after = "quarantine_failure"
        admission = AdmissionOutcome(held=held, released=held, released_after=released_after)

        return SandboxLeaseReceipt(
            kind=policy.kind,
            sandbox_id=self._sandbox_id,
            interpreter=interpreter,
            provider=provider,
            admission=admission,
            quarantine=QuarantineOutcome(
                quarantined=quarantined,
                lane="owner_loop" if quarantined else "none",
                error=quarantine_error,
            ),
            duration_s=time.monotonic() - started,
            first_error=first_error,
        )

    async def _run_fallback_close(self) -> SandboxLeaseReceipt:
        try:
            receipt = await self._close_core(bounded_interpreter=False)
        except BaseException:
            self._close_task = None
            self._state = LeaseState.FAILED
            self._closed = False
            raise
        self._receipt = receipt
        self._close_task = None
        self._state = _receipt_state(receipt)
        self._closed = True
        return receipt

    async def _run_async_close(self) -> SandboxLeaseReceipt:
        current = asyncio.current_task()
        try:
            receipt = await self._close_core()
        except BaseException:
            async with self._close_lock:
                if self._close_task is current:
                    self._close_task = None
                    self._state = LeaseState.FAILED
                    self._closed = False
            raise
        async with self._close_lock:
            if self._close_task is current:
                self._receipt = receipt
                self._close_task = None
                self._state = _receipt_state(receipt)
                self._closed = True
        return receipt

    async def aclose(self, *, deadline: float | None = None) -> SandboxLeaseReceipt:
        task: asyncio.Future[SandboxLeaseReceipt] | None = None
        async with self._close_lock:
            if self._receipt is not None:
                return self._receipt
            task = self._close_task
            if task is None:
                coroutine = self._run_async_close()
                try:
                    task = asyncio.create_task(coroutine, name="fleet-sandbox-lease-close")
                except BaseException:
                    coroutine.close()
                    execution = schedule_owned_close(
                        loop=asyncio.get_running_loop(),
                        build=self._run_fallback_close,
                        thread_name="fleet-sandbox-lease-close-fallback",
                    )
                    task = asyncio.ensure_future(asyncio.wrap_future(execution.future))
                self._close_task = task
                _retain_close_task(task, self)
                self._state = LeaseState.CLOSING
                if task.done() and self._receipt is None:
                    failed = task.cancelled()
                    if not failed:
                        with contextlib.suppress(BaseException):
                            failed = task.exception() is not None
                    if failed:
                        self._close_task = None
                        self._state = LeaseState.FAILED
                        self._closed = False
        assert task is not None
        if deadline is None:
            return await asyncio.shield(task)
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise TimeoutError("Sandbox lease cleanup timed out")
        return await asyncio.wait_for(asyncio.shield(task), timeout=remaining)

    async def wait_ownership(self, *, timeout: float | None = None) -> bool:
        if timeout is not None and timeout < 0:
            raise ValueError("timeout must be non-negative")
        close_deadline = None
        if timeout is not None:
            close_deadline = asyncio.get_running_loop().time() + timeout
        try:
            await self.aclose(deadline=close_deadline)
        except TimeoutError:
            return False
        tasks = tuple(
            task
            for task in (
                self._close_task,
                self._deferred_close_task,
                *tuple(self._provider_tasks),
            )
            if task is not None and not task.done()
        )
        if not tasks:
            return not self.has_pending_ownership
        if timeout is None:
            await asyncio.gather(*(asyncio.shield(task) for task in tasks), return_exceptions=True)
        else:
            _, pending = await asyncio.wait(tasks, timeout=timeout)
            if pending:
                return False
        return not self.has_pending_ownership

    def close(self) -> SandboxLeaseReceipt:
        if self._receipt is not None:
            return self._receipt
        self._state = LeaseState.CLOSING
        self._closed = False
        try:
            self._receipt = asyncio.run(self._close_core(bounded_interpreter=False))
            self._state = _receipt_state(self._receipt)
            self._closed = True
        except BaseException as exc:
            self._receipt = SandboxLeaseReceipt(
                kind=self._policy.kind,
                sandbox_id=self._sandbox_id,
                interpreter=InterpreterCloseOutcome(status="not_present"),
                provider=ProviderCleanupOutcome(action="none", requested=False, confirmed_absent=False),
                admission=AdmissionOutcome(
                    held=self._permit is not None,
                    released=self._permit is not None,
                    released_after="quarantine_failure" if self._permit is not None else "not_held",
                ),
                quarantine=QuarantineOutcome(
                    quarantined=True, lane="fallback_thread", error=sanitize_failure_text(exc)
                ),
                duration_s=0.0,
                first_error=sanitize_failure_text(exc),
            )
            self._state = LeaseState.FAILED
            self._closed = False
            if self._permit is not None:
                self._permit.release()
        return self._receipt


@dataclass(slots=True)
class OwnedCloseExecution:
    future: Future[Any]
    used_fallback: bool
    coroutine: Any | None = None


def schedule_owned_close(
    *,
    loop: asyncio.AbstractEventLoop,
    build: Callable[[], Coroutine[Any, Any, Any]],
    fallback_owner_release: Callable[[], None] | None = None,
    thread_name: str = "fleet-lease-close-fallback",
) -> OwnedCloseExecution:
    coroutine = build()
    try:
        return OwnedCloseExecution(
            future=asyncio.run_coroutine_threadsafe(coroutine, loop),
            used_fallback=False,
            coroutine=coroutine,
        )
    except BaseException:
        if inspect.iscoroutine(coroutine):
            coroutine.close()

    fallback: Future[Any] = Future()

    def run_fallback() -> None:
        try:
            asyncio.run(build())
        except BaseException as exc:
            if not fallback.done():
                fallback.set_exception(exc)
        else:
            if not fallback.done():
                fallback.set_result(None)

    thread = Thread(target=run_fallback, name=thread_name, daemon=True)
    try:
        thread.start()
    except BaseException as exc:
        if fallback_owner_release is not None:
            with contextlib.suppress(BaseException):
                fallback_owner_release()
        fallback.set_exception(exc)
    return OwnedCloseExecution(future=fallback, used_fallback=True, coroutine=None)


async def _claim_session_lease(
    registry: ActiveLeaseRegistry, session_id: UUID, run_id: UUID, *, workspace_id: UUID, deadline: float
) -> None:
    loop = asyncio.get_running_loop()
    claim_wait_deadline = loop.time() + _PREWARM_CLAIM_WAIT_SECONDS
    while True:
        try:
            registry.acquire(session_id, run_id, workspace_id=workspace_id)
            return
        except ActiveLeaseConflictError as exc:
            if run_id == PREWARM_RUN_ID or exc.holder_run_id != PREWARM_RUN_ID:
                raise
        remaining = min(deadline, claim_wait_deadline) - loop.time()
        if remaining <= 0:
            raise DaytonaLeaseAcquisitionTimeoutError("Daytona lease acquisition timed out") from None
        await asyncio.sleep(min(0.2, remaining))
