"""DaytonaSessionManager: acquire/release leases and capability-aware lifecycle.

Release never deletes a Sandbox. Volume identity is preserved across replace.
Workspace Volume Scope uses VolumeMount subpath ``workspaces/<workspace_id>``.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
import time
import weakref
from collections.abc import Awaitable, Callable, Coroutine
from concurrent.futures import Future
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from threading import Lock, Thread
from typing import Any, Literal, Protocol, TypeAlias
from uuid import UUID, uuid4

from fleet_rlm.daytona.admission import (
    DaytonaAdmission,
    DaytonaAdmissionPermit,
)
from fleet_rlm.daytona.errors import (
    DaytonaAdapterError,
    ProviderRequestError,
    is_safe_pre_creation_retry,
    map_provider_error,
    sanitize_failure_text,
)
from fleet_rlm.daytona.interpreter import (
    DEFAULT_EXECUTION_OUTPUT_CHARS,
    DEFAULT_EXECUTION_TIMEOUT_S,
    DaytonaCodeInterpreter,
    sandbox_backend,
)
from fleet_rlm.daytona.lifecycle import AbsenceConfirmation, AbsenceOutcome, confirm_absence
from fleet_rlm.daytona.platform import sandbox_state
from fleet_rlm.daytona.provisioning import (
    DaytonaSandboxSpec,
    ExpectedWorkspaceMount,
    SandboxPlatform,
    SandboxProvisioner,
    VolumeClient,
    VolumeConfig,
    get_or_create_volume_id,
)
from fleet_rlm.daytona.sync_bridge import SyncBridgeDispatcher
from fleet_rlm.runtime.bindings import (
    BindingGenerationAuthority,
    SandboxBinding,
    require_non_zero_workspace_id,
    require_scoped_volume_subpath,
    workspace_volume_subpath,
)
from fleet_rlm.runtime.cleanup import RunCleanupSupervisor
from fleet_rlm.runtime.owned_effect import OwnedEffect

logger = logging.getLogger(__name__)


async def await_cleanup(callback: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Invoke sync or async cleanup without changing its exception identity."""
    result = callback(*args, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


class LeaseState(StrEnum):
    """Lifecycle states shared by public root and child handles."""

    OPEN = "OPEN"
    CLOSING = "CLOSING"
    CLOSED = "CLOSED"
    FAILED = "FAILED"


class RootSessionLease:
    """Cancellation-safe owner for one reusable root provider lease."""

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
        self.broker = (
            broker
            if broker is not None
            else getattr(self.interpreter, "broker", getattr(self.interpreter, "_http_broker", None))
        )
        self.volume = volume if volume is not None else getattr(lease, "volume", None)
        sandbox_id = getattr(lease, "sandbox_id", None) or getattr(self.sandbox, "id", None)
        self.sandbox_id = str(sandbox_id or "")
        self.volume_id = volume_id or _optional_text(getattr(lease, "volume_id", None))
        self.mount_path = mount_path or _optional_text(getattr(lease, "mount_path", None))
        self.volume_subpath = volume_subpath or _optional_text(getattr(lease, "volume_subpath", None))
        self._state = LeaseState.OPEN
        self._environment_provider_owner: Any | None = None
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
            barrier = self._close_barrier
            if barrier is not None:
                await await_cleanup(barrier)
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


def _optional_text(value: Any) -> str | None:
    return None if value is None else str(value)


_DEFERRED_CLOSE_TASKS: set[asyncio.Task[None]] = set()
_PROVIDER_REQUEST_OWNERS: set[tuple[asyncio.Future[Any], Any]] = set()
_CLOSE_TASK_OWNERS: set[tuple[asyncio.Future[Any], Any]] = set()
_FAILED_LEASE_OWNERS: dict[int, Any] = {}


def has_pending_lease_ownership() -> bool:
    deferred = any(not task.done() for task in _DEFERRED_CLOSE_TASKS)
    provider = any(not task.done() for task, _lease in _PROVIDER_REQUEST_OWNERS)
    close = any(not task.done() for task, _lease in _CLOSE_TASK_OWNERS)
    failed = bool(_FAILED_LEASE_OWNERS)
    return deferred or provider or close or failed


async def wait_lease_ownership(*, timeout: float | None = None) -> bool:
    if timeout is not None and timeout < 0:
        raise ValueError("timeout must be non-negative")
    tasks = tuple(
        task
        for task in (
            *tuple(task for task in _DEFERRED_CLOSE_TASKS if not task.done()),
            *tuple(task for task, _lease in _PROVIDER_REQUEST_OWNERS if not task.done()),
            *tuple(task for task, _lease in _CLOSE_TASK_OWNERS if not task.done()),
        )
    )
    if not tasks:
        return not _FAILED_LEASE_OWNERS
    current_loop = asyncio.get_running_loop()
    if any(task.get_loop() is not current_loop for task in tasks):
        return False
    if timeout is None:
        await asyncio.gather(*(asyncio.shield(task) for task in tasks), return_exceptions=True)
        return not has_pending_lease_ownership()
    _, pending = await asyncio.wait(tasks, timeout=timeout)
    return not pending and not has_pending_lease_ownership()


LeaseKind: TypeAlias = Literal["retained_session", "recursive_child", "volume_io", "recovery_fence"]
DEFAULT_CLOSE_RESULT_TIMEOUT_S = 60.0


class LeaseCleanupError(RuntimeError):
    pass


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


def _retain_close_task(task: asyncio.Future[Any], lease: SandboxLease) -> None:
    entry = (task, lease)
    _CLOSE_TASK_OWNERS.add(entry)

    def settled(completed: asyncio.Future[Any]) -> None:
        _CLOSE_TASK_OWNERS.discard(entry)
        if not completed.cancelled():
            with contextlib.suppress(BaseException):
                completed.exception()

    task.add_done_callback(settled)


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
        has_broker = bool(getattr(interpreter, "_http_broker", None)) if interpreter is not None else False
        has_backend = bool(getattr(interpreter, "_backend", None)) if interpreter is not None else False
        if interpreter is None or not policy.interpreter_shutdown:
            return InterpreterCloseOutcome(
                status="not_present" if interpreter is None else "skipped",
                broker="not_present" if not has_broker else "skipped",
                backend="not_present" if not has_backend else "skipped",
            )
        try:
            interpreter.shutdown(strict_broker_cleanup=policy.strict_broker_cleanup)
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


def _receipt_state(receipt: SandboxLeaseReceipt) -> LeaseState:
    if receipt.first_error is not None or receipt.quarantine.quarantined:
        return LeaseState.FAILED
    return LeaseState.CLOSED


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


def _sandbox_id_or_none(sandbox: Any) -> str | None:
    value = getattr(sandbox, "id", None)
    return value if isinstance(value, str) and value else None


@dataclass(slots=True)
class _LateOwner:
    request: LeaseRequest
    run_id: UUID
    permit: DaytonaAdmissionPermit | None = None
    acquisition: asyncio.Task[InterpreterLease] | None = None
    lease: InterpreterLease | None = None
    cleanup_task: Any | None = None
    cleanup_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    callback_started: bool = False
    callback_settled: bool = False
    unpublished: bool = False


class ActiveLeaseConflictError(RuntimeError):
    def __init__(self, session_id: UUID, holder_run_id: UUID | None = None) -> None:
        self.session_id = session_id
        self.holder_run_id = holder_run_id
        super().__init__(f"active lease conflict for session {session_id}")


PREWARM_RUN_ID = UUID("00000000-0000-4000-8000-000000000000")
_PREWARM_CLAIM_WAIT_SECONDS = 60.0


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


class ActiveLeaseRegistry:
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


@dataclass(slots=True)
class InterpreterLease:
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
                self.interpreter.shutdown(strict_broker_cleanup=True)
            except BaseException:
                self._state = LeaseState.FAILED
                raise
            self._released = True
            self._state = LeaseState.CLOSED
            if self._on_release is not None and not self._defer_owner_release:
                with contextlib.suppress(BaseException):
                    self._on_release()


class DaytonaLeaseAcquisitionTimeoutError(RuntimeError):
    pass


class _ProviderCallDeadlineError(TimeoutError):
    def __init__(self, task: asyncio.Future[Any], operation: str) -> None:
        self.task = task
        self.operation = operation
        super().__init__(f"Daytona {operation} timed out")


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


DEFAULT_IDLE_STOP_SECONDS = 300.0


@dataclass(frozen=True, slots=True)
class LeaseRequest:
    session_id: UUID
    user_id: UUID
    workspace_id: UUID
    run_id: UUID | None = None


@dataclass(slots=True)
class _AcquisitionContext:
    expected: ExpectedWorkspaceMount
    binding: SandboxBinding | None
    persisted_binding: SandboxBinding | None = None


class BindingStoreLike(Protocol):
    async def get(self, session_id: UUID) -> SandboxBinding | None: ...
    async def upsert(self, binding: SandboxBinding) -> SandboxBinding: ...


def _sandbox_id(sandbox: Any) -> str:
    sid = getattr(sandbox, "id", None)
    if sid is None:
        raise DaytonaAdapterError(message="sandbox missing id", cause_type="SandboxIdentityError")
    return str(sid)


def _build_interpreter(
    sandbox: Any,
    *,
    loop: asyncio.AbstractEventLoop,
    dispatcher: SyncBridgeDispatcher | None = None,
    execution_output_cap: int = DEFAULT_EXECUTION_OUTPUT_CHARS,
    execution_timeout_s: int = DEFAULT_EXECUTION_TIMEOUT_S,
) -> DaytonaCodeInterpreter:
    if hasattr(sandbox, "code_interpreter"):
        return DaytonaCodeInterpreter(
            backend=sandbox_backend(sandbox, loop=loop, dispatcher=dispatcher, timeout_s=execution_timeout_s),
            execution_output_cap=execution_output_cap,
        )
    existing = getattr(sandbox, "interpreter", None)
    if isinstance(existing, DaytonaCodeInterpreter):
        return existing
    return DaytonaCodeInterpreter(
        backend=getattr(sandbox, "backend", None),
        execution_output_cap=execution_output_cap,
    )


def binding_matches_expected(binding: SandboxBinding, expected: ExpectedWorkspaceMount) -> bool:
    try:
        require_non_zero_workspace_id(binding.workspace_id)
        require_scoped_volume_subpath(binding.volume_subpath, workspace_id=binding.workspace_id)
    except (TypeError, ValueError):
        return False
    return (
        binding.workspace_id == expected.workspace_id
        and binding.volume_id == expected.volume_id
        and binding.volume_subpath == expected.volume_subpath
        and binding.mount_path == expected.mount_path
    )


class DaytonaSessionManager:
    """Owns Sandbox lifecycle policy for Fleet RLM sessions."""

    def __init__(
        self,
        *,
        platform: SandboxPlatform,
        volume_client: VolumeClient,
        volume_config: VolumeConfig,
        bindings: BindingStoreLike,
        admission: DaytonaAdmission | None = None,
        sandbox_spec: DaytonaSandboxSpec,
        cleanup: RunCleanupSupervisor | None = None,
        idle_stop_seconds: float | None = None,
        execution_output_cap: int = DEFAULT_EXECUTION_OUTPUT_CHARS,
        execution_timeout_s: int = DEFAULT_EXECUTION_TIMEOUT_S,
        dispatcher: SyncBridgeDispatcher | None = None,
    ) -> None:
        self._platform = platform
        self._volume_client = volume_client
        self._volume_config = volume_config
        self._bindings = bindings
        self._binding_authority = BindingGenerationAuthority()
        self._active_leases = ActiveLeaseRegistry()
        self._admission = admission or DaytonaAdmission()
        self._dispatcher = dispatcher
        self._sandbox_spec = sandbox_spec
        self._cleanup = cleanup or RunCleanupSupervisor()
        self._execution_output_cap = execution_output_cap
        self._execution_timeout_s = execution_timeout_s
        if idle_stop_seconds is not None and idle_stop_seconds <= 0:
            raise ValueError("idle_stop_seconds must be positive")
        self._idle_stop_seconds = idle_stop_seconds
        self._idle_tasks: dict[tuple[UUID, UUID], asyncio.Task[None]] = {}
        self._runtime_ref: weakref.ReferenceType[Any] | None = None
        self._owned_sandbox_ids: set[str] = set()
        self._owned_sandbox_lock = Lock()
        self._release_tasks: set[asyncio.Task[None]] = set()
        self._handled_release_tasks: set[asyncio.Task[None]] = set()
        self._release_leases: dict[asyncio.Task[None], InterpreterLease] = {}
        self._late_cleanup_tasks: set[Any] = set()
        self._late_owners: dict[int, _LateOwner] = {}
        self._provider_tasks: set[asyncio.Future[Any]] = set()
        self._provisioner = SandboxProvisioner(
            platform=platform,
            volume_config=volume_config,
            sandbox_spec=sandbox_spec,
        )

    @property
    def active_leases(self) -> ActiveLeaseRegistry:
        return self._active_leases

    def bind_runtime(self, runtime: Any) -> None:
        self._runtime_ref = weakref.ref(runtime)

    def _has_open_retained_root(self, workspace_id: UUID | None, session_id: UUID) -> bool:
        runtime = self._runtime_ref() if self._runtime_ref is not None else None
        if runtime is None:
            return False
        owns = getattr(runtime, "owns_open_root", None)
        if not callable(owns):
            return False
        try:
            return bool(owns(workspace_id, session_id))
        except (TypeError, ValueError):
            logger.warning(
                "Unable to verify retained Daytona root before idle stop",
                extra={"session_id": str(session_id), "workspace_id": str(workspace_id)},
                exc_info=True,
            )
            return True

    def _idle_stop_blocked(self, session_id: UUID, workspace_id: UUID | None) -> bool:
        if self._active_leases.holder(session_id, workspace_id=workspace_id) is not None:
            return True
        if self._active_leases.has_session(session_id):
            return True
        return self._has_open_retained_root(workspace_id, session_id)

    def _observe_binding(self, binding: SandboxBinding | None) -> None:
        if binding is None:
            return
        authority = getattr(self, "_binding_authority", None)
        if authority is not None:
            authority.observe(binding)

    def is_binding_current(
        self,
        *,
        session_id: UUID,
        workspace_id: UUID,
        sandbox_id: str,
        generation: int,
    ) -> bool:
        authority = getattr(self, "_binding_authority", None)
        if authority is None:
            return True
        return authority.is_current(
            session_id=session_id,
            workspace_id=workspace_id,
            sandbox_id=sandbox_id,
            generation=generation,
        )

    def revoke_binding(
        self,
        *,
        session_id: UUID,
        workspace_id: UUID,
        sandbox_id: str,
        generation: int,
    ) -> None:
        authority = getattr(self, "_binding_authority", None)
        if authority is not None:
            authority.revoke(
                session_id=session_id,
                workspace_id=workspace_id,
                sandbox_id=sandbox_id,
                generation=generation,
            )

    def _mark_sandbox_owned(self, sandbox_id: str) -> None:
        with self._owned_sandbox_lock:
            self._owned_sandbox_ids.add(sandbox_id)

    def _mark_sandbox_released(self, sandbox_id: str) -> None:
        with self._owned_sandbox_lock:
            self._owned_sandbox_ids.discard(sandbox_id)

    @property
    def has_pending_ownership(self) -> bool:
        with self._owned_sandbox_lock:
            has_owned_sandboxes = bool(self._owned_sandbox_ids)
        return bool(
            has_owned_sandboxes
            or self._late_owners
            or self._late_cleanup_tasks
            or any(not task.done() for task in self._provider_tasks)
            or any(not task.done() for task in self._release_tasks)
            or any(not task.done() for task in self._idle_tasks.values())
        )

    def owns_sandbox(self, sandbox_id: str) -> bool:
        with self._owned_sandbox_lock:
            return sandbox_id in self._owned_sandbox_ids

    async def prewarm_session(
        self,
        session_id: UUID,
        *,
        user_id: UUID,
        workspace_id: UUID,
        deadline: float | None = None,
    ) -> bool:
        effective_deadline = deadline if deadline is not None else asyncio.get_running_loop().time() + 120.0
        try:
            lease = await self.acquire(
                LeaseRequest(
                    session_id=session_id,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    run_id=PREWARM_RUN_ID,
                ),
                deadline=effective_deadline,
            )
        except ActiveLeaseConflictError:
            return False
        await self.release(lease)
        return True

    def schedule_prewarm(
        self,
        session_id: UUID,
        user_id: UUID,
        workspace_id: UUID,
    ) -> asyncio.Task[None]:
        async def run_prewarm() -> None:
            try:
                await self.prewarm_session(session_id, user_id=user_id, workspace_id=workspace_id)
            except asyncio.CancelledError:
                raise
            except BaseException:
                pass

        task = asyncio.create_task(run_prewarm(), name=f"fleet-session-prewarm-{session_id}")
        _retain_provider_task(task, self._provider_tasks)
        return task

    def _expected_mount(self, *, volume_id: str, workspace_id: UUID) -> ExpectedWorkspaceMount:
        return self._provisioner.expected_mount(volume_id=volume_id, workspace_id=workspace_id)

    def _sandbox_retirement_lease(
        self,
        sandbox_id: str,
        *,
        confirm_timeout_s: float = 120.0,
        provider_request_timeout_s: float | None = 30.0,
    ) -> SandboxLease:
        return SandboxLease(
            kind="retained_session",
            sandbox=None,
            sandbox_id=sandbox_id,
            platform=self._platform,
            policy=SandboxLeasePolicy(
                kind="retained_session",
                interpreter_shutdown=False,
                provider_action="delete",
                confirm_timeout_s=confirm_timeout_s,
                provider_request_timeout_s=provider_request_timeout_s,
            ),
        )

    async def acquire(
        self,
        request: LeaseRequest,
        *,
        deadline: float,
        force_new: bool = False,
    ) -> InterpreterLease:
        require_non_zero_workspace_id(request.workspace_id)
        run_id = request.run_id or uuid4()
        session_id = request.session_id
        await self._cancel_idle_stop(session_id, workspace_id=request.workspace_id, deadline=deadline)
        await _claim_session_lease(
            self._active_leases, session_id, run_id, workspace_id=request.workspace_id, deadline=deadline
        )
        claim_held = True
        permit: DaytonaAdmissionPermit | None = None
        try:
            permit = await self._admission.acquire(deadline=deadline)
            acquisition = asyncio.create_task(
                self._acquire_provider(request, run_id=run_id, deadline=deadline, force_new=force_new),
                name="fleet-daytona-provider-acquisition",
            )
            try:
                async with asyncio.timeout_at(deadline):
                    lease = await asyncio.shield(acquisition)
            except TimeoutError:
                self._adopt_late_acquisition(acquisition, permit, request, run_id)
                permit = None
                claim_held = False
                raise DaytonaLeaseAcquisitionTimeoutError("Daytona lease acquisition timed out") from None
            except asyncio.CancelledError:
                self._adopt_late_acquisition(acquisition, permit, request, run_id)
                permit = None
                claim_held = False
                raise

            self._bind_lease_ownership(
                lease,
                permit,
                session_id=session_id,
                workspace_id=request.workspace_id,
                run_id=run_id,
            )
            self._mark_sandbox_owned(lease.sandbox_id)
            return lease
        except BaseException:
            try:
                if permit is not None:
                    permit.release()
            finally:
                if claim_held:
                    self._active_leases.release(session_id, run_id, workspace_id=request.workspace_id)
            raise

    @staticmethod
    async def _settle_provider_acquisition(acquisition: asyncio.Task[InterpreterLease]) -> InterpreterLease:
        effect = OwnedEffect.from_task(acquisition)
        await effect.settle()
        return effect.result()

    async def _settle_late_owner(self, owner: _LateOwner, *, deadline: float | None = None) -> None:
        if owner.unpublished:
            await self._finish_unpublished_lease(owner, deadline=deadline)
            return
        if owner.acquisition is not None and owner.lease is None:
            await self._settle_late_acquisition(owner)
            return
        await self._settle_late_lease(owner)

    async def _settle_late_acquisition(self, owner: _LateOwner) -> None:
        acquisition = owner.acquisition
        assert acquisition is not None
        if not acquisition.done():
            try:
                acquisition_loop = acquisition.get_loop()
            except BaseException:
                acquisition_loop = None
            if acquisition_loop is not asyncio.get_running_loop():
                return
        try:
            try:
                lease = await self._settle_provider_acquisition(acquisition)
            except BaseException:
                try:
                    if owner.permit is not None:
                        owner.permit.release()
                finally:
                    self._active_leases.release(
                        owner.request.session_id,
                        owner.run_id,
                        workspace_id=owner.request.workspace_id,
                    )
                return
            owner.lease = lease
            owner.acquisition = None
            if self._late_owners.get(id(acquisition)) is owner:
                self._late_owners.pop(id(acquisition), None)
            self._late_owners[id(lease)] = owner
            self._mark_sandbox_owned(lease.sandbox_id)
            await self._settle_late_lease(owner)
        finally:
            if acquisition.done() and self._late_owners.get(id(acquisition)) is owner:
                self._late_owners.pop(id(acquisition), None)

    def _schedule_late_owner_fallback(self, owner: _LateOwner) -> bool:
        try:
            owner_loop = owner.acquisition.get_loop() if owner.acquisition is not None else None
        except BaseException:
            owner_loop = None
        if owner_loop is None or owner_loop.is_closed() or not owner_loop.is_running():
            owner_loop = asyncio.new_event_loop()
            owner_loop.close()
        try:
            execution = schedule_owned_close(
                loop=owner_loop,
                build=lambda: self._settle_late_owner(owner),
                thread_name="fleet-daytona-late-ownership-fallback",
            )
        except BaseException as exc:
            logger.critical("unable to retain late Daytona ownership cleanup", extra={"error_type": type(exc).__name__})
            return False
        task = execution.future
        owner.cleanup_task = task
        self._late_cleanup_tasks.add(task)
        task.add_done_callback(self._settled_late_cleanup)
        return True

    def _schedule_late_owner(self, owner: _LateOwner) -> bool:
        if owner.cleanup_task is not None and not owner.cleanup_task.done():
            return True
        awaitable = self._settle_late_owner(owner)
        try:
            task = self._cleanup.submit(awaitable)
        except BaseException:
            try:
                task = asyncio.create_task(awaitable, name="fleet-daytona-late-ownership-cleanup")
            except BaseException:
                with contextlib.suppress(BaseException):
                    awaitable.close()
                return self._schedule_late_owner_fallback(owner)
        owner.cleanup_task = task
        self._late_cleanup_tasks.add(task)
        task.add_done_callback(self._settled_late_cleanup)
        return True

    def _adopt_late_acquisition(
        self,
        acquisition: asyncio.Task[InterpreterLease],
        permit: DaytonaAdmissionPermit,
        request: LeaseRequest,
        run_id: UUID,
    ) -> None:
        owner = _LateOwner(
            request=request,
            run_id=run_id,
            permit=permit,
            acquisition=acquisition,
        )
        self._late_owners[id(acquisition)] = owner
        if self._schedule_late_owner(owner):
            return

        def retry_after_settlement(_completed: asyncio.Future[Any]) -> None:
            if owner.cleanup_task is None:
                self._schedule_late_owner(owner)

        acquisition.add_done_callback(retry_after_settlement)

    async def _settle_late_lease(self, owner: _LateOwner) -> None:
        lease = owner.lease
        assert lease is not None
        assert owner.permit is not None
        release_error: BaseException | None = None
        try:
            release_task = asyncio.create_task(asyncio.to_thread(lease.release))
            await OwnedEffect.from_task(release_task).settle()
        except BaseException as exc:
            release_error = exc

        quarantine_error: BaseException | None = None
        if release_error is None:
            try:
                await self._quarantine(
                    lease,
                    LeaseRequest(
                        session_id=owner.request.session_id,
                        user_id=owner.request.user_id,
                        workspace_id=UUID(str(lease.workspace_id)) if lease.workspace_id else UUID(int=0),
                        run_id=owner.run_id,
                    ),
                )
            except BaseException as exc:
                quarantine_error = exc

        if release_error is not None or quarantine_error is not None:
            return

        owner.permit.release()
        self._active_leases.release(
            owner.request.session_id,
            owner.run_id,
            workspace_id=owner.request.workspace_id,
        )
        self._mark_sandbox_released(lease.sandbox_id)
        self._late_owners.pop(id(lease), None)

    async def _retry_late_owners(self, deadline: float) -> bool:
        current_loop = asyncio.get_running_loop()
        tasks: list[asyncio.Future[Any]] = []
        unique_owners: list[_LateOwner] = []
        seen_owner_ids: set[int] = set()
        for owner in self._late_owners.values():
            owner_id = id(owner)
            if owner_id in seen_owner_ids:
                continue
            seen_owner_ids.add(owner_id)
            unique_owners.append(owner)
        for owner in unique_owners:
            if owner.unpublished:
                awaitable = self._settle_late_owner(owner, deadline=deadline)
                try:
                    task = asyncio.create_task(awaitable, name="fleet-daytona-unpublished-lease-retry")
                except BaseException:
                    with contextlib.suppress(BaseException):
                        awaitable.close()
                    continue
                owner.cleanup_task = task
                self._late_cleanup_tasks.add(task)
                task.add_done_callback(self._settled_late_cleanup)
                tasks.append(task)
                continue
            if owner.acquisition is not None and owner.lease is None:
                if not owner.acquisition.done():
                    try:
                        acquisition_loop = owner.acquisition.get_loop()
                    except BaseException:
                        acquisition_loop = None
                    if acquisition_loop is not current_loop:
                        continue
                task = owner.cleanup_task
                if task is None or task.done():
                    self._schedule_late_owner(owner)
                    task = owner.cleanup_task
                if task is not None:
                    if isinstance(task, asyncio.Future):
                        tasks.append(task)
                    else:
                        tasks.append(asyncio.wrap_future(task))
                continue
            task = owner.cleanup_task
            if task is None or task.done():
                task = asyncio.create_task(self._settle_late_lease(owner), name="fleet-daytona-late-lease-retry")
                owner.cleanup_task = task
                self._late_cleanup_tasks.add(task)
                task.add_done_callback(self._settled_late_cleanup)
            tasks.append(task)
        if not tasks:
            return not self._late_owners
        remaining = max(0.0, deadline - current_loop.time())
        _, pending = await asyncio.wait(tuple(tasks), timeout=remaining)
        return not pending and not self._late_owners

    def _settled_late_cleanup(self, task: Any) -> None:
        self._late_cleanup_tasks.discard(task)
        if task.cancelled():
            return
        with contextlib.suppress(BaseException):
            error = task.exception()
        if error is not None:
            logger.warning("late Daytona ownership cleanup failed", extra={"error_type": type(error).__name__})

    async def _quarantine(
        self, lease: InterpreterLease, request: LeaseRequest, *, deadline: float | None = None
    ) -> None:
        if lease.requires_sandbox_deletion:
            await self._persist_native_binding_state(
                lease,
                request,
                provider_state="fencing",
                deadline=deadline,
            )
            timeout_s = 30.0
            if deadline is not None:
                timeout_s = max(0.1, min(timeout_s, deadline - asyncio.get_running_loop().time()))
            retirement = self._sandbox_retirement_lease(
                lease.sandbox_id, confirm_timeout_s=timeout_s, provider_request_timeout_s=timeout_s
            )
            receipt = await retirement.aclose()
            if not receipt.clean:
                raise RuntimeError("native sandbox deletion was not confirmed")
            await self._persist_native_binding_state(
                lease,
                request,
                provider_state="quarantined",
                deadline=deadline,
            )
            return
        await self._fence_binding(
            SandboxBinding(
                session_id=request.session_id,
                sandbox_id=lease.sandbox_id,
                workspace_id=request.workspace_id,
                volume_id=lease.volume_id,
                volume_subpath=lease.volume_subpath or workspace_volume_subpath(request.workspace_id),
                mount_path=lease.mount_path,
                provider_state="running",
                generation=lease.binding_generation,
            ),
            deadline=deadline,
        )
        if lease.created_sandbox:
            retire = self._sandbox_retirement_lease(lease.sandbox_id)
            receipt_box: dict[str, SandboxLeaseReceipt] = {}

            async def _retire() -> None:
                receipt_box["receipt"] = await retire.aclose()

            deletion = _retire()
            try:
                deletion_task = self._cleanup.submit(deletion)
            except BaseException as scheduler_error:
                try:
                    deletion_task = asyncio.create_task(deletion, name="fleet-daytona-late-sandbox-retirement")
                except BaseException:
                    with contextlib.suppress(BaseException):
                        deletion.close()
                    raise RuntimeError("sandbox retirement ownership unavailable") from scheduler_error
            await deletion_task
            receipt = receipt_box["receipt"]
            if not receipt.clean:
                raise RuntimeError("sandbox retirement was not confirmed")

    async def _persist_native_binding_state(
        self,
        lease: InterpreterLease,
        request: LeaseRequest,
        *,
        provider_state: str,
        deadline: float | None = None,
    ) -> None:
        binding = await self._get_binding_for_workspace(
            request.session_id,
            request.workspace_id,
            deadline=deadline,
        )
        if binding is None or binding.sandbox_id != lease.sandbox_id or binding.generation != lease.binding_generation:
            return
        persisted = await _provider_call(
            self._bindings.upsert(replace(binding, provider_state=provider_state, last_verified_at=datetime.now(UTC))),
            deadline=deadline,
            operation=f"Native Sandbox {provider_state} persistence",
            owner=self._provider_tasks,
        )
        self._observe_binding(persisted)

    async def _get_binding_for_workspace(
        self,
        session_id: UUID,
        workspace_id: UUID,
        *,
        deadline: float | None = None,
    ) -> SandboxBinding | None:
        async def read(awaitable: Awaitable[Any]) -> Any:
            return await _provider_call(
                awaitable,
                deadline=deadline,
                operation="Sandbox binding lookup",
                owner=self._provider_tasks,
            )

        scoped_get = getattr(self._bindings, "get_scoped", None)
        if callable(scoped_get):
            binding = await read(scoped_get(session_id, workspace_id=workspace_id))
            if binding is not None:
                self._observe_binding(binding)
                return binding
            unscoped = await read(self._bindings.get(session_id))
            if unscoped is not None:
                raise DaytonaAdapterError(
                    message="sandbox binding does not match workspace scope",
                    cause_type="WorkspaceMountMismatch",
                )
            return None
        binding = await read(self._bindings.get(session_id))
        if binding is not None and binding.workspace_id != workspace_id:
            raise DaytonaAdapterError(
                message="sandbox binding does not match workspace scope",
                cause_type="WorkspaceMountMismatch",
            )
        self._observe_binding(binding)
        return binding

    async def fence_session(
        self,
        session_id: UUID,
        *,
        workspace_id: UUID | None = None,
        deadline: float | None = None,
    ) -> None:
        if workspace_id is not None:
            binding = await self._get_binding_for_workspace(session_id, workspace_id, deadline=deadline)
        else:
            binding = await _provider_call(
                self._bindings.get(session_id),
                deadline=deadline,
                operation="Sandbox binding lookup",
                owner=self._provider_tasks,
            )
        if binding is None or not binding.sandbox_id:
            return
        await self._fence_binding(binding, deadline=deadline)

    async def _fence_binding(self, binding: SandboxBinding, *, deadline: float | None = None) -> None:
        fenced = await _provider_call(
            self._bindings.upsert(replace(binding, provider_state="fencing", last_verified_at=datetime.now(UTC))),
            deadline=deadline,
            operation="Sandbox fence persistence",
            owner=self._provider_tasks,
        )
        self._observe_binding(fenced)
        if binding.sandbox_id is None:
            return
        timeout_s = 30.0
        if deadline is not None:
            timeout_s = max(0.1, min(timeout_s, deadline - asyncio.get_running_loop().time()))
        fence_lease = SandboxLease(
            kind="recovery_fence",
            sandbox=None,
            sandbox_id=binding.sandbox_id,
            platform=self._platform,
            policy=SandboxLeasePolicy(
                kind="recovery_fence",
                interpreter_shutdown=False,
                provider_action="stop",
                stop_force=True,
                confirm_timeout_s=timeout_s,
                provider_request_timeout_s=timeout_s,
            ),
        )

        async def _fenced_stop() -> None:
            receipt = await fence_lease.aclose()
            if receipt.first_error is not None:
                raise RuntimeError(str(receipt.first_error))

        await _provider_call(
            _fenced_stop(),
            deadline=deadline,
            operation="Sandbox fencing",
            owner=self._provider_tasks,
        )
        quarantined = await _provider_call(
            self._bindings.upsert(replace(binding, provider_state="quarantined", last_verified_at=datetime.now(UTC))),
            deadline=deadline,
            operation="Sandbox quarantine persistence",
            owner=self._provider_tasks,
        )
        self._observe_binding(quarantined)

    def _bind_lease_ownership(
        self,
        lease: InterpreterLease,
        permit: DaytonaAdmissionPermit,
        *,
        session_id: UUID,
        workspace_id: UUID,
        run_id: UUID,
    ) -> None:
        def _clear_active() -> None:
            try:
                permit.release()
            finally:
                self._mark_sandbox_released(lease.sandbox_id)
                self._active_leases.release(session_id, run_id, workspace_id=workspace_id)

        lease._on_release = _clear_active

    async def _acquire_provider(
        self,
        request: LeaseRequest,
        *,
        run_id: UUID,
        deadline: float | None = None,
        force_new: bool = False,
    ) -> InterpreterLease:
        context: _AcquisitionContext | None = None
        sandbox: Any | None = None
        created_sandbox = False
        try:
            context = await self._resolve_acquisition_context(request, deadline=deadline)
            sandbox, created_sandbox = await self._prepare_sandbox(
                request,
                context,
                deadline=deadline,
                force_new=force_new,
            )
            await self._verify_run_layout(
                sandbox, context.expected, request.session_id, run_id, created_sandbox, deadline=deadline
            )
            return await self._persist_binding_and_build_lease(
                request,
                run_id,
                context.expected,
                sandbox,
                created_sandbox,
                deadline=deadline,
                context=context,
            )
        except _ProviderCallDeadlineError as exc:
            with contextlib.suppress(BaseException):
                await _settle_provider_task(exc.task)
            if context is not None and sandbox is not None:
                await self._cleanup_failed_acquisition(
                    request,
                    sandbox,
                    created_sandbox=created_sandbox,
                    deadline=deadline,
                    binding=context.persisted_binding,
                )
            raise DaytonaLeaseAcquisitionTimeoutError(f"Daytona {exc.operation} timed out") from None
        except BaseException:
            if context is not None and sandbox is not None:
                await self._cleanup_failed_acquisition(
                    request,
                    sandbox,
                    created_sandbox=created_sandbox,
                    deadline=deadline,
                    binding=context.persisted_binding,
                )
            raise

    async def _resolve_acquisition_context(
        self, request: LeaseRequest, *, deadline: float | None = None
    ) -> _AcquisitionContext:
        volume_id = await self._resolve_volume_id(deadline=deadline)
        expected = self._expected_mount(volume_id=volume_id, workspace_id=request.workspace_id)
        binding = await self._get_binding_for_workspace(request.session_id, request.workspace_id, deadline=deadline)
        if binding is not None and binding.provider_state == "fencing":
            raise DaytonaAdapterError(
                message="sandbox execution fence is not confirmed",
                cause_type="SandboxFenceUnconfirmed",
            )
        return _AcquisitionContext(expected, binding)

    async def _prepare_sandbox(
        self,
        request: LeaseRequest,
        context: _AcquisitionContext,
        *,
        deadline: float | None = None,
        force_new: bool = False,
    ) -> tuple[Any, bool]:
        sandbox = await self._reuse_bound_sandbox(request, context, deadline=deadline, force_new=force_new)
        created_sandbox = sandbox is None or force_new
        if created_sandbox:
            sandbox = await self._create_sandbox(
                volume_id=context.expected.volume_id,
                mount_path=context.expected.mount_path,
                volume_subpath=context.expected.volume_subpath,
                request=request,
                deadline=deadline,
            )
        return sandbox, created_sandbox

    async def _reuse_bound_sandbox(
        self,
        request: LeaseRequest,
        context: _AcquisitionContext,
        *,
        deadline: float | None = None,
        force_new: bool = False,
    ) -> Any | None:
        binding = context.binding
        if binding is None or not binding.sandbox_id or binding.provider_state in {"quarantined", "unrecoverable"}:
            return None
        if not binding_matches_expected(binding, context.expected):
            raise DaytonaAdapterError(
                message="sandbox binding does not match workspace scope",
                cause_type="WorkspaceMountMismatch",
            )
        if force_new:
            replacement = await self.replace(
                replace(binding, provider_state="unrecoverable", last_verified_at=None),
                workspace_id=request.workspace_id,
                user_id=request.user_id,
                deadline=deadline,
            )
            replacement_id = replacement.sandbox_id
            if not replacement_id:
                raise DaytonaAdapterError(
                    message="sandbox replacement did not produce a sandbox id",
                    cause_type="SandboxReplaceIdentityError",
                )
            replacement_sandbox = await self._get_bound_sandbox(replacement_id, deadline=deadline)
            if replacement_sandbox is None:
                raise DaytonaAdapterError(
                    message="replacement sandbox is not retrievable",
                    cause_type="SandboxUnrecoverable",
                )
            return replacement_sandbox
        sandbox = await self._get_bound_sandbox(binding.sandbox_id, deadline=deadline)
        if sandbox is None:
            return None
        try:
            self._provisioner.verify(sandbox, context.expected)
            sandbox = await self._ensure_running(
                sandbox,
                sandbox_state(sandbox),
                volume_id=context.expected.volume_id,
                mount_path=context.expected.mount_path,
                deadline=deadline,
            )
            self._provisioner.verify(sandbox, context.expected)
            return sandbox
        except ProviderRequestError:
            raise
        except DaytonaAdapterError as exc:
            if exc.cause_type not in {"SandboxUnrecoverable", "SandboxSnapshotMismatch"}:
                raise
            replacement = await self.replace(
                replace(binding, provider_state="unrecoverable", last_verified_at=None),
                workspace_id=request.workspace_id,
                user_id=request.user_id,
                deadline=deadline,
            )
            replacement_id = replacement.sandbox_id
            if not replacement_id:
                raise DaytonaAdapterError(
                    message="sandbox replacement did not produce a sandbox id",
                    cause_type="SandboxReplaceIdentityError",
                ) from exc
            replacement_sandbox = await self._get_bound_sandbox(replacement_id, deadline=deadline)
            if replacement_sandbox is None:
                raise DaytonaAdapterError(
                    message="replacement sandbox is not retrievable",
                    cause_type="SandboxUnrecoverable",
                ) from exc
            return replacement_sandbox

    async def _get_bound_sandbox(self, sandbox_id: str, *, deadline: float | None = None) -> Any | None:
        try:
            return await _provider_call(
                self._platform.get(sandbox_id),
                deadline=deadline,
                operation="Sandbox lookup",
                owner=self._provider_tasks,
            )
        except ProviderRequestError:
            raise
        except DaytonaAdapterError:
            raise
        except _ProviderCallDeadlineError:
            raise
        except Exception as exc:
            raise map_provider_error(exc) from exc

    async def _verify_run_layout(
        self,
        sandbox: Any,
        expected: ExpectedWorkspaceMount,
        session_id: UUID,
        run_id: UUID,
        created_sandbox: bool,
        deadline: float | None = None,
    ) -> None:
        del created_sandbox
        await _provider_call(
            self._provisioner.verify_run_layout(
                sandbox,
                expected,
                session_id=session_id,
                run_id=run_id,
            ),
            deadline=deadline,
            operation="Sandbox verification",
            owner=self._provider_tasks,
        )

    async def _cleanup_failed_acquisition(
        self,
        request: LeaseRequest,
        sandbox: Any,
        *,
        created_sandbox: bool,
        deadline: float | None = None,
        binding: SandboxBinding | None = None,
    ) -> None:
        sandbox_id = _sandbox_id(sandbox)
        candidate = binding
        durable_read_failed = False
        try:
            durable_binding = await self._get_binding_for_workspace(
                request.session_id,
                request.workspace_id,
                deadline=deadline,
            )
        except Exception:
            durable_read_failed = True
            durable_binding = None
        if not durable_read_failed:
            if (
                durable_binding is None
                or durable_binding.sandbox_id != sandbox_id
                or (
                    candidate is not None
                    and (
                        candidate.sandbox_id != durable_binding.sandbox_id
                        or candidate.generation != durable_binding.generation
                    )
                )
            ):
                candidate = None
            else:
                candidate = durable_binding
        if candidate is not None and candidate.sandbox_id == sandbox_id:
            state = "quarantined" if created_sandbox else "fencing"
            with contextlib.suppress(BaseException):
                fenced = await _provider_call(
                    self._bindings.upsert(replace(candidate, provider_state=state, last_verified_at=None)),
                    deadline=deadline,
                    operation="Failed Sandbox fencing persistence",
                    owner=self._provider_tasks,
                )
                self._observe_binding(fenced)

        interpreter: DaytonaCodeInterpreter | None = None
        with contextlib.suppress(BaseException):
            interpreter = _build_interpreter(
                sandbox,
                loop=asyncio.get_running_loop(),
                dispatcher=self._dispatcher,
                execution_output_cap=self._execution_output_cap,
                execution_timeout_s=self._execution_timeout_s,
            )

        cleanup = SandboxLease(
            kind="retained_session" if created_sandbox else "recovery_fence",
            sandbox=sandbox,
            sandbox_id=sandbox_id,
            platform=self._platform,
            interpreter=interpreter,
            policy=SandboxLeasePolicy(
                kind="retained_session" if created_sandbox else "recovery_fence",
                provider_action="delete" if created_sandbox else "stop",
                stop_force=not created_sandbox,
                confirm_timeout_s=30.0,
                provider_request_timeout_s=30.0,
            ),
        )
        try:
            receipt = await cleanup.aclose(deadline=deadline)
        except TimeoutError:
            with contextlib.suppress(BaseException):
                await cleanup.wait_ownership()
            return
        except BaseException:
            with contextlib.suppress(BaseException):
                await cleanup.wait_ownership()
            return
        if not receipt.clean:
            with contextlib.suppress(BaseException):
                await cleanup.wait_ownership()

    async def _persist_binding_and_build_lease(
        self,
        request: LeaseRequest,
        run_id: UUID,
        expected: ExpectedWorkspaceMount,
        sandbox: Any,
        created_sandbox: bool,
        deadline: float | None = None,
        context: _AcquisitionContext | None = None,
    ) -> InterpreterLease:
        session_id = request.session_id
        sid = _sandbox_id(sandbox)
        prior_binding = await self._get_binding_for_workspace(session_id, request.workspace_id, deadline=deadline)
        if prior_binding is None:
            binding_generation = 1
        elif (
            prior_binding.sandbox_id == sid
            and prior_binding.provider_state == "running"
            and self.is_binding_current(
                session_id=session_id,
                workspace_id=request.workspace_id,
                sandbox_id=sid,
                generation=prior_binding.generation,
            )
        ):
            binding_generation = prior_binding.generation
        else:
            binding_generation = prior_binding.generation + 1
        candidate = SandboxBinding(
            session_id=session_id,
            sandbox_id=sid,
            workspace_id=request.workspace_id,
            volume_id=expected.volume_id,
            volume_subpath=expected.volume_subpath,
            mount_path=expected.mount_path,
            provider_state="running",
            last_verified_at=datetime.now(UTC),
            generation=binding_generation,
        )
        atomic_replace = getattr(self._bindings, "replace_with_next_generation", None)
        is_replacement = prior_binding is not None and prior_binding.sandbox_id != sid
        persist = (
            atomic_replace(candidate)
            if is_replacement and callable(atomic_replace)
            else self._bindings.upsert(candidate)
        )
        persisted = await _provider_call(
            persist,
            deadline=deadline,
            operation="Sandbox binding persistence",
            owner=self._provider_tasks,
        )
        self._observe_binding(persisted)
        if context is not None:
            context.persisted_binding = persisted
        binding_generation = persisted.generation
        interpreter = _build_interpreter(
            sandbox,
            loop=asyncio.get_running_loop(),
            dispatcher=self._dispatcher,
            execution_output_cap=self._execution_output_cap,
            execution_timeout_s=self._execution_timeout_s,
        )
        return InterpreterLease(
            sandbox_id=sid,
            interpreter_id=f"interp-{sid}-{uuid4().hex[:8]}",
            volume_id=expected.volume_id,
            mount_path=expected.mount_path,
            volume_subpath=expected.volume_subpath,
            interpreter=interpreter,
            sandbox=sandbox,
            session_id=str(session_id),
            user_id=str(request.user_id),
            run_id=str(run_id),
            workspace_id=str(request.workspace_id),
            created_sandbox=created_sandbox,
            binding_generation=binding_generation,
        )

    def _start_release_task(self, lease: InterpreterLease) -> asyncio.Task[None]:
        for task, known in tuple(self._release_leases.items()):
            if known is lease and not task.done():
                return task
        release_task = asyncio.create_task(
            asyncio.to_thread(lease.release),
            name="fleet-daytona-interpreter-release",
        )
        self._release_tasks.add(release_task)
        self._release_leases[release_task] = lease
        release_task.add_done_callback(lambda task: self._settled_release_task(lease, task))
        return release_task

    async def _release_interpreter(self, lease: InterpreterLease) -> None:
        release_task = self._start_release_task(lease)
        await asyncio.shield(release_task)
        self._settled_release_task(lease, release_task)

    async def release(self, lease: InterpreterLease) -> None:
        unpublished = self._late_owners.get(id(lease))
        if unpublished is not None and unpublished.unpublished:
            await self._finish_unpublished_lease(unpublished)
            return
        if lease.requires_sandbox_deletion and not lease._provider_retired:
            if lease.session_id is None or lease.workspace_id is None or lease.run_id is None or lease.user_id is None:
                raise RuntimeError("native sandbox retirement requires complete lease ownership")
            await self.release_and_quarantine(
                lease,
                LeaseRequest(
                    session_id=UUID(lease.session_id),
                    workspace_id=UUID(lease.workspace_id),
                    user_id=UUID(lease.user_id),
                    run_id=UUID(lease.run_id),
                ),
            )
            return
        await self._release_interpreter(lease)

    async def _finish_unpublished_lease(
        self,
        owner: _LateOwner,
        *,
        deadline: float | None = None,
    ) -> None:
        async with owner.cleanup_lock:
            lease = owner.lease
            assert lease is not None
            if lease._provider_retired:
                self._late_owners.pop(id(lease), None)
                return
            lease._defer_owner_release = True
            lease._defer_idle_cleanup = True
            if not lease._released:
                await self._release_interpreter(lease)
            await self._quarantine(lease, owner.request, deadline=deadline)

            callback = lease._on_release
            if not owner.callback_settled:
                if owner.callback_started:
                    raise RuntimeError("unpublished lease finalization remains unresolved")
                owner.callback_started = True
                try:
                    if callback is not None:
                        callback()
                except BaseException:
                    raise
                owner.callback_settled = True
            lease._provider_retired = True
            lease._defer_owner_release = False
            lease._defer_idle_cleanup = False
            self._late_owners.pop(id(lease), None)

    async def release_and_quarantine(
        self,
        lease: InterpreterLease,
        request: LeaseRequest,
        *,
        deadline: float | None = None,
    ) -> None:
        owner = self._late_owners.get(id(lease))
        if owner is None or not owner.unpublished:
            owner = _LateOwner(
                request=request,
                run_id=request.run_id or UUID(int=0),
                lease=lease,
                unpublished=True,
            )
            self._late_owners[id(lease)] = owner
        else:
            owner.request = request
        await self._finish_unpublished_lease(owner, deadline=deadline)

    async def quarantine(
        self,
        lease: InterpreterLease,
        request: LeaseRequest,
        *,
        deadline: float | None = None,
    ) -> None:
        await self._quarantine(lease, request, deadline=deadline)

    def _settled_release_task(self, lease: InterpreterLease, task: asyncio.Task[None]) -> None:
        if task in self._handled_release_tasks:
            self._handled_release_tasks.discard(task)
            return
        self._handled_release_tasks.add(task)
        self._release_tasks.discard(task)
        if task.cancelled():
            return
        try:
            task.result()
        except BaseException as exc:
            logger.warning(
                "Daytona interpreter release failed",
                extra={"sandbox_id": lease.sandbox_id, "error_type": type(exc).__name__},
            )
            return
        for owned_task, owned_lease in tuple(self._release_leases.items()):
            if owned_lease is lease:
                self._release_leases.pop(owned_task, None)
        if lease._defer_idle_cleanup or lease._provider_retired:
            return
        if self._idle_stop_seconds is None or lease.session_id is None:
            return
        session_id = UUID(lease.session_id)
        workspace_id = UUID(lease.workspace_id) if lease.workspace_id else UUID(int=0)
        idle_key = self._idle_key(session_id, workspace_id)
        self._request_cancel_idle_stop(session_id, workspace_id=workspace_id)
        idle_task = asyncio.create_task(
            self._stop_after_idle(
                session_id=session_id,
                sandbox_id=lease.sandbox_id,
                workspace_id=lease.workspace_id,
                delay=self._idle_stop_seconds,
            ),
            name="fleet-daytona-idle-stop",
        )
        self._idle_tasks[idle_key] = idle_task
        idle_task.add_done_callback(lambda completed, key=idle_key: self._forget_idle_task(key, completed))

    @staticmethod
    def _idle_key(session_id: UUID, workspace_id: UUID | None) -> tuple[UUID, UUID]:
        return (workspace_id or UUID(int=0), session_id)

    def _find_idle_task(
        self,
        session_id: UUID,
        workspace_id: UUID | None,
    ) -> tuple[tuple[UUID, UUID], asyncio.Task[None]] | None:
        if workspace_id is not None:
            key = self._idle_key(session_id, workspace_id)
            task = self._idle_tasks.get(key)
            return (key, task) if task is not None else None
        matches = [(key, task) for key, task in self._idle_tasks.items() if key[1] == session_id]
        return matches[0] if len(matches) == 1 else None

    async def _cancel_idle_stop(
        self,
        session_id: UUID,
        *,
        workspace_id: UUID | None = None,
        deadline: float | None = None,
    ) -> None:
        found = self._find_idle_task(session_id, workspace_id)
        if found is None:
            return
        key, task = found
        task.cancel()
        try:
            if deadline is None:
                await asyncio.shield(task)
            else:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise TimeoutError
                await asyncio.wait_for(asyncio.shield(task), timeout=remaining)
        except TimeoutError:
            raise DaytonaLeaseAcquisitionTimeoutError("Daytona idle-stop cleanup timed out") from None
        except asyncio.CancelledError:
            if task.cancelled():
                self._forget_idle_task(key, task)
                return
            raise

    def _request_cancel_idle_stop(self, session_id: UUID, *, workspace_id: UUID | None = None) -> None:
        found = self._find_idle_task(session_id, workspace_id)
        if found is not None:
            found[1].cancel()

    def _forget_idle_task(self, key: tuple[UUID, UUID], task: asyncio.Task[None]) -> None:
        if self._idle_tasks.get(key) is task:
            self._idle_tasks.pop(key, None)

    async def _stop_after_idle(
        self,
        *,
        session_id: UUID,
        sandbox_id: str,
        workspace_id: str | None,
        delay: float,
    ) -> None:
        await asyncio.sleep(delay)
        workspace_scope = UUID(workspace_id) if workspace_id is not None else None
        if self._idle_stop_blocked(session_id, workspace_scope):
            return
        if workspace_id is not None:
            assert workspace_scope is not None
            binding = await self._get_binding_for_workspace(session_id, workspace_scope)
        else:
            binding = await _provider_call(
                self._bindings.get(session_id),
                deadline=None,
                operation="Idle Sandbox binding lookup",
                owner=self._provider_tasks,
            )
        if binding is None or binding.sandbox_id != sandbox_id or binding.provider_state != "running":
            return
        sandbox = await self._get_bound_sandbox(sandbox_id)
        if sandbox is None or self._idle_stop_blocked(session_id, workspace_scope):
            return

        stop_task = asyncio.create_task(self._platform.stop(sandbox_id))
        _retain_provider_task(stop_task, self._provider_tasks)
        try:
            await asyncio.shield(stop_task)
        except asyncio.CancelledError:
            await asyncio.shield(stop_task)
            raise
        if self._idle_stop_blocked(session_id, workspace_scope):
            return
        if workspace_id is not None:
            assert workspace_scope is not None
            latest = await self._get_binding_for_workspace(session_id, workspace_scope)
        else:
            latest = await _provider_call(
                self._bindings.get(session_id),
                deadline=None,
                operation="Idle Sandbox binding lookup",
                owner=self._provider_tasks,
            )
        if latest is None or latest.sandbox_id != sandbox_id or latest.provider_state != "running":
            return
        self.revoke_binding(
            session_id=session_id,
            workspace_id=workspace_scope or latest.workspace_id,
            sandbox_id=sandbox_id,
            generation=latest.generation,
        )
        update = asyncio.ensure_future(
            self._bindings.upsert(
                replace(
                    latest,
                    provider_state="stopped",
                    last_verified_at=datetime.now(UTC),
                    generation=latest.generation + 1,
                )
            )
        )
        _retain_provider_task(update, self._provider_tasks)
        try:
            persisted = await asyncio.shield(update)
            self._observe_binding(persisted)
        except asyncio.CancelledError:
            await OwnedEffect.from_task(update).settle()
            raise

    async def aclose(self, *, drain_seconds: float = 30.0) -> bool:
        if drain_seconds < 0:
            raise ValueError("drain_seconds must be non-negative")
        deadline = asyncio.get_running_loop().time() + drain_seconds
        idle = tuple(self._idle_tasks.values())
        for task in idle:
            task.cancel()
        release = tuple(self._release_tasks)
        provider = tuple(self._provider_tasks)
        all_tasks = tuple(dict.fromkeys((*idle, *release, *provider, *self._late_cleanup_tasks)))
        pending: set[asyncio.Future[Any]] = set()
        for task in all_tasks:
            if isinstance(task, asyncio.Future):
                pending.add(task)
            else:
                pending.add(asyncio.wrap_future(task))
        if pending:
            remaining = max(0.0, deadline - asyncio.get_running_loop().time())
            _, pending = await asyncio.wait(pending, timeout=remaining)
        if pending:
            return False

        retry_release = [
            self._start_release_task(lease) for lease in tuple(self._release_leases.values()) if not lease._released
        ]
        if retry_release:
            remaining = max(0.0, deadline - asyncio.get_running_loop().time())
            _, retry_pending = await asyncio.wait(tuple(retry_release), timeout=remaining)
            if retry_pending or any(not lease._released for lease in self._release_leases.values()):
                return False

        return await self._retry_late_owners(deadline)

    def _retain_late_created_sandbox(self, task: asyncio.Future[Any]) -> None:
        async def retire_late() -> None:
            try:
                sandbox = await asyncio.shield(task)
            except BaseException:
                return
            if sandbox is None:
                return
            with contextlib.suppress(BaseException):
                await self._sandbox_retirement_lease(_sandbox_id(sandbox)).aclose()

        coroutine = retire_late()
        try:
            cleanup = asyncio.create_task(coroutine, name="fleet-daytona-late-sandbox-creation-cleanup")
        except BaseException:
            coroutine.close()
            return
        _retain_provider_task(cleanup, self._provider_tasks)

    async def _resolve_volume_id(self, *, deadline: float | None = None) -> str:
        for attempt in range(2):
            try:
                return await _provider_call(
                    get_or_create_volume_id(self._volume_client, self._volume_config),
                    deadline=deadline,
                    operation="Volume resolution",
                    owner=self._provider_tasks,
                )
            except _ProviderCallDeadlineError:
                raise
            except Exception as exc:
                mapped = map_provider_error(exc)
                if attempt == 0 and is_safe_pre_creation_retry(mapped):
                    continue
                if mapped is exc:
                    raise
                raise mapped from exc
        raise AssertionError("unreachable")

    async def replace(
        self,
        binding: SandboxBinding,
        *,
        workspace_id: UUID | None = None,
        user_id: UUID | None = None,
        deadline: float | None = None,
    ) -> SandboxBinding:
        resolved_workspace = workspace_id or binding.workspace_id
        require_non_zero_workspace_id(resolved_workspace)
        if workspace_id is not None and binding.workspace_id != workspace_id:
            raise DaytonaAdapterError(
                message="sandbox binding does not match workspace scope",
                cause_type="WorkspaceMountMismatch",
            )
        if user_id is None or user_id == UUID(int=0):
            raise DaytonaAdapterError(
                message="replace requires a real user_id (zero UUID is forbidden)",
                cause_type="SandboxReplaceIdentityError",
            )
        volume_id = binding.volume_id or await self._resolve_volume_id(deadline=deadline)
        expected = self._expected_mount(volume_id=volume_id, workspace_id=resolved_workspace)
        if binding.sandbox_id:
            retirement = self._sandbox_retirement_lease(binding.sandbox_id)
            try:
                receipt = await retirement.aclose(deadline=deadline)
            except TimeoutError as exc:
                raise DaytonaAdapterError(
                    message="sandbox retirement timed out",
                    cause_type="SandboxRetirementTimeout",
                ) from exc
            if not receipt.clean:
                if deadline is None:
                    await retirement.wait_ownership()
                else:
                    try:
                        remaining = deadline - asyncio.get_running_loop().time()
                        if remaining > 0:
                            await retirement.wait_ownership(timeout=remaining)
                    except TimeoutError:
                        pass
                raise DaytonaAdapterError(
                    message="sandbox retirement was not confirmed",
                    cause_type="SandboxRetirementUnconfirmed",
                )
        request = LeaseRequest(
            session_id=binding.session_id,
            user_id=user_id,
            workspace_id=resolved_workspace,
        )
        sandbox: Any | None = None
        try:
            sandbox = await self._create_sandbox(
                volume_id=expected.volume_id,
                mount_path=expected.mount_path,
                volume_subpath=expected.volume_subpath,
                request=request,
                deadline=deadline,
                settle_on_deadline=False,
            )
            self._provisioner.verify(sandbox, expected)
            new_binding = SandboxBinding(
                session_id=binding.session_id,
                sandbox_id=_sandbox_id(sandbox),
                workspace_id=resolved_workspace,
                volume_id=expected.volume_id,
                volume_subpath=expected.volume_subpath,
                mount_path=expected.mount_path,
                provider_state="running",
                last_verified_at=datetime.now(UTC),
                generation=binding.generation + 1,
            )
            atomic_replace = getattr(self._bindings, "replace_with_next_generation", None)
            persist = atomic_replace(new_binding) if callable(atomic_replace) else self._bindings.upsert(new_binding)
            persisted = await persist
            self._observe_binding(persisted)
            return persisted
        except BaseException:
            if sandbox is not None:
                with contextlib.suppress(BaseException):
                    await self._sandbox_retirement_lease(_sandbox_id(sandbox)).aclose()
            with contextlib.suppress(BaseException):
                await self._bindings.upsert(replace(binding, provider_state="quarantined", last_verified_at=None))
            raise

    async def _ensure_running(
        self,
        sandbox: Any,
        state: str,
        *,
        volume_id: str,
        mount_path: str,
        deadline: float | None = None,
    ) -> Any:
        del volume_id, mount_path
        if state == "running":
            return sandbox
        if state in {"stopped", "paused", "archived"}:
            try:
                await _provider_call(
                    self._platform.start(_sandbox_id(sandbox)),
                    deadline=deadline,
                    operation="Sandbox start",
                    owner=self._provider_tasks,
                )
                refreshed = await _provider_call(
                    self._platform.get(_sandbox_id(sandbox)),
                    deadline=deadline,
                    operation="Sandbox lookup",
                    owner=self._provider_tasks,
                )
                return refreshed or sandbox
            except _ProviderCallDeadlineError:
                raise
            except Exception as exc:
                raise map_provider_error(exc) from exc
        raise DaytonaAdapterError(
            message=f"sandbox unusable in state {state}",
            cause_type="SandboxUnrecoverable",
        )

    async def _create_sandbox(
        self,
        *,
        volume_id: str,
        mount_path: str,
        volume_subpath: str,
        request: LeaseRequest,
        deadline: float | None = None,
        settle_on_deadline: bool = True,
    ) -> Any:
        expected = ExpectedWorkspaceMount(
            volume_id=volume_id,
            volume_subpath=volume_subpath,
            mount_path=mount_path,
            workspace_id=request.workspace_id,
        )
        try:
            return await _provider_call(
                self._provisioner.create(
                    expected,
                    labels={
                        "session_id": str(request.session_id),
                        "user_id": str(request.user_id),
                        "workspace_id": str(request.workspace_id),
                        "fleet_package": "fleet_rlm",
                        "volume_subpath": expected.volume_subpath,
                    },
                    ephemeral=False,
                ),
                deadline=deadline,
                operation="Sandbox creation",
                owner=self._provider_tasks,
            )
        except _ProviderCallDeadlineError as exc:
            if settle_on_deadline:
                return await _settle_provider_task(exc.task)
            self._retain_late_created_sandbox(exc.task)
            raise DaytonaLeaseAcquisitionTimeoutError(f"Daytona {exc.operation} timed out") from None


__all__ = [
    "DEFAULT_IDLE_STOP_SECONDS",
    "PREWARM_RUN_ID",
    "ActiveLeaseConflictError",
    "ActiveLeaseRegistry",
    "BindingStoreLike",
    "DaytonaLeaseAcquisitionTimeoutError",
    "DaytonaSessionManager",
    "InterpreterLease",
    "LeaseKind",
    "LeaseRequest",
    "LeaseState",
    "OwnedCloseExecution",
    "RootSessionLease",
    "SandboxLease",
    "SandboxLeasePolicy",
    "SandboxLeaseReceipt",
    "binding_matches_expected",
    "has_pending_lease_ownership",
    "schedule_owned_close",
    "wait_lease_ownership",
    "workspace_volume_subpath",
]
