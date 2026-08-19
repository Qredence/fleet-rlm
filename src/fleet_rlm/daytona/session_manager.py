"""DaytonaSessionManager: acquire/release leases and capability-aware lifecycle.

Release never deletes a Sandbox. Volume identity is preserved across replace.
Workspace Volume Scope uses VolumeMount subpath ``workspaces/<workspace_id>``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import Lock
from typing import Any, Protocol
from uuid import UUID, uuid4

from fleet_rlm.chat.run_cleanup import RunCleanupSupervisor, RunCleanupUnavailableError

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
    require_non_zero_workspace_id,
    require_scoped_volume_subpath,
    workspace_volume_subpath,
)
from fleet_rlm.daytona.sandbox_lease import SandboxLease, SandboxLeasePolicy, SandboxLeaseReceipt
from fleet_rlm.runtime.bindings import SandboxBinding
from fleet_rlm.runtime.owned_effect import OwnedEffect

logger = logging.getLogger(__name__)


class ActiveLeaseConflictError(RuntimeError):
    """Another run already holds the active lease for this Session."""

    def __init__(self, session_id: UUID, holder_run_id: UUID | None = None) -> None:
        self.session_id = session_id
        self.holder_run_id = holder_run_id
        super().__init__(f"active lease conflict for session {session_id}")


class ActiveLeaseRegistry:
    """At most one active Interpreter Lease per Session in this process."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._holders: dict[UUID, UUID] = {}

    def acquire(self, session_id: UUID, run_id: UUID) -> None:
        with self._lock:
            existing = self._holders.get(session_id)
            if existing is not None and existing != run_id:
                raise ActiveLeaseConflictError(session_id, holder_run_id=existing)
            self._holders[session_id] = run_id

    def release(self, session_id: UUID, run_id: UUID) -> None:
        with self._lock:
            if self._holders.get(session_id) == run_id:
                del self._holders[session_id]

    def holder(self, session_id: UUID) -> UUID | None:
        with self._lock:
            return self._holders.get(session_id)


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
    volume_subpath: str | None = None
    delete_sandbox: Callable[[str], None] | None = None
    created_sandbox: bool = False
    _released: bool = field(default=False, init=False, repr=False)
    _on_release: Callable[[], None] | None = field(default=None, init=False, repr=False)

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        try:
            self.interpreter.shutdown()
        finally:
            if self._on_release is not None:
                with contextlib.suppress(Exception):
                    self._on_release()


class DaytonaLeaseAcquisitionTimeoutError(RuntimeError):
    """The Turn deadline elapsed while provider lease work was in flight."""


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
        self._idle_tasks: dict[UUID, asyncio.Task[None]] = {}
        self._provisioner = SandboxProvisioner(
            platform=platform,
            volume_config=volume_config,
            sandbox_spec=sandbox_spec,
        )

    def _expected_mount(self, *, volume_id: str, workspace_id: UUID) -> ExpectedWorkspaceMount:
        return self._provisioner.expected_mount(
            volume_id=volume_id,
            workspace_id=workspace_id,
        )

    def _sandbox_retirement_lease(self, sandbox_id: str, *, confirm_timeout_s: float = 120.0) -> SandboxLease:
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
            ),
        )

    async def acquire(self, request: LeaseRequest, *, deadline: float) -> InterpreterLease:
        """Ensure a running Sandbox with Workspace Volume Scope; return a lease."""
        require_non_zero_workspace_id(request.workspace_id)
        run_id = request.run_id or uuid4()
        session_id = request.session_id
        self._cancel_idle_stop(session_id)
        get_active_lease_registry().acquire(session_id, run_id)
        claim_held = True
        permit: DaytonaAdmissionPermit | None = None
        try:
            permit = await self._admission.acquire(deadline=deadline)
            acquisition = asyncio.create_task(self._acquire_provider(request, run_id=run_id))
            try:
                async with asyncio.timeout_at(deadline):
                    lease = await asyncio.shield(acquisition)
            except TimeoutError:
                if acquisition.done():
                    raise
                self._adopt_late_acquisition(acquisition, permit, request, run_id)
                permit = None
                claim_held = False
                raise DaytonaLeaseAcquisitionTimeoutError("Daytona lease acquisition timed out") from None
            except asyncio.CancelledError:
                self._adopt_late_acquisition(acquisition, permit, request, run_id)
                permit = None
                claim_held = False
                raise

            self._bind_lease_ownership(lease, permit, session_id=session_id, run_id=run_id)
            return lease
        except BaseException:
            try:
                if permit is not None:
                    permit.release()
            finally:
                if claim_held:
                    get_active_lease_registry().release(session_id, run_id)
            raise

    @staticmethod
    async def _settle_provider_acquisition(
        acquisition: asyncio.Task[InterpreterLease],
    ) -> InterpreterLease:
        """Wait through repeated caller cancellation until provider work settles."""
        effect = OwnedEffect.from_task(acquisition)
        await effect.settle()
        return effect.result()

    def _adopt_late_acquisition(
        self,
        acquisition: asyncio.Task[InterpreterLease],
        permit: DaytonaAdmissionPermit,
        request: LeaseRequest,
        run_id: UUID,
    ) -> None:
        """
        Schedule cleanup for an acquisition that completes after its lease request ends.

        Parameters:
            acquisition (asyncio.Task[InterpreterLease]): The provider acquisition task to settle.
            permit (DaytonaAdmissionPermit): The admission permit to release after cleanup.
            request (LeaseRequest): The request associated with the late-acquired lease.
            run_id (UUID): The run identifier to release from the active lease registry.
        """

        async def cleanup() -> None:
            try:
                lease = await self._settle_provider_acquisition(acquisition)
            except BaseException:
                permit.release()
                get_active_lease_registry().release(request.session_id, run_id)
                return
            try:
                lease.release()
                await self._quarantine(lease, request)
            finally:
                permit.release()
                get_active_lease_registry().release(request.session_id, run_id)

        self._cleanup.submit(cleanup())

    async def _quarantine(self, lease: InterpreterLease, request: LeaseRequest) -> None:
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
            )
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
                self._cleanup.submit(deletion)
            except RunCleanupUnavailableError:
                deletion.close()
                await _retire()
                receipt = receipt_box["receipt"]
                if receipt.first_error is not None:
                    logger.warning(
                        "retired session sandbox deletion was not confirmed",
                        extra={"sandbox_id": lease.sandbox_id, "error": receipt.first_error},
                    )

    async def fence_session(self, session_id: UUID) -> None:
        """Fence a Sandbox retained by a settling Run during startup recovery."""
        binding = await self._bindings.get(session_id)
        if binding is None or not binding.sandbox_id:
            return
        await self._fence_binding(binding)

    async def _fence_binding(self, binding: SandboxBinding) -> None:
        """Persist fencing around one awaited provider stop."""
        await self._bindings.upsert(
            SandboxBinding(
                session_id=binding.session_id,
                sandbox_id=binding.sandbox_id,
                workspace_id=binding.workspace_id,
                volume_id=binding.volume_id,
                volume_subpath=binding.volume_subpath,
                mount_path=binding.mount_path,
                provider_state="fencing",
                last_verified_at=datetime.now(UTC),
            )
        )
        if binding.sandbox_id is None:
            return
        # Lease-backed (QRE-156/AC4): recovery fencing rides the shared
        # provider lifecycle adapter; the receipt records the provider action
        # and failure without letting force=True deletion go unrecorded.
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
            ),
        )

        async def _fenced_stop() -> None:
            receipt = await fence_lease.aclose()
            if receipt.first_error is not None:
                raise RuntimeError(str(receipt.first_error))

        await asyncio.wait_for(
            _fenced_stop(),
            timeout=60,
        )
        await self._bindings.upsert(
            SandboxBinding(
                session_id=binding.session_id,
                sandbox_id=binding.sandbox_id,
                workspace_id=binding.workspace_id,
                volume_id=binding.volume_id,
                volume_subpath=binding.volume_subpath,
                mount_path=binding.mount_path,
                provider_state="quarantined",
                last_verified_at=datetime.now(UTC),
            )
        )

    @staticmethod
    def _bind_lease_ownership(
        lease: InterpreterLease,
        permit: DaytonaAdmissionPermit,
        *,
        session_id: UUID,
        run_id: UUID,
    ) -> None:
        def _clear_active() -> None:
            try:
                permit.release()
            finally:
                get_active_lease_registry().release(session_id, run_id)

        lease._on_release = _clear_active

    async def _acquire_provider(self, request: LeaseRequest, *, run_id: UUID) -> InterpreterLease:
        """Complete provider work after admission; caller owns settlement."""
        context = await self._resolve_acquisition_context(request)
        sandbox, created_sandbox = await self._prepare_sandbox(request, context)
        await self._verify_run_layout(sandbox, context.expected, request.session_id, run_id, created_sandbox)
        return await self._persist_binding_and_build_lease(request, run_id, context.expected, sandbox, created_sandbox)

    async def _resolve_acquisition_context(self, request: LeaseRequest) -> _AcquisitionContext:
        """Resolve the workspace mount and reject bindings still under provider fencing."""
        volume_id = await self._resolve_volume_id()
        expected = self._expected_mount(volume_id=volume_id, workspace_id=request.workspace_id)
        binding = await self._bindings.get(request.session_id)
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
    ) -> tuple[Any, bool]:
        """Reuse a verified binding or replace/create a Sandbox for the workspace."""
        sandbox = await self._reuse_bound_sandbox(request, context)
        created_sandbox = sandbox is None
        if created_sandbox:
            sandbox = await self._create_sandbox(
                volume_id=context.expected.volume_id,
                mount_path=context.expected.mount_path,
                volume_subpath=context.expected.volume_subpath,
                request=request,
            )
        return sandbox, created_sandbox

    async def _reuse_bound_sandbox(
        self,
        request: LeaseRequest,
        context: _AcquisitionContext,
    ) -> Any | None:
        binding = context.binding
        if binding is None or not binding.sandbox_id or binding.provider_state in {"quarantined", "unrecoverable"}:
            return None
        if not binding_matches_expected(binding, context.expected):
            raise DaytonaAdapterError(
                message="sandbox binding does not match workspace scope",
                cause_type="WorkspaceMountMismatch",
            )
        sandbox = await self._get_bound_sandbox(binding.sandbox_id)
        if sandbox is None:
            return None
        try:
            self._provisioner.verify(sandbox, context.expected)
            sandbox = await self._ensure_running(
                sandbox,
                sandbox_state(sandbox),
                volume_id=context.expected.volume_id,
                mount_path=context.expected.mount_path,
            )
            self._provisioner.verify(sandbox, context.expected)
            return sandbox
        except ProviderRequestError:
            raise
        except DaytonaAdapterError as exc:
            if exc.cause_type not in {"SandboxUnrecoverable", "SandboxSnapshotMismatch"}:
                raise
            replacement = await self.replace(
                SandboxBinding(
                    session_id=request.session_id,
                    sandbox_id=binding.sandbox_id,
                    workspace_id=request.workspace_id,
                    volume_id=context.expected.volume_id,
                    volume_subpath=context.expected.volume_subpath,
                    mount_path=context.expected.mount_path,
                    provider_state="unrecoverable",
                ),
                workspace_id=request.workspace_id,
                user_id=request.user_id,
            )
            replacement_id = replacement.sandbox_id
            if not replacement_id:
                raise DaytonaAdapterError(
                    message="sandbox replacement did not produce a sandbox id",
                    cause_type="SandboxReplaceIdentityError",
                ) from exc
            replacement_sandbox = await self._get_bound_sandbox(replacement_id)
            if replacement_sandbox is None:
                raise DaytonaAdapterError(
                    message="replacement sandbox is not retrievable",
                    cause_type="SandboxUnrecoverable",
                ) from exc
            return replacement_sandbox

    async def _get_bound_sandbox(self, sandbox_id: str) -> Any | None:
        try:
            return await self._platform.get(sandbox_id)
        except ProviderRequestError:
            raise
        except DaytonaAdapterError:
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
    ) -> None:
        """Verify the run-specific layout and delete only a newly created invalid Sandbox."""
        try:
            await self._provisioner.verify_run_layout(
                sandbox,
                expected,
                session_id=session_id,
                run_id=run_id,
            )
        except BaseException:
            if created_sandbox:
                with contextlib.suppress(Exception):
                    await self._sandbox_retirement_lease(_sandbox_id(sandbox), confirm_timeout_s=30.0).aclose()
            raise

    async def _persist_binding_and_build_lease(
        self,
        request: LeaseRequest,
        run_id: UUID,
        expected: ExpectedWorkspaceMount,
        sandbox: Any,
        created_sandbox: bool,
    ) -> InterpreterLease:
        """Persist the verified provider binding and construct the caller-owned interpreter lease."""
        session_id = request.session_id
        sid = _sandbox_id(sandbox)
        await self._bindings.upsert(
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
            delete_sandbox=None,
            created_sandbox=created_sandbox,
        )

    async def release(self, lease: InterpreterLease) -> None:
        """Release the interpreter and schedule the explicit retained-Sandbox idle stop."""
        # The Daytona interpreter is a synchronous facade over the async SDK.
        # Its shutdown must not run on the event loop that owns the bridge.
        await asyncio.to_thread(lease.release)
        if self._idle_stop_seconds is None or lease.session_id is None:
            return
        session_id = UUID(lease.session_id)
        self._cancel_idle_stop(session_id)
        task = asyncio.create_task(
            self._stop_after_idle(
                session_id=session_id,
                sandbox_id=lease.sandbox_id,
                delay=self._idle_stop_seconds,
            )
        )
        self._idle_tasks[session_id] = task
        task.add_done_callback(lambda completed, sid=session_id: self._forget_idle_task(sid, completed))

    def _cancel_idle_stop(self, session_id: UUID) -> None:
        task = self._idle_tasks.pop(session_id, None)
        if task is not None:
            task.cancel()

    def _forget_idle_task(self, session_id: UUID, task: asyncio.Task[None]) -> None:
        if self._idle_tasks.get(session_id) is task:
            self._idle_tasks.pop(session_id, None)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.warning(
                "Retained Daytona Sandbox idle stop failed",
                extra={
                    "session_id": str(session_id),
                    "error_type": type(error).__name__,
                },
            )

    async def _stop_after_idle(self, *, session_id: UUID, sandbox_id: str, delay: float) -> None:
        await asyncio.sleep(delay)
        if get_active_lease_registry().holder(session_id) is not None:
            return
        binding = await self._bindings.get(session_id)
        if binding is None or binding.sandbox_id != sandbox_id or binding.provider_state != "running":
            return
        sandbox = await self._platform.get(sandbox_id)
        if sandbox is None:
            return
        await self._platform.stop(sandbox_id)
        await self._bindings.upsert(
            SandboxBinding(
                session_id=binding.session_id,
                sandbox_id=binding.sandbox_id,
                workspace_id=binding.workspace_id,
                volume_id=binding.volume_id,
                volume_subpath=binding.volume_subpath,
                mount_path=binding.mount_path,
                provider_state="stopped",
                last_verified_at=datetime.now(UTC),
            )
        )

    async def aclose(self) -> None:
        """Cancel process-local idle policy tasks during composition shutdown."""
        tasks = tuple(self._idle_tasks.values())
        self._idle_tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _resolve_volume_id(self) -> str:
        """Retry one safe transient failure before sandbox creation can begin."""
        for attempt in range(2):
            try:
                return await get_or_create_volume_id(self._volume_client, self._volume_config)
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
    ) -> SandboxBinding:
        """Replace an unrecoverable Sandbox; keep Volume id and Workspace scope."""
        resolved_workspace = workspace_id or binding.workspace_id
        require_non_zero_workspace_id(resolved_workspace)
        if user_id is None or user_id == UUID(int=0):
            raise DaytonaAdapterError(
                message="replace requires a real user_id (zero UUID is forbidden)",
                cause_type="SandboxReplaceIdentityError",
            )
        volume_id = binding.volume_id or await get_or_create_volume_id(self._volume_client, self._volume_config)
        expected = self._expected_mount(volume_id=volume_id, workspace_id=resolved_workspace)
        if binding.sandbox_id:
            with contextlib.suppress(Exception):
                await self._sandbox_retirement_lease(binding.sandbox_id).aclose()
        request = LeaseRequest(
            session_id=binding.session_id,
            user_id=user_id,
            workspace_id=resolved_workspace,
        )
        sandbox = await self._create_sandbox(
            volume_id=expected.volume_id,
            mount_path=expected.mount_path,
            volume_subpath=expected.volume_subpath,
            request=request,
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

    async def _ensure_running(
        self,
        sandbox: Any,
        state: str,
        *,
        volume_id: str,
        mount_path: str,
    ) -> Any:
        del volume_id, mount_path  # reserved for future remount checks
        if state == "running":
            return sandbox
        if state in {"stopped", "paused", "archived"}:
            try:
                await self._platform.start(_sandbox_id(sandbox))
                return await self._platform.get(_sandbox_id(sandbox)) or sandbox
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
    ) -> Any:
        expected = ExpectedWorkspaceMount(
            volume_id=volume_id,
            volume_subpath=volume_subpath,
            mount_path=mount_path,
            workspace_id=request.workspace_id,
        )
        return await self._provisioner.create(
            expected,
            labels={
                "session_id": str(request.session_id),
                "user_id": str(request.user_id),
                "workspace_id": str(request.workspace_id),
                "fleet_package": "fleet_rlm",
                "volume_subpath": expected.volume_subpath,
            },
            ephemeral=False,
        )


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
