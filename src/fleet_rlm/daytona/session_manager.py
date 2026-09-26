"""DaytonaSessionManager: acquire/release leases and capability-aware lifecycle.

Release never deletes a Sandbox. Volume identity is preserved across replace.
Workspace Volume Scope uses VolumeMount subpath ``workspaces/<workspace_id>``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import weakref
from collections.abc import Awaitable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from threading import Lock
from typing import Any, Protocol
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
from fleet_rlm.daytona.sandbox import (
    PREWARM_RUN_ID,
    ActiveLeaseConflictError,
    ActiveLeaseRegistry,
    DaytonaLeaseAcquisitionTimeoutError,
    InterpreterLease,
    LeaseKind,
    LeaseRequest,
    LeaseState,
    OwnedCloseExecution,
    RootSessionLease,
    SandboxLease,
    SandboxLeasePolicy,
    SandboxLeaseReceipt,
    has_pending_lease_ownership,
    schedule_owned_close,
    wait_lease_ownership,
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
        return await _settle_provider_task(acquisition)

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

    def _track_late_cleanup(self, owner: _LateOwner, task: Any) -> None:
        owner.cleanup_task = task
        self._late_cleanup_tasks.add(task)
        task.add_done_callback(self._settled_late_cleanup)

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
        self._track_late_cleanup(owner, execution.future)
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
        self._track_late_cleanup(owner, task)
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
        try:
            release_task = asyncio.create_task(asyncio.to_thread(lease.release))
            await _settle_provider_task(release_task)
        except asyncio.CancelledError:
            # Cancellation cannot skip remote sandbox quarantine.
            logger.warning("Late interpreter lease release was cancelled; continuing sandbox quarantine")
        except Exception as exc:
            logger.warning(
                "Late interpreter lease release failed; continuing sandbox quarantine",
                extra={"error_type": type(exc).__name__},
            )

        quarantine_error: BaseException | None = None
        # A failed interpreter/broker release cannot discard remote ownership.
        # Fence and retire the sandbox anyway; otherwise a transient local
        # shutdown error leaves the admission slot and provider resource live.
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
        except asyncio.CancelledError as exc:
            quarantine_error = exc
        except Exception as exc:
            # Keep ownership and admission while failed quarantine is retried.
            quarantine_error = exc

        if quarantine_error is not None:
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
        for owner in {id(o): o for o in self._late_owners.values()}.values():
            if owner.unpublished:
                awaitable = self._settle_late_owner(owner, deadline=deadline)
                try:
                    task = asyncio.create_task(awaitable, name="fleet-daytona-unpublished-lease-retry")
                except BaseException:
                    with contextlib.suppress(BaseException):
                        awaitable.close()
                    continue
                self._track_late_cleanup(owner, task)
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
                    tasks.append(task if isinstance(task, asyncio.Future) else asyncio.wrap_future(task))
                continue
            task = owner.cleanup_task
            if task is None or task.done():
                task = asyncio.create_task(self._settle_late_lease(owner), name="fleet-daytona-late-lease-retry")
                self._track_late_cleanup(owner, task)
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
            return await self._replace_bound_sandbox(binding, request, deadline=deadline)
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
            return await self._replace_bound_sandbox(binding, request, deadline=deadline, cause=exc)

    async def _replace_bound_sandbox(
        self,
        binding: SandboxBinding,
        request: LeaseRequest,
        *,
        deadline: float | None = None,
        cause: Exception | None = None,
    ) -> Any:
        replacement = await self.replace(
            replace(binding, provider_state="unrecoverable", last_verified_at=None),
            workspace_id=request.workspace_id,
            user_id=request.user_id,
            deadline=deadline,
        )
        replacement_id = replacement.sandbox_id
        if not replacement_id:
            err = DaytonaAdapterError(
                message="sandbox replacement did not produce a sandbox id",
                cause_type="SandboxReplaceIdentityError",
            )
            raise err from cause if cause else err
        replacement_sandbox = await self._get_bound_sandbox(replacement_id, deadline=deadline)
        if replacement_sandbox is None:
            err = DaytonaAdapterError(
                message="replacement sandbox is not retrievable",
                cause_type="SandboxUnrecoverable",
            )
            raise err from cause if cause else err
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
            if not receipt.clean:
                with contextlib.suppress(BaseException):
                    await cleanup.wait_ownership()
        except BaseException:
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
        try:
            await self._release_interpreter(lease)
        except BaseException:
            if lease.session_id is None or lease.workspace_id is None or lease.run_id is None or lease.user_id is None:
                raise
            # A failed broker/interpreter shutdown still owns a live remote
            # resource.  Retain it behind the existing durable fencing and
            # quarantine path instead of merely logging a failed task.  Keep
            # the original failure visible to the active caller; ``aclose``
            # owns the bounded retry and final provider retirement.
            self._retain_unpublished_lease(
                lease,
                LeaseRequest(
                    session_id=UUID(lease.session_id),
                    workspace_id=UUID(lease.workspace_id),
                    user_id=UUID(lease.user_id),
                    run_id=UUID(lease.run_id),
                ),
            )
            raise

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
            release_error: BaseException | None = None
            prior_release_failed = any(
                known is lease and task.done() and not task.cancelled() and task.exception() is not None
                for task, known in self._release_leases.items()
            )
            if not lease._released and not prior_release_failed:
                try:
                    await self._release_interpreter(lease)
                except BaseException as exc:
                    # Local release cancellation cannot skip provider retirement.
                    release_error = exc
            # Provider retirement is still required after a broker shutdown
            # failure. It is the containment fallback for a lease whose local
            # release could not be confirmed.
            await self._quarantine(lease, owner.request, deadline=deadline)
            if release_error is not None or prior_release_failed:
                # Provider retirement has contained the failed local release.
                # Mark the lease terminal so future shutdown retries cannot
                # reopen a broker that no longer has a remote owner.
                lease._released = True
                lease._state = LeaseState.CLOSED

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
            if release_error is not None:
                logger.info(
                    "Daytona interpreter release was contained by sandbox retirement",
                    extra={"sandbox_id": lease.sandbox_id, "error_type": type(release_error).__name__},
                )

    async def release_and_quarantine(
        self,
        lease: InterpreterLease,
        request: LeaseRequest,
        *,
        deadline: float | None = None,
    ) -> None:
        owner = self._retain_unpublished_lease(lease, request)
        await self._finish_unpublished_lease(owner, deadline=deadline)

    def _retain_unpublished_lease(self, lease: InterpreterLease, request: LeaseRequest) -> _LateOwner:
        """Keep failed interpreter release attached to durable cleanup ownership."""
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
        return owner

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

        unpublished_leases = {
            id(owner.lease) for owner in self._late_owners.values() if owner.unpublished and owner.lease is not None
        }
        retry_release = [
            self._start_release_task(lease)
            for lease in tuple(self._release_leases.values())
            if not lease._released and id(lease) not in unpublished_leases
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
