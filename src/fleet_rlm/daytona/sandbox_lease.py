"""Confirmed Sandbox Lease ownership for Daytona lifecycles (P20/QRE-155).

Provider lifecycle semantics previously lived in hand-rolled, subtly different
close paths per caller (retained Session release, recursive child cleanup,
temporary Volume-I/O teardown, startup recovery fencing): who releases the
admission permit relative to provider deletion, how a late acquisition gets
owned, where a blocking close runs, and what a quarantined cleanup records.
This module concentrates those semantics behind one deep lease seam:

- :class:`SandboxLeasePolicy` declares the lifecycle shape (purge policy,
  provider action, absence confirmation, admission coupling).
- :class:`SandboxLease` owns exactly one Sandbox handle and its
  :meth:`SandboxLease.close` (sync, worker-thread safe) plus
  :meth:`SandboxLease.aclose` (async) execution; repeated closes are
  idempotent and the close returns a typed :class:`SandboxLeaseReceipt`
  exposing interpreter/broker/provider/admission/quarantine outcomes.
- Late-acquisition ownership (P30) is adopted by the recursive child lease
  machinery and the runtime owned-effect queue instead of a parallel seam.

The seam never broadens public error surfaces: receipts carry typed statuses
and sanitized, credential-free error strings only.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
import time
from collections.abc import Awaitable, Callable, Coroutine
from concurrent.futures import Future
from dataclasses import dataclass
from threading import Thread
from typing import Any, Literal, Protocol, TypeAlias

from fleet_rlm.daytona._lease import LeaseState
from fleet_rlm.daytona.admission import DaytonaAdmissionPermit
from fleet_rlm.daytona.errors import sanitize_failure_text
from fleet_rlm.daytona.lifecycle import AbsenceConfirmation, AbsenceOutcome, confirm_absence
from fleet_rlm.daytona.provisioning import SandboxPlatform

logger = logging.getLogger(__name__)

# Strong process-local ownership for ordered cleanup that outlives the public
# close receipt.  The task retains its SandboxLease until provider settlement.
_DEFERRED_CLOSE_TASKS: set[asyncio.Task[None]] = set()
_PROVIDER_REQUEST_OWNERS: set[tuple[asyncio.Future[Any], Any]] = set()
# The initial close task itself must retain the lease until it publishes a
# receipt. This covers callers that time out/cancel before ``_close_core`` can
# create its later deferred continuation.
_CLOSE_TASK_OWNERS: set[tuple[asyncio.Future[Any], Any]] = set()
_FAILED_LEASE_OWNERS: dict[int, Any] = {}


def has_pending_lease_ownership() -> bool:
    """Return whether any process-owned Sandbox cleanup still needs its client."""
    deferred = any(not task.done() for task in _DEFERRED_CLOSE_TASKS)
    provider = any(not task.done() for task, _lease in _PROVIDER_REQUEST_OWNERS)
    close = any(not task.done() for task, _lease in _CLOSE_TASK_OWNERS)
    failed = bool(_FAILED_LEASE_OWNERS)
    return deferred or provider or close or failed


async def wait_lease_ownership(*, timeout: float | None = None) -> bool:
    """Wait for process-owned lease continuations without cancelling them."""
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
        # Completed close tasks can still be retained as failed owners (for
        # example after an owner loop was destroyed).  Do not report the
        # provider as disposable merely because no task remains awaitable.
        return not _FAILED_LEASE_OWNERS
    current_loop = asyncio.get_running_loop()
    # A task from a destroyed composition loop cannot be awaited safely from a
    # replacement loop. Treat it as unresolved ownership; the caller must keep
    # the provider client fenced rather than attempting cross-loop cancellation.
    if any(task.get_loop() is not current_loop for task in tasks):
        return False
    if timeout is None:
        await asyncio.gather(*(asyncio.shield(task) for task in tasks), return_exceptions=True)
        return not has_pending_lease_ownership()
    _, pending = await asyncio.wait(tasks, timeout=timeout)
    return not pending and not has_pending_lease_ownership()


__all__ = [
    "AdmissionOutcome",
    "CloseComponentOutcome",
    "InterpreterCloseOutcome",
    "LeaseCleanupError",
    "LeaseKind",
    "ProviderCleanupOutcome",
    "QuarantineOutcome",
    "SandboxLease",
    "SandboxLeasePolicy",
    "SandboxLeaseReceipt",
    "has_pending_lease_ownership",
    "wait_lease_ownership",
]

LeaseKind: TypeAlias = Literal["retained_session", "recursive_child", "volume_io", "recovery_fence"]

#: Bound for one blocking close result before ownership quarantines the
#: still-running close (it keeps the permit until it settles).
DEFAULT_CLOSE_RESULT_TIMEOUT_S = 60.0


class LeaseCleanupError(RuntimeError):
    """One lease close could not reach a clean confirmed outcome."""


@dataclass(frozen=True, slots=True)
class CloseComponentOutcome:
    """Close outcome for one owned component."""

    status: Literal["clean", "failed", "timed_out", "quarantined", "skipped", "not_present"]
    error: str | None = None


@dataclass(frozen=True, slots=True)
class InterpreterCloseOutcome:
    """Interpreter shutdown outcome, with the broker view surfaced separately.

    ``DaytonaCodeInterpreter.shutdown`` consolidates broker stop and backend
    close into one strict call; when it fails, the lease cannot prove which
    side failed without lying, so ``broker`` and ``backend`` both inherit the
    recorded ``status`` and ``error`` (a consolidated outcome, surfaced as
    such). ``has_broker`` records whether a broker was attached at close time.
    """

    status: Literal["clean", "failed", "timed_out", "quarantined", "skipped", "not_present"]
    broker: str = "not_present"
    backend: str = "not_present"
    error: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderCleanupOutcome:
    """Provider action taken during close and its confirmation state."""

    action: Literal["delete", "stop", "none"]
    requested: bool
    confirmed_absent: bool
    plateau: tuple[str, ...] = ()
    duration_s: float = 0.0
    error: str | None = None


@dataclass(frozen=True, slots=True)
class AdmissionOutcome:
    """When (and whether) the admission permit was released during close."""

    held: bool
    released: bool
    released_after: Literal["confirmed_cleanup", "quarantine_failure", "not_held"]


@dataclass(frozen=True, slots=True)
class QuarantineOutcome:
    """Whether the close quarantined still-running work instead of blocking on it."""

    quarantined: bool
    lane: Literal["owner_loop", "fallback_thread", "none"]
    error: str | None = None


@dataclass(frozen=True, slots=True)
class SandboxLeaseReceipt:
    """Complete typed record of one lease close (idempotent: repeated closes
    return the same receipt)."""

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
        """True when every component reached its clean terminal state."""
        provider_clean = (
            self.provider.action == "delete" and self.provider.requested and self.provider.confirmed_absent
        ) or (self.provider.action in {"stop", "none"} and self.provider.error is None)
        return (
            self.interpreter.status in {"clean", "skipped", "not_present"}
            and provider_clean
            and self.quarantine.error is None
            and self.first_error is None
        )


class LeasePurgeHook(Protocol):
    """Caller-supplied file purge policy executed before provider cleanup."""

    def __call__(self, sandbox: Any) -> Awaitable[None]: ...


@dataclass(frozen=True, slots=True)
class SandboxLeasePolicy:
    """Lifecycle shape declaration for one lease."""

    kind: LeaseKind
    provider_action: Literal["delete", "stop", "none"] = "delete"
    confirm_absence: bool = True
    confirm_timeout_s: float = 120.0
    confirm_poll_interval_s: float = 1.0
    # Test/contract seam for injecting a scripted confirmation policy; the
    # default is fleet_rlm.daytona.lifecycle.confirm_absence.
    confirm_fn: Callable[..., Awaitable[AbsenceOutcome]] | None = None
    interpreter_shutdown: bool = True
    strict_broker_cleanup: bool = True
    close_result_timeout_s: float = DEFAULT_CLOSE_RESULT_TIMEOUT_S
    # stop(force=True) silently DELETES on stop failure (platform.py) — that is
    # fencing semantics. Retained-session/idle semantics must never force.
    stop_force: bool = False
    # Bound on the provider's action REQUEST (delete/stop) itself. When set,
    # a hung request is abandoned client-side (the provider may still dequeue
    # it) and the close proceeds to confirmation. None = unbounded request.
    provider_request_timeout_s: float | None = None


def _retain_close_task(task: asyncio.Future[Any], lease: Any) -> None:
    """Retain the lease while its first close task is still settling."""
    _CLOSE_TASK_OWNERS.add((task, lease))

    def settled(completed: asyncio.Future[Any]) -> None:
        _CLOSE_TASK_OWNERS.discard((completed, lease))
        if completed.cancelled():
            _FAILED_LEASE_OWNERS[id(lease)] = lease
            return
        with contextlib.suppress(BaseException):
            error = completed.exception()
        if error is None:
            _FAILED_LEASE_OWNERS.pop(id(lease), None)
        else:
            _FAILED_LEASE_OWNERS[id(lease)] = lease

    task.add_done_callback(settled)


class SandboxLease:
    """Owns one Sandbox handle and its confirmed, idempotent close.

    Construction takes ONLY configuration and handles; no provider I/O happens
    until :meth:`close`/:meth:`aclose`. The close pipeline order is fixed:
    interpreter shutdown (strict broker cleanup per policy) → caller purge
    hook → provider action (delete/stop/none) → absence confirmation (per
    policy) → admission permit release. The permit is therefore always
    released strictly after its confirmed provider outcome or an explicit
    quarantine failure — never on request acceptance alone.
    """

    def __init__(
        self,
        *,
        kind: LeaseKind,
        sandbox: Any | None,
        sandbox_id: str | None,
        platform: SandboxPlatform | None,
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
        # Provider requests can outlive a bounded request wait.  Retain their
        # task on the lease so cancellation never turns an in-flight delete or
        # stop into an unowned side effect.
        self._provider_tasks: set[asyncio.Future[Any]] = set()

    @property
    def state(self) -> LeaseState:
        """Return the explicit cleanup state for this provider lease."""
        return self._state

    @property
    def closed(self) -> bool:
        # Historical callers use this as "the close receipt was published";
        # inspect ``state`` to distinguish a clean close from a failed one.
        return self._closed

    @property
    def closing(self) -> bool:
        return self._state is LeaseState.CLOSING

    @property
    def failed(self) -> bool:
        return self._state is LeaseState.FAILED

    @property
    def has_pending_ownership(self) -> bool:
        """Whether this lease still owns provider or deferred close work."""
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
            # Consolidated shutdown: broker/backend share the recorded failure.
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
        """Run strict interpreter shutdown off-loop under owned timeout semantics."""
        task = asyncio.create_task(asyncio.to_thread(self._shutdown_interpreter))
        self._interpreter_task = task
        try:
            if not bounded:
                return await task
            return await asyncio.wait_for(asyncio.shield(task), timeout=max(self._policy.close_result_timeout_s, 1.0))
        except TimeoutError:
            # The shutdown thread keeps ownership; the lease reports the
            # quarantine rather than dropping it silently.
            return InterpreterCloseOutcome(
                status="quarantined",
                broker="quarantined" if self._interpreter is not None else "not_present",
                backend="quarantined" if self._interpreter is not None else "not_present",
                error="interpreter shutdown quarantined past close bound",
            )

    def _retain_provider_task(self, task: asyncio.Future[Any]) -> None:
        """Keep a timed-out provider request strongly owned until it settles."""
        self._provider_tasks.add(task)
        _PROVIDER_REQUEST_OWNERS.add((task, self))

        def settled(completed: asyncio.Future[Any]) -> None:
            self._provider_tasks.discard(completed)
            _PROVIDER_REQUEST_OWNERS.discard((completed, self))
            if completed.cancelled():
                return
            with contextlib.suppress(BaseException):
                error = completed.exception()
            if error is not None:
                logger.warning(
                    "bounded Daytona provider request failed after close returned",
                    extra={"sandbox_id": self._sandbox_id, "error_type": type(error).__name__},
                )

        task.add_done_callback(settled)

    async def _run_provider_request(
        self,
        request: Awaitable[Any],
        *,
        timeout_s: float | None,
    ) -> str | None:
        """Run one provider request with owned timeout semantics."""
        task = asyncio.ensure_future(request)
        self._retain_provider_task(task)
        try:
            if timeout_s is None:
                await task
            else:
                await asyncio.wait_for(asyncio.shield(task), timeout=max(0.0, timeout_s))
        except TimeoutError:
            # Short-lived Volume-I/O sandboxes are local cleanup requests:
            # cancel and settle their coroutine before leaving the gateway.
            # Retained Session/root leases use the stronger late-provider
            # ownership path and leave the request running.
            if self._policy.kind == "volume_io":
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            return "provider request TimeoutError"
        except BaseException as exc:
            if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                return sanitize_failure_text(exc)
            return sanitize_failure_text(exc)
        return None

    async def _bounded_probe(self, sandbox_id: str) -> Any | None:
        """Bound a single provider lookup while retaining it when necessary."""
        assert self._platform is not None
        probe = getattr(self._platform, "get", None)
        if not callable(probe):
            raise RuntimeError("absence probe unavailable: platform lacks get")
        task = asyncio.ensure_future(probe(sandbox_id))
        self._retain_provider_task(task)
        # A probe must not consume the whole confirmation budget. A hung SDK
        # call is itself provider ownership; retained leases keep it, while
        # ephemeral volume-I/O leases cancel and settle it below.
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
            # Confirmation runs even when the delete request itself failed:
            # the Sandbox may be absent already, or deletion may have been
            # accepted provider-side despite the client error (QRE-151
            # semantics for every ephemeral lease).
            plateau: tuple[str, ...] = ()
            absent = False
            confirm_error: str | None = None
            probe = getattr(platform, "get", None)
            if policy.confirm_absence and not callable(probe):
                # No absence-probe surface: classify rather than explode; the
                # caller keeps its bounded teardown semantics.
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
            # LiveDaytonaPlatform may fall back from a forced stop to delete.
            # Fence that destructive fallback exactly like an explicit delete
            # before releasing ownership.
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
                absence: AbsenceOutcome = await confirm_fn(
                    probe=self._bounded_probe,
                    sandbox_id=self._sandbox_id,
                    # A forced stop has already failed at its provider
                    # request boundary; use a short bounded fence here so
                    # recovery cannot consume the full delete-confirmation
                    # budget while the provider error is still unresolved.
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
        """Retry retained provider cleanup without unbounded individual waits."""
        retry_delay = min(max(self._policy.confirm_poll_interval_s, 0.05), 1.0)
        while True:
            pending = tuple(task for task in self._provider_tasks if not task.done())
            if pending:
                # Do not await a provider request without a bound. The deferred
                # close remains the strong owner, while shutdown callers can
                # observe a pending/quarantined lease and return on their own
                # deadline.
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
            # Retained Session/root ownership is deliberately not dropped on an
            # unconfirmed provider result. Keep the continuation alive and
            # retry the idempotent provider action/confirmation until it settles.
            await asyncio.sleep(retry_delay)

    async def _finish_deferred_close(
        self,
        interpreter_task: asyncio.Task[InterpreterCloseOutcome],
    ) -> None:
        """Finish purge/provider/admission only after interpreter shutdown settles."""
        try:
            interpreter = await interpreter_task
        except BaseException as exc:
            logger.warning(
                "deferred Daytona interpreter shutdown failed",
                extra={"sandbox_id": self._sandbox_id, "error_type": type(exc).__name__},
            )
            interpreter = InterpreterCloseOutcome(
                status="failed",
                broker="failed",
                backend="failed",
                error=sanitize_failure_text(exc),
            )

        # A bounded shutdown can complete with a provider/backend failure even
        # after its worker thread has settled. Retry the strict interpreter
        # boundary before touching purge/provider state; otherwise a live
        # interpreter could race Sandbox deletion. The continuation remains a
        # strong owner until one attempt reports a clean boundary.
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
            # The retained provider request has its own retryable fence. Do not
            # release admission merely because the first request/confirmation
            # attempt returned a typed error.
            await self._finish_retained_provider_close()
            return
        if provider.error is not None:
            logger.warning(
                "deferred Daytona provider cleanup was not clean",
                extra={"sandbox_id": self._sandbox_id, "error": provider.error},
            )
        if self._permit is not None:
            self._permit.release()
            self._permit = None

    def _retain_deferred_close(self, task: asyncio.Task[None]) -> None:
        """Retain a quarantine continuation until all ordered cleanup settles."""
        self._deferred_close_task = task
        _DEFERRED_CLOSE_TASKS.add(task)

        def settled(completed: asyncio.Task[None]) -> None:
            _DEFERRED_CLOSE_TASKS.discard(completed)
            if completed.cancelled():
                _FAILED_LEASE_OWNERS[id(self)] = self
                logger.warning("deferred Daytona cleanup was cancelled", extra={"sandbox_id": self._sandbox_id})
                return
            with contextlib.suppress(BaseException):
                error = completed.exception()
            if error is None:
                _FAILED_LEASE_OWNERS.pop(id(self), None)
            else:
                _FAILED_LEASE_OWNERS[id(self)] = self
                logger.warning(
                    "deferred Daytona cleanup failed",
                    extra={"sandbox_id": self._sandbox_id, "error_type": type(error).__name__},
                )

        task.add_done_callback(settled)

    async def _close_core(self, *, bounded_interpreter: bool = True) -> SandboxLeaseReceipt:
        started = time.monotonic()
        policy = self._policy
        first_error: str | None = None

        # Interpreter shutdown performs blocking HTTP + broker work; it runs
        # off the owner loop. A shutdown that outlives the close bound is
        # quarantined (the coroutine continues; ownership is not abandoned).
        interpreter = await self._shutdown_interpreter_owned(bounded=bounded_interpreter)
        if interpreter.status in {"failed", "quarantined"} and first_error is None:
            first_error = interpreter.error

        if interpreter.status in {"failed", "quarantined"}:
            interpreter_task = self._interpreter_task
            if interpreter_task is None:
                raise RuntimeError("interpreter quarantine has no owned task")
            if not bounded_interpreter:
                # The synchronous fallback owns a disposable event loop. Do a
                # retry before that loop is destroyed; never leave a deferred
                # coroutine tied to a loop that ``asyncio.run`` is about to
                # close. A still-failing interpreter is returned quarantined
                # with its permit held for the caller's next retry.
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

        # Session/root provider requests remain owned after a bounded request
        # timeout. Return a quarantine receipt now, but keep a retryable
        # continuation that settles the request and only then releases the
        # admission permit. Ephemeral volume-I/O leases cancel and settle their
        # request in ``_run_provider_request`` and do not take this branch.
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

        receipt = SandboxLeaseReceipt(
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
        return receipt

    async def _run_fallback_close(self) -> SandboxLeaseReceipt:
        """Complete a close on a disposable loop when the owner loop is stopping."""
        try:
            receipt = await self._close_core(bounded_interpreter=False)
        except BaseException:
            # This coroutine may run on a disposable loop, so it cannot acquire
            # the owner loop's asyncio.Lock.  The fallback is installed while
            # that lock is held and the state transition is a single-threaded
            # assignment under the GIL.
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
        """Own one async close so cancellation cannot abandon its permit."""
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
        """Close once with an optional absolute deadline; retain late ownership."""
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
                    # ``create_task`` can fail while the owner loop is closing.
                    # Close the just-built coroutine, then hand a fresh close
                    # coroutine to the disposable-loop fallback. Its Future is
                    # retained as the lease's single-flight owner.
                    coroutine.close()
                    execution = schedule_owned_close(
                        loop=asyncio.get_running_loop(),
                        build=self._run_fallback_close,
                        thread_name="fleet-sandbox-lease-close-fallback",
                    )
                    task = asyncio.ensure_future(asyncio.wrap_future(execution.future))
                self._close_task = task
                _retain_close_task(task, self)
                # A disposable-loop fallback can finish before the wrapper is
                # installed on this owner loop.  Reconcile an already-failed
                # result now so the next caller can retry instead of awaiting
                # the same terminal exception forever.
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
        """Wait for every close/provider owner retained by this lease.

        ``aclose`` intentionally returns a quarantine receipt when a retained
        provider request is still in flight. Callers that own an admission
        slot (for example failed acquisition cleanup) can use this stronger
        boundary before releasing that slot. Waiting is shielded so caller
        cancellation never cancels the provider continuation.
        """
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
        """Synchronous close for worker-thread owners.

        When the caller is on the owner event loop this blocks it; the bridge
        guard is the composition's, not the lease's — callers own thread
        placement (mirrors the pre-seam recursive child close contract).
        Idempotent: repeated calls return the first close's receipt.
        """
        if self._receipt is not None:
            return self._receipt
        self._state = LeaseState.CLOSING
        self._closed = False
        try:
            # Synchronous owners run on a disposable worker loop.  They must
            # finish the ordered interpreter/provider pipeline before that
            # loop is destroyed; the async path retains a bounded quarantine
            # continuation instead.
            self._receipt = asyncio.run(self._close_core(bounded_interpreter=False))
            self._state = _receipt_state(self._receipt)
            self._closed = True
        except BaseException as exc:
            # Unreachable in the seam (close_core captures), but never let a
            # close raise instead of reporting.
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
    """Map a typed receipt to the explicit lifecycle state."""
    if receipt.first_error is not None or receipt.quarantine.quarantined:
        return LeaseState.FAILED
    return LeaseState.CLOSED


@dataclass(slots=True)
class OwnedCloseExecution:
    """One scheduled owned async close, with its disposable-loop fallback record.

    ``coroutine`` is the initially built close coroutine on the posted path
    (caller may ``close()`` it when cancelling the future); on the fallback
    path it was already closed by the seam and reads ``None``.
    """

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
    """Post one owned async close to the owner loop, or run it on a disposable loop.

    ``build`` creates the close coroutine, which is solely responsible for its
    owned resources (permit release, provider deletion, receipts). When the
    post fails (owner loop closing during late-acquisition handoff), the close
    still runs on a disposable daemon loop built from a FRESH coroutine so the
    permit's settle semantics execute; a thread-start failure releases via
    ``fallback_owner_release`` (when provided) and surfaces the error on the
    returned future rather than stranding ownership.
    """
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
        # A thread-start failure cannot safely run provider I/O. Release the
        # owned resource synchronously and surface the failure.
        if fallback_owner_release is not None:
            with contextlib.suppress(BaseException):
                fallback_owner_release()
        fallback.set_exception(exc)
    return OwnedCloseExecution(future=fallback, used_fallback=True, coroutine=None)


def _sandbox_id_or_none(sandbox: Any) -> str | None:
    value = getattr(sandbox, "id", None)
    return value if isinstance(value, str) and value else None
