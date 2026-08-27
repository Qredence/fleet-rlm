"""DaytonaSessionManager: acquire/release leases and capability-aware lifecycle.

Release never deletes a Sandbox. Volume identity is preserved across replace.
Workspace Volume Scope uses VolumeMount subpath ``workspaces/<workspace_id>``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from threading import Lock
from typing import Any, Protocol
from uuid import UUID, uuid4

from fleet_rlm.chat.run_cleanup import RunCleanupSupervisor

# Admission ownership lives in fleet_rlm.daytona.admission (QRE-156); the
# re-export here keeps the historical session_manager import surface working.
from fleet_rlm.daytona.admission import (
    DaytonaAdmission,
    DaytonaAdmissionPermit,
    DaytonaAdmissionTimeoutError,
)
from fleet_rlm.daytona.dspy_sync_bridge import SyncBridgeDispatcher
from fleet_rlm.daytona.errors import (
    DaytonaAdapterError,
    ProviderRequestError,
    is_safe_pre_creation_retry,
    map_provider_error,
)
from fleet_rlm.daytona.interpreter import (
    DEFAULT_EXECUTION_OUTPUT_CHARS,
    DEFAULT_EXECUTION_TIMEOUT_S,
    DaytonaCodeInterpreter,
    sandbox_backend,
)
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
from fleet_rlm.daytona.sandbox_lease import (
    SandboxLease,
    SandboxLeasePolicy,
    SandboxLeaseReceipt,
    schedule_owned_close,
)
from fleet_rlm.runtime.bindings import (
    SandboxBinding,
    require_non_zero_workspace_id,
    require_scoped_volume_subpath,
    workspace_volume_subpath,
)
from fleet_rlm.runtime.owned_effect import OwnedEffect

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _LateLeaseOwner:
    """Retryable ownership record for a late provider acquisition."""

    lease: InterpreterLease
    permit: DaytonaAdmissionPermit
    manager: DaytonaSessionManager
    session_id: UUID
    user_id: UUID
    workspace_id: UUID
    run_id: UUID
    retry_task: asyncio.Task[None] | None = None


_LATE_LEASE_OWNERS: dict[int, _LateLeaseOwner] = {}


@dataclass(slots=True)
class _LateAcquisitionOwner:
    """Strong owner retained even when no event-loop task can be scheduled."""

    acquisition: asyncio.Task[InterpreterLease]
    permit: DaytonaAdmissionPermit
    manager: DaytonaSessionManager
    request: LeaseRequest
    run_id: UUID
    # Usually an asyncio.Task on the manager loop; a closed-loop fallback may
    # retain the concurrent Future returned by ``schedule_owned_close``.
    cleanup_task: Any | None = None


_LATE_ACQUISITION_OWNERS: dict[int, _LateAcquisitionOwner] = {}


class ActiveLeaseConflictError(RuntimeError):
    """Another run already holds the active lease for this Session."""

    def __init__(self, session_id: UUID, holder_run_id: UUID | None = None) -> None:
        self.session_id = session_id
        self.holder_run_id = holder_run_id
        super().__init__(f"active lease conflict for session {session_id}")


class ActiveLeaseRegistry:
    """At most one active Interpreter Lease per Workspace+Session in this process."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._holders: dict[tuple[UUID, UUID], UUID] = {}

    @staticmethod
    def _key(session_id: UUID, workspace_id: UUID | None) -> tuple[UUID, UUID]:
        # The zero workspace preserves the historical one-argument helper for
        # callers that already operate in one known workspace. Production
        # admission always supplies the real Workspace scope.
        return (workspace_id or UUID(int=0), session_id)

    def acquire(self, session_id: UUID, run_id: UUID, *, workspace_id: UUID | None = None) -> None:
        with self._lock:
            key = self._key(session_id, workspace_id)
            existing = self._holders.get(key)
            if existing is not None and existing != run_id:
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
            matches = [run_id for (scope, sid), run_id in self._holders.items() if sid == session_id]
            return matches[0] if len(matches) == 1 else None


_REGISTRY = ActiveLeaseRegistry()


def get_active_lease_registry() -> ActiveLeaseRegistry:
    return _REGISTRY


@dataclass(slots=True)
class InterpreterLease:
    """Acquired interpreter binding for one Run."""

    sandbox_id: str
    interpreter_id: str
    volume_id: str
    mount_path: str
    interpreter: DaytonaCodeInterpreter
    session_id: str | None = None
    run_id: str | None = None
    workspace_id: str | None = None
    volume_subpath: str | None = None
    created_sandbox: bool = False
    _released: bool = field(default=False, init=False, repr=False)
    _on_release: Callable[[], None] | None = field(default=None, init=False, repr=False)
    _release_lock: Lock = field(default_factory=Lock, init=False, repr=False)

    def release(self) -> None:
        # ``DaytonaSessionManager.release`` may be called concurrently by a
        # stream cleanup task and a composition shutdown. Serialize the entire
        # synchronous shutdown/callback boundary so the interpreter and permit
        # are released exactly once, while a failed shutdown remains retryable.
        with self._release_lock:
            if self._released:
                return
            # Keep admission and the active Session claim until interpreter
            # shutdown succeeds. A failed release must remain retryable;
            # clearing ownership in ``finally`` would let a root lease be
            # discarded while its provider/interpreter is still live.
            self.interpreter.shutdown(strict_broker_cleanup=True)
            self._released = True
            if self._on_release is not None:
                with contextlib.suppress(BaseException):
                    self._on_release()


class DaytonaLeaseAcquisitionTimeoutError(RuntimeError):
    """The Turn deadline elapsed while provider lease work was in flight."""


class _ProviderCallDeadlineError(TimeoutError):
    """Internal timeout carrying the still-owned provider task."""

    def __init__(self, task: asyncio.Future[Any], operation: str) -> None:
        self.task = task
        self.operation = operation
        super().__init__(f"Daytona {operation} timed out")


def _retain_provider_task(task: asyncio.Future[Any], owner: set[asyncio.Future[Any]]) -> None:
    """Retain one provider task and consume its late exception exactly once."""
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
    """Run one provider-facing operation behind the absolute Turn deadline.

    The provider operation is started as a separately-owned task. A timeout or
    caller cancellation never leaves a provider coroutine without an owner.
    Callers which know how to recover its result (most importantly Sandbox
    creation) can settle it before continuing cleanup, while the manager owner
    set keeps a late request alive after the public operation returns.
    """
    loop = asyncio.get_running_loop()
    if deadline is not None and deadline <= loop.time():
        close = getattr(awaitable, "close", None)
        if callable(close):
            close()
        raise DaytonaLeaseAcquisitionTimeoutError(f"Daytona {operation} timed out") from None
    task = asyncio.ensure_future(awaitable)
    if deadline is None:
        try:
            # Provider requests remain owned when a caller is canceled.  A
            # direct await would propagate cancellation into ``task`` before
            # the owner set can retain it.
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
        # ``wait_for`` also surfaces a TimeoutError raised by the provider
        # itself. Only a still-pending task represents our deadline expiring.
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
    """Settle a timed-out provider task while preserving its original result."""
    await OwnedEffect.from_task(task).settle()
    return task.result()


DEFAULT_IDLE_STOP_SECONDS = 300.0


@dataclass(frozen=True, slots=True)
class LeaseRequest:
    session_id: UUID
    user_id: UUID
    workspace_id: UUID
    run_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class _AcquisitionContext:
    expected: ExpectedWorkspaceMount
    binding: SandboxBinding | None


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
    """Attach a code-interpreter backend when the sandbox exposes one."""
    if hasattr(sandbox, "code_interpreter"):
        return DaytonaCodeInterpreter(
            backend=sandbox_backend(sandbox, loop=loop, dispatcher=dispatcher, timeout_s=execution_timeout_s),
            execution_output_cap=execution_output_cap,
        )
    # Fake/test sandboxes may already carry an interpreter attribute.
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
        self._owned_sandbox_ids: set[str] = set()
        self._owned_sandbox_lock = Lock()
        self._release_tasks: set[asyncio.Task[None]] = set()
        self._handled_release_tasks: set[asyncio.Task[None]] = set()
        # Strongly retain leases whose interpreter release failed or outlived
        # its caller so shutdown can retry the exact owner.
        self._release_leases: dict[asyncio.Task[None], InterpreterLease] = {}
        self._late_cleanup_tasks: set[Any] = set()
        # Provider calls which outlive the Turn deadline remain owned until the
        # SDK task settles; this fence is independent of caller references.
        self._provider_tasks: set[asyncio.Future[Any]] = set()
        self._provisioner = SandboxProvisioner(
            platform=platform,
            volume_config=volume_config,
            sandbox_spec=sandbox_spec,
        )

    def _mark_sandbox_owned(self, sandbox_id: str) -> None:
        with self._owned_sandbox_lock:
            self._owned_sandbox_ids.add(sandbox_id)

    def _mark_sandbox_released(self, sandbox_id: str) -> None:
        with self._owned_sandbox_lock:
            self._owned_sandbox_ids.discard(sandbox_id)

    @property
    def has_pending_ownership(self) -> bool:
        """Whether release/acquisition/idle cleanup still owns provider work."""
        with self._owned_sandbox_lock:
            sandbox_owned = bool(self._owned_sandbox_ids)
        return bool(
            self._late_cleanup_tasks
            or self._provider_tasks
            or self._release_tasks
            or any(not lease._released for lease in self._release_leases.values())
            or self._idle_tasks
            or sandbox_owned
            or any(owner.manager is self for owner in _LATE_LEASE_OWNERS.values())
            or any(owner.manager is self for owner in _LATE_ACQUISITION_OWNERS.values())
        )

    def owns_sandbox(self, sandbox_id: str) -> bool:
        """Return whether a live InterpreterLease still owns this Sandbox."""
        with self._owned_sandbox_lock:
            return sandbox_id in self._owned_sandbox_ids

    def _expected_mount(self, *, volume_id: str, workspace_id: UUID) -> ExpectedWorkspaceMount:
        return self._provisioner.expected_mount(
            volume_id=volume_id,
            workspace_id=workspace_id,
        )

    def _sandbox_retirement_lease(
        self,
        sandbox_id: str,
        *,
        confirm_timeout_s: float = 120.0,
        provider_request_timeout_s: float | None = 30.0,
    ) -> SandboxLease:
        """Confirmed teardown of a formerly owned Session sandbox (QRE-156)."""
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
        """Ensure a running Sandbox with Workspace Volume Scope; return a lease."""
        require_non_zero_workspace_id(request.workspace_id)
        run_id = request.run_id or uuid4()
        session_id = request.session_id
        await self._cancel_idle_stop(session_id, workspace_id=request.workspace_id, deadline=deadline)
        get_active_lease_registry().acquire(session_id, run_id, workspace_id=request.workspace_id)
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
                # Even a provider task that completed at the deadline has not
                # been bound to this caller. Adopt it for release/quarantine
                # rather than dropping a successful lease on the floor.
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
                    get_active_lease_registry().release(session_id, run_id, workspace_id=request.workspace_id)
            raise

    @staticmethod
    async def _settle_provider_acquisition(
        acquisition: asyncio.Task[InterpreterLease],
    ) -> InterpreterLease:
        """Wait through repeated caller cancellation until provider work settles."""
        effect = OwnedEffect.from_task(acquisition)
        await effect.settle()
        return effect.result()

    async def _run_late_acquisition_owner(self, owner: _LateAcquisitionOwner) -> None:
        """Settle one late acquisition, then retire any produced lease."""
        acquisition = owner.acquisition
        # A disposable-loop fallback may be invoked after the acquisition's
        # original loop has been destroyed.  Never await a pending Task from a
        # foreign loop: leave the acquisition owner quarantined for the done
        # callback or a later manager retry instead of releasing its permit.
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
                # The acquisition task owns all work it started. If it failed
                # before producing a Sandbox, there is no lease quarantine to
                # run, but the admission/claim still belong to this owner until
                # this point.
                try:
                    owner.permit.release()
                finally:
                    get_active_lease_registry().release(
                        owner.request.session_id,
                        owner.run_id,
                        workspace_id=owner.request.workspace_id,
                    )
                return
            late_owner = _LateLeaseOwner(
                lease,
                owner.permit,
                self,
                owner.request.session_id,
                owner.request.user_id,
                owner.request.workspace_id,
                owner.run_id,
            )
            _LATE_LEASE_OWNERS[id(lease)] = late_owner
            self._mark_sandbox_owned(lease.sandbox_id)
            await self._run_late_owner_cleanup(late_owner)
        finally:
            if acquisition.done() and _LATE_ACQUISITION_OWNERS.get(id(acquisition)) is owner:
                _LATE_ACQUISITION_OWNERS.pop(id(acquisition), None)

    def _schedule_late_acquisition_fallback(self, owner: _LateAcquisitionOwner) -> bool:
        """Run settled late acquisition cleanup when its owner loop is closing."""
        try:
            owner_loop = owner.acquisition.get_loop()
        except BaseException:
            owner_loop = None
        if owner_loop is None or owner_loop.is_closed() or not owner_loop.is_running():
            # ``schedule_owned_close`` uses a closed target as an explicit
            # signal to start its disposable-loop fallback.  The acquisition
            # result is read synchronously once done, so this path does not
            # await a Task belonging to the destroyed loop.
            owner_loop = asyncio.new_event_loop()
            owner_loop.close()
        try:
            execution = schedule_owned_close(
                loop=owner_loop,
                build=lambda: self._run_late_acquisition_owner(owner),
                thread_name="fleet-daytona-late-acquisition-fallback",
            )
        except BaseException as exc:
            logger.critical(
                "unable to retain late Daytona acquisition cleanup",
                extra={"error_type": type(exc).__name__},
            )
            return False
        task = execution.future
        owner.cleanup_task = task
        self._late_cleanup_tasks.add(task)
        task.add_done_callback(self._settled_late_cleanup)
        return True

    def _schedule_late_acquisition_owner(self, owner: _LateAcquisitionOwner) -> bool:
        """Schedule late cleanup or leave its owner quarantined for a retry."""
        if owner.cleanup_task is not None and not owner.cleanup_task.done():
            return True
        awaitable = self._run_late_acquisition_owner(owner)
        try:
            task = self._cleanup.submit(awaitable)
        except BaseException as scheduler_error:
            try:
                task = asyncio.create_task(awaitable, name="fleet-daytona-late-acquisition-cleanup")
            except BaseException:
                with contextlib.suppress(BaseException):
                    awaitable.close()
                logger.warning(
                    "Daytona cleanup supervisor rejected late acquisition; using owned fallback",
                    extra={"error_type": type(scheduler_error).__name__},
                )
                return self._schedule_late_acquisition_fallback(owner)
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
        """Own an acquisition that outlives its caller before scheduling cleanup."""
        owner = _LateAcquisitionOwner(acquisition, permit, self, request, run_id)
        _LATE_ACQUISITION_OWNERS[id(acquisition)] = owner
        if self._schedule_late_acquisition_owner(owner):
            return

        # If both the cleanup supervisor and the current loop reject task
        # creation, retain the acquisition owner and retry scheduling when the
        # provider task settles. The owner map itself keeps the permit, active
        # claim, and acquisition identity alive across that gap.
        def retry_after_settlement(_completed: asyncio.Future[Any]) -> None:
            if owner.cleanup_task is None:
                self._schedule_late_acquisition_owner(owner)

        acquisition.add_done_callback(retry_after_settlement)

    async def _run_late_owner_cleanup(self, owner: _LateLeaseOwner) -> None:
        """Retry interpreter release and provider quarantine for one late lease."""
        lease = owner.lease
        release_error: BaseException | None = None
        try:
            # Interpreter shutdown is synchronous and may block on the broker.
            # Keep the late owner cancellable without abandoning the worker
            # thread or making a caller wait on the event loop.
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
                        session_id=owner.session_id,
                        user_id=owner.user_id,
                        workspace_id=UUID(str(lease.workspace_id)) if lease.workspace_id else UUID(int=0),
                        run_id=owner.run_id,
                    ),
                )
            except BaseException as exc:
                quarantine_error = exc

        if release_error is not None or quarantine_error is not None:
            # Keep admission, active-session fencing, and Sandbox identity for
            # a later retry. In particular, never delete a Sandbox while an
            # interpreter shutdown is still unresolved.
            if quarantine_error is not None:
                logger.warning(
                    "late Daytona lease quarantine failed",
                    extra={"sandbox_id": lease.sandbox_id, "error_type": type(quarantine_error).__name__},
                )
            if release_error is not None:
                logger.warning(
                    "late Daytona interpreter release failed",
                    extra={"sandbox_id": lease.sandbox_id, "error_type": type(release_error).__name__},
                )
            return

        owner.permit.release()
        get_active_lease_registry().release(
            owner.session_id,
            owner.run_id,
            workspace_id=owner.workspace_id,
        )
        self._mark_sandbox_released(lease.sandbox_id)
        _LATE_LEASE_OWNERS.pop(id(lease), None)

    async def _retry_late_owners(self, deadline: float) -> bool:
        """Start retryable late-owner cleanup and wait only through shutdown bound."""
        current_loop = asyncio.get_running_loop()
        tasks: list[asyncio.Future[Any]] = []
        for owner in tuple(_LATE_ACQUISITION_OWNERS.values()):
            if owner.manager is not self:
                continue
            # A Task still pending on another loop cannot be awaited or moved
            # to this loop.  Its original done callback remains responsible for
            # adopting it; retaining the owner here is the safe outcome.
            if not owner.acquisition.done():
                try:
                    acquisition_loop = owner.acquisition.get_loop()
                except BaseException:
                    acquisition_loop = None
                if acquisition_loop is not current_loop:
                    continue
            task = owner.cleanup_task
            if task is None or task.done():
                self._schedule_late_acquisition_owner(owner)
                task = owner.cleanup_task
            if task is not None:
                if isinstance(task, asyncio.Future):
                    tasks.append(task)
                else:
                    # Closed-loop fallback ownership is represented by a
                    # concurrent Future.  Wrap it only for this loop's bounded
                    # wait; the owner retains the underlying Future.
                    tasks.append(asyncio.wrap_future(task))
        for owner in tuple(_LATE_LEASE_OWNERS.values()):
            if owner.manager is not self:
                continue
            task = owner.retry_task
            if task is None or task.done():
                task = asyncio.create_task(
                    self._run_late_owner_cleanup(owner),
                    name="fleet-daytona-late-lease-retry",
                )
                owner.retry_task = task
                self._late_cleanup_tasks.add(task)
                task.add_done_callback(self._settled_late_cleanup)
            tasks.append(task)
        if not tasks:
            return True
        remaining = max(0.0, deadline - current_loop.time())
        _, pending = await asyncio.wait(tuple(tasks), timeout=remaining)
        return not pending

    def _settled_late_cleanup(self, task: Any) -> None:
        """Retire fallback ownership and consume cleanup failures explicitly."""
        self._late_cleanup_tasks.discard(task)
        if task.cancelled():
            return
        with contextlib.suppress(BaseException):
            error = task.exception()
        if error is not None:
            logger.warning(
                "late Daytona acquisition cleanup failed",
                extra={"error_type": type(error).__name__},
                exc_info=(type(error), error, error.__traceback__),
            )

    async def _quarantine(
        self, lease: InterpreterLease, request: LeaseRequest, *, deadline: float | None = None
    ) -> None:
        """Confirm the old Sandbox is stopped before its ownership is released."""
        await self._fence_binding(
            SandboxBinding(
                session_id=request.session_id,
                sandbox_id=lease.sandbox_id,
                workspace_id=request.workspace_id,
                volume_id=lease.volume_id,
                volume_subpath=lease.volume_subpath or workspace_volume_subpath(request.workspace_id),
                mount_path=lease.mount_path,
                provider_state="running",
            ),
            deadline=deadline,
        )
        if lease.created_sandbox:
            # Lease-backed (QRE-156): the retired sandbox's deletion is
            # confirmed absent through the shared contract, before the
            # submission lane lets it go (including the inline fallback).
            retire = self._sandbox_retirement_lease(lease.sandbox_id)
            receipt_box: dict[str, SandboxLeaseReceipt] = {}

            async def _retire() -> None:
                receipt_box["receipt"] = await retire.aclose()

            deletion = _retire()
            try:
                deletion_task = self._cleanup.submit(deletion)
            except BaseException as scheduler_error:
                # A supervisor can reject work while its event loop is
                # stopping.  Fall back to a manager-owned task and keep the
                # admission/claim until the typed receipt exists.
                try:
                    deletion_task = asyncio.create_task(deletion, name="fleet-daytona-late-sandbox-retirement")
                except BaseException:
                    with contextlib.suppress(BaseException):
                        deletion.close()
                    raise RuntimeError("sandbox retirement ownership unavailable") from scheduler_error
            # Do not release the acquisition's admission/Session claim until
            # the owned deletion reaches its typed confirmation.
            await deletion_task
            receipt = receipt_box["receipt"]
            if not receipt.clean:
                logger.warning(
                    "retired session sandbox deletion was not confirmed",
                    extra={"sandbox_id": lease.sandbox_id, "error": receipt.first_error},
                )
                raise RuntimeError("sandbox retirement was not confirmed")

    async def _get_binding_for_workspace(
        self,
        session_id: UUID,
        workspace_id: UUID,
        *,
        deadline: float | None = None,
    ) -> SandboxBinding | None:
        """Read a binding through the strongest available Session/Workspace key."""

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
                return binding
            # Distinguish an absent binding from a Session binding in another
            # Workspace so a cross-tenant request cannot overwrite it.
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
        return binding

    async def fence_session(
        self,
        session_id: UUID,
        *,
        workspace_id: UUID | None = None,
        deadline: float | None = None,
    ) -> None:
        """Fence a Sandbox retained by a settling Run during startup recovery."""
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
        """Persist fencing around one bounded, owned provider stop."""
        await _provider_call(
            self._bindings.upsert(replace(binding, provider_state="fencing", last_verified_at=datetime.now(UTC))),
            deadline=deadline,
            operation="Sandbox fence persistence",
            owner=self._provider_tasks,
        )
        if binding.sandbox_id is None:
            return
        # Lease-backed (QRE-156/AC4): recovery fencing rides the shared
        # provider lifecycle adapter; the receipt records the provider action
        # and failure without letting force=True deletion go unrecorded.
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
        await _provider_call(
            self._bindings.upsert(replace(binding, provider_state="quarantined", last_verified_at=datetime.now(UTC))),
            deadline=deadline,
            operation="Sandbox quarantine persistence",
            owner=self._provider_tasks,
        )

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
                get_active_lease_registry().release(session_id, run_id, workspace_id=workspace_id)

        lease._on_release = _clear_active

    async def _acquire_provider(
        self,
        request: LeaseRequest,
        *,
        run_id: UUID,
        deadline: float | None = None,
        force_new: bool = False,
    ) -> InterpreterLease:
        """Complete provider work after admission; caller owns settlement."""
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
                request, run_id, context.expected, sandbox, created_sandbox, deadline=deadline
            )
        except _ProviderCallDeadlineError as exc:
            # Keep this owned acquisition task alive until the provider call
            # settles. The public manager has already returned at its deadline,
            # but admission/active-lease ownership must not race late provider
            # mutations or a subsequent Session acquisition.
            with contextlib.suppress(BaseException):
                await _settle_provider_task(exc.task)
            if context is not None and sandbox is not None:
                await self._cleanup_failed_acquisition(
                    request,
                    sandbox,
                    created_sandbox=created_sandbox,
                    deadline=deadline,
                    binding=context.binding,
                )
            raise DaytonaLeaseAcquisitionTimeoutError(f"Daytona {exc.operation} timed out") from None
        except BaseException:
            if context is not None and sandbox is not None:
                # Once a Sandbox exists, acquisition owns it until a lease is
                # returned. Persisting a binding or constructing the
                # interpreter may fail after provider creation; centralize
                # retirement so the outer admission handler never drops an
                # unleased Sandbox.
                await self._cleanup_failed_acquisition(
                    request,
                    sandbox,
                    created_sandbox=created_sandbox,
                    deadline=deadline,
                    binding=context.binding,
                )
            raise

    async def _resolve_acquisition_context(
        self, request: LeaseRequest, *, deadline: float | None = None
    ) -> _AcquisitionContext:
        """Resolve the workspace mount and reject bindings still under provider fencing."""
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
        """Reuse a verified binding or replace/create a Sandbox for the workspace."""
        sandbox = await self._reuse_bound_sandbox(request, context, deadline=deadline, force_new=force_new)
        # A forced context rotation retires the old binding and creates a new
        # Sandbox even when its workspace mount is otherwise reusable.  Mark it
        # as newly created so post-acquisition failures retire that identity.
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
            # A Run-scoped context manifest cannot be rebound to an existing
            # Daytona interpreter. Retire the old binding through the normal
            # confirmed replacement path, then acquire the fresh identity.
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
            # binding_matches_expected above proved identity/mount fields match
            # this request's scope; mark the bound record unrecoverable with a
            # reset verification timestamp.
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
        """Verify the run-specific layout for the acquired Sandbox."""
        del created_sandbox
        try:
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
        except BaseException:
            raise

    async def _cleanup_failed_acquisition(
        self,
        request: LeaseRequest,
        sandbox: Any,
        *,
        created_sandbox: bool,
        deadline: float | None = None,
        binding: SandboxBinding | None = None,
    ) -> None:
        """Retire or fence provider state after lease construction fails."""
        sandbox_id = _sandbox_id(sandbox)
        # Make a failed/reused identity ineligible before cleanup starts. This
        # prevents a reused Sandbox from remaining durably ``running`` while an
        # interpreter shutdown or provider fence is still owned out of band.
        candidate = binding
        if candidate is None:
            with contextlib.suppress(BaseException):
                candidate = await self._get_binding_for_workspace(
                    request.session_id,
                    request.workspace_id,
                    deadline=deadline,
                )
        if candidate is not None and candidate.sandbox_id == sandbox_id:
            state = "quarantined" if created_sandbox else "fencing"
            with contextlib.suppress(BaseException):
                await _provider_call(
                    self._bindings.upsert(replace(candidate, provider_state=state, last_verified_at=None)),
                    deadline=deadline,
                    operation="Failed Sandbox fencing persistence",
                    owner=self._provider_tasks,
                )

        # Keep interpreter shutdown and provider retirement in one ordered lease
        # owner. If shutdown fails, SandboxLease retains the interpreter and does
        # not touch the provider until a later retry succeeds.
        interpreter: DaytonaCodeInterpreter | None = None
        try:
            interpreter = _build_interpreter(
                sandbox,
                loop=asyncio.get_running_loop(),
                dispatcher=self._dispatcher,
                execution_output_cap=self._execution_output_cap,
                execution_timeout_s=self._execution_timeout_s,
            )
        except BaseException as exc:
            logger.warning(
                "failed acquisition interpreter construction raised",
                extra={"sandbox_id": sandbox_id, "error_type": type(exc).__name__},
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
        except TimeoutError as exc:
            # The close task is retained by SandboxLease. Keep this acquisition
            # task owned until its interpreter/provider boundary settles, so the
            # outer manager cannot release admission while cleanup is in flight.
            logger.warning(
                "failed acquisition cleanup remains owned after deadline",
                extra={"sandbox_id": sandbox_id, "error_type": type(exc).__name__},
            )
            with contextlib.suppress(BaseException):
                await cleanup.wait_ownership()
            return
        except BaseException as exc:
            logger.warning(
                "failed acquisition cleanup raised",
                extra={"sandbox_id": sandbox_id, "error_type": type(exc).__name__},
            )
            with contextlib.suppress(BaseException):
                await cleanup.wait_ownership()
            return
        if not receipt.clean:
            logger.warning(
                "failed acquisition cleanup was quarantined",
                extra={"sandbox_id": sandbox_id, "error": receipt.first_error},
            )
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
    ) -> InterpreterLease:
        """Persist the verified provider binding and construct the caller-owned interpreter lease."""
        session_id = request.session_id
        sid = _sandbox_id(sandbox)
        await _provider_call(
            self._bindings.upsert(
                SandboxBinding(
                    session_id=session_id,
                    sandbox_id=sid,
                    workspace_id=request.workspace_id,
                    volume_id=expected.volume_id,
                    volume_subpath=expected.volume_subpath,
                    mount_path=expected.mount_path,
                    provider_state="running",
                    last_verified_at=datetime.now(UTC),
                )
            ),
            deadline=deadline,
            operation="Sandbox binding persistence",
            owner=self._provider_tasks,
        )
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
            session_id=str(session_id),
            run_id=str(run_id),
            workspace_id=str(request.workspace_id),
            created_sandbox=created_sandbox,
        )

    def _start_release_task(self, lease: InterpreterLease) -> asyncio.Task[None]:
        """Start or recover one single-flight worker-thread release."""
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

    async def release(self, lease: InterpreterLease) -> None:
        """Release an interpreter with owned thread settlement and idle fencing."""
        release_task = self._start_release_task(lease)
        await asyncio.shield(release_task)
        # A done callback runs on the next loop turn. Settle it here too so
        # normal callers observe the idle task before release() returns; the
        # callback becomes a no-op.
        self._settled_release_task(lease, release_task)

    def _settled_release_task(self, lease: InterpreterLease, task: asyncio.Task[None]) -> None:
        """Schedule idle policy only after the worker-thread release settles."""
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
            # Keep the exact lease in ``_release_leases``. Interpreter shutdown
            # is retryable and admission/session ownership must not disappear
            # merely because this attempt failed.
            logger.warning(
                "Daytona interpreter release failed",
                extra={"sandbox_id": lease.sandbox_id, "error_type": type(exc).__name__},
            )
            return
        for owned_task, owned_lease in tuple(self._release_leases.items()):
            if owned_lease is lease:
                self._release_leases.pop(owned_task, None)
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
        """Cancel and settle an idle stop before a new lease can reuse the Sandbox."""
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
            # ``acquire`` deliberately cancels an obsolete idle-stop task before
            # reusing its Session.  The canceled child is normal cleanup, not a
            # canceled acquisition.  Preserve caller cancellation when the
            # shielded wait itself was canceled instead.
            if task.cancelled():
                self._forget_idle_task(key, task)
                return
            raise

    def _request_cancel_idle_stop(self, session_id: UUID, *, workspace_id: UUID | None = None) -> None:
        """Request cancellation without dropping strong ownership from the task map."""
        found = self._find_idle_task(session_id, workspace_id)
        if found is not None:
            found[1].cancel()

    def _forget_idle_task(self, key: tuple[UUID, UUID], task: asyncio.Task[None]) -> None:
        if self._idle_tasks.get(key) is task:
            self._idle_tasks.pop(key, None)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.warning(
                "Retained Daytona Sandbox idle stop failed",
                extra={
                    "session_id": str(key[1]),
                    "workspace_id": str(key[0]),
                    "error_type": type(error).__name__,
                },
            )

    async def _stop_after_idle(
        self,
        *,
        session_id: UUID,
        sandbox_id: str,
        workspace_id: str | None,
        delay: float,
    ) -> None:
        """Stop an idle Sandbox only after identity and active-lease rechecks."""
        await asyncio.sleep(delay)
        workspace_scope = UUID(workspace_id) if workspace_id is not None else None
        if get_active_lease_registry().holder(session_id, workspace_id=workspace_scope) is not None:
            return
        if workspace_id is not None:
            assert workspace_scope is not None
            binding = await self._get_binding_for_workspace(session_id, workspace_scope)
        else:
            try:
                binding = await _provider_call(
                    self._bindings.get(session_id),
                    deadline=None,
                    operation="Idle Sandbox binding lookup",
                    owner=self._provider_tasks,
                )
            except _ProviderCallDeadlineError as exc:  # pragma: no cover - deadline=None
                binding = await _settle_provider_task(exc.task)
        if binding is None or binding.sandbox_id != sandbox_id or binding.provider_state != "running":
            return
        sandbox = await self._get_bound_sandbox(sandbox_id)
        if sandbox is None or get_active_lease_registry().holder(session_id, workspace_id=workspace_scope) is not None:
            return

        # Keep the provider stop request owned if a new acquire cancels this
        # idle task while the request is in flight.  The acquire path awaits
        # this task before it can obtain a new lease for the Session.
        stop_task = asyncio.create_task(self._platform.stop(sandbox_id))
        _retain_provider_task(stop_task, self._provider_tasks)
        try:
            await asyncio.shield(stop_task)
        except asyncio.CancelledError:
            await asyncio.shield(stop_task)
            raise
        if get_active_lease_registry().holder(session_id, workspace_id=workspace_scope) is not None:
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
        update = asyncio.ensure_future(
            self._bindings.upsert(replace(latest, provider_state="stopped", last_verified_at=datetime.now(UTC)))
        )
        _retain_provider_task(update, self._provider_tasks)
        try:
            await asyncio.shield(update)
        except asyncio.CancelledError:
            # A canceled idle task must not let a late persistence write race
            # a new acquisition for this Session.
            await OwnedEffect.from_task(update).settle()
            raise

    async def aclose(self, *, drain_seconds: float = 30.0) -> bool:
        """Bound policy-task draining without abandoning late ownership."""
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
                # ``schedule_owned_close`` returns a concurrent Future when a
                # loop-bound cleanup owner must run on a disposable loop.
                # Keep that Future strongly owned while waiting through this
                # manager loop.
                pending.add(asyncio.wrap_future(task))
        if pending:
            remaining = max(0.0, deadline - asyncio.get_running_loop().time())
            _, pending = await asyncio.wait(pending, timeout=remaining)
        if pending:
            logger.warning(
                "Daytona lifecycle drain expired with %d owned job(s)",
                len(pending),
            )
            return False

        # A failed worker-thread shutdown is no longer in ``_release_tasks``
        # once its Future settles, but its lease remains strongly owned. Retry
        # those exact leases before declaring the manager disposable.
        retry_release = [
            self._start_release_task(lease) for lease in tuple(self._release_leases.values()) if not lease._released
        ]
        if retry_release:
            remaining = max(0.0, deadline - asyncio.get_running_loop().time())
            _, retry_pending = await asyncio.wait(tuple(retry_release), timeout=remaining)
            if retry_pending or any(not lease._released for lease in self._release_leases.values()):
                logger.warning("Daytona interpreter release ownership remains pending")
                return False

        retry_settled = await self._retry_late_owners(deadline)
        pending_owner = any(owner.manager is self for owner in _LATE_LEASE_OWNERS.values())
        return retry_settled and not pending_owner

    def _retain_late_created_sandbox(self, task: asyncio.Future[Any]) -> None:
        """Retire a Sandbox produced after a bounded creation call returned."""

        async def retire_late() -> None:
            try:
                sandbox = await asyncio.shield(task)
            except BaseException:
                return
            if sandbox is None:
                return
            try:
                receipt = await self._sandbox_retirement_lease(_sandbox_id(sandbox)).aclose()
                if not receipt.clean:
                    logger.warning(
                        "late Daytona Sandbox creation retirement was not confirmed",
                        extra={"sandbox_id": _sandbox_id(sandbox)},
                    )
            except BaseException as exc:
                logger.warning(
                    "late Daytona Sandbox creation retirement failed",
                    extra={"sandbox_id": _sandbox_id(sandbox), "error_type": type(exc).__name__},
                )

        coroutine = retire_late()
        try:
            cleanup = asyncio.create_task(coroutine, name="fleet-daytona-late-sandbox-creation-cleanup")
        except BaseException:
            coroutine.close()
            # The provider request remains in ``_provider_tasks``. A closing
            # loop cannot safely start another coroutine; ownership is retained
            # for the next manager/composition retry.
            return
        _retain_provider_task(cleanup, self._provider_tasks)

    async def _resolve_volume_id(self, *, deadline: float | None = None) -> str:
        """Retry one safe transient failure before sandbox creation can begin."""
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
        """Replace an unrecoverable Sandbox; keep Volume id and Workspace scope."""
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
            # Never replace the durable row while the old provider ownership is
            # unconfirmed.  Otherwise a failed replacement loses the only
            # identity from which recovery can retry the old Sandbox.
            retirement = self._sandbox_retirement_lease(binding.sandbox_id)
            try:
                receipt = await retirement.aclose(deadline=deadline)
            except TimeoutError as exc:
                # The lease close task remains strongly owned by SandboxLease;
                # do not advance the durable binding while its provider delete
                # or absence confirmation is still in flight.
                raise DaytonaAdapterError(
                    message="sandbox retirement timed out",
                    cause_type="SandboxRetirementTimeout",
                ) from exc
            if not receipt.clean:
                # A retained-session lease may return a quarantine receipt while
                # its delete/absence probe is still owned. Replacement must not
                # advance the durable identity until that owner settles. A
                # caller deadline bounds this wait; the close owner remains
                # available for a later retry.
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
            )
            return await self._bindings.upsert(new_binding)
        except BaseException:
            if sandbox is not None:
                with contextlib.suppress(BaseException):
                    receipt = await self._sandbox_retirement_lease(_sandbox_id(sandbox)).aclose()
                    if not receipt.clean:
                        logger.warning(
                            "replacement Sandbox retirement was not confirmed",
                            extra={"sandbox_id": _sandbox_id(sandbox)},
                        )
            # Keep the old row scoped and explicitly ineligible if persistence
            # still permits the quarantine marker.  Never replace it with a
            # partially verified new identity.
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
        del volume_id, mount_path  # reserved for future remount checks
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
        # missing / unrecoverable → caller should create; signal by raising
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
                # The acquisition task owns the late result and can safely
                # continue its ordered cleanup after the public deadline.
                return await _settle_provider_task(exc.task)
            # Direct replacement callers must return at their deadline. The
            # late result gets an independent retirement owner so a Sandbox
            # created after the timeout cannot leak.
            self._retain_late_created_sandbox(exc.task)
            raise DaytonaLeaseAcquisitionTimeoutError(f"Daytona {exc.operation} timed out") from None


__all__ = [
    "ActiveLeaseConflictError",
    "BindingStoreLike",
    "DaytonaAdmission",
    "DaytonaAdmissionTimeoutError",
    "DaytonaSessionManager",
    "InterpreterLease",
    "LeaseRequest",
    "binding_matches_expected",
    "get_active_lease_registry",
    "workspace_volume_subpath",
]
