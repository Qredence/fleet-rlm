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

from fleet_rlm.daytona.admission import DaytonaAdmissionPermit
from fleet_rlm.daytona.errors import sanitize_failure_text
from fleet_rlm.daytona.lifecycle import AbsenceConfirmation, AbsenceOutcome, confirm_absence
from fleet_rlm.daytona.provisioning import SandboxPlatform

logger = logging.getLogger(__name__)

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


@dataclass(frozen=True, slots=True)
class SandboxLeaseReceiptStore:
    """Simplest owner-visible receipt carrier: last close outcome."""

    receipt: SandboxLeaseReceipt | None = None


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
        self._receipt: SandboxLeaseReceipt | None = None

    @property
    def closed(self) -> bool:
        return self._closed

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

    async def _shutdown_interpreter_owned(self) -> InterpreterCloseOutcome:
        """Run the strict interpreter shutdown off-loop under a bounded wait."""
        task = asyncio.create_task(asyncio.to_thread(self._shutdown_interpreter))
        try:
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
                if policy.provider_request_timeout_s is not None:
                    await asyncio.wait_for(request, timeout=policy.provider_request_timeout_s)
                else:
                    await request
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
                absence: AbsenceOutcome = await confirm_fn(
                    probe=platform.get,
                    sandbox_id=self._sandbox_id,
                    timeout_s=policy.confirm_timeout_s,
                    poll_interval_s=policy.confirm_poll_interval_s,
                )
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
        try:
            stop_request = platform.stop(self._sandbox_id, timeout=60, force=self._policy.stop_force)
            if policy.provider_request_timeout_s is not None:
                await asyncio.wait_for(stop_request, timeout=policy.provider_request_timeout_s)
            else:
                await stop_request
        except BaseException as exc:
            return ProviderCleanupOutcome(
                action="stop",
                requested=True,
                confirmed_absent=False,
                duration_s=time.monotonic() - started,
                error=sanitize_failure_text(exc),
            )
        return ProviderCleanupOutcome(
            action="stop",
            requested=True,
            confirmed_absent=False,
            duration_s=time.monotonic() - started,
        )

    async def _close_core(self) -> SandboxLeaseReceipt:
        started = time.monotonic()
        policy = self._policy
        first_error: str | None = None

        # Interpreter shutdown performs blocking HTTP + broker work; it runs
        # off the owner loop. A shutdown that outlives the close bound is
        # quarantined (the coroutine continues; ownership is not abandoned).
        interpreter = await self._shutdown_interpreter_owned()
        if interpreter.status in {"failed", "quarantined"} and first_error is None:
            first_error = interpreter.error

        if self._purge is not None and self._sandbox is not None:
            try:
                await self._purge(self._sandbox)
            except BaseException as exc:
                if first_error is None:
                    first_error = sanitize_failure_text(exc)

        provider = await self._provider_close()
        if provider.error is not None and first_error is None:
            first_error = provider.error

        quarantined = interpreter.status == "quarantined"
        quarantine_error: str | None = interpreter.error if quarantined else None
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

    async def aclose(self) -> SandboxLeaseReceipt:
        """Close once (idempotent); returns the close receipt."""
        if self._receipt is not None:
            return self._receipt
        self._closed = True
        self._receipt = await self._close_core()
        return self._receipt

    def close(self) -> SandboxLeaseReceipt:
        """Synchronous close for worker-thread owners.

        When the caller is on the owner event loop this blocks it; the bridge
        guard is the composition's, not the lease's — callers own thread
        placement (mirrors the pre-seam recursive child close contract).
        Idempotent: repeated calls return the first close's receipt.
        """
        if self._receipt is not None:
            return self._receipt
        self._closed = True
        try:
            self._receipt = asyncio.run(self._close_core())
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
            if self._permit is not None:
                self._permit.release()
        return self._receipt


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
