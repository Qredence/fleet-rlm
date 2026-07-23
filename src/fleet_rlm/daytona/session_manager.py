"""DaytonaSessionManager: acquire/release leases and capability-aware lifecycle.

Release never deletes a Sandbox. Volume identity is preserved across replace.
Workspace Volume Scope uses VolumeMount subpath ``workspaces/<workspace_id>``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID, uuid4

from fleet_rlm.chat.turn_cleanup import TurnCleanupSupervisor, TurnCleanupUnavailable
from fleet_rlm.daytona.admission import DaytonaAdmission, DaytonaAdmissionPermit
from fleet_rlm.daytona.bindings import SandboxBinding
from fleet_rlm.daytona.errors import (
    DaytonaAdapterError,
    ProviderRequestError,
    is_safe_pre_creation_retry,
    map_provider_error,
)
from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, sandbox_backend
from fleet_rlm.daytona.leases import InterpreterLease
from fleet_rlm.daytona.lifecycle import (
    LifecycleCapabilityError,
    call_if_supported,
    sandbox_state,
)
from fleet_rlm.daytona.sandbox_spec import DaytonaSandboxSpec, verify_sandbox_spec
from fleet_rlm.daytona.volume_layout import ensure_volume_layout
from fleet_rlm.daytona.volumes import (
    VolumeClient,
    VolumeConfig,
    get_or_create_volume_id,
    require_non_zero_workspace_id,
    require_scoped_volume_subpath,
    volume_mount_spec,
    workspace_volume_subpath,
)


class DaytonaLeaseAcquisitionTimeout(RuntimeError):
    """The Turn deadline elapsed while provider lease work was in flight."""


@dataclass(frozen=True, slots=True)
class LeaseRequest:
    session_id: UUID
    user_id: UUID
    workspace_id: UUID
    run_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class ExpectedWorkspaceMount:
    volume_id: str
    volume_subpath: str
    mount_path: str
    workspace_id: UUID


class BindingStoreLike(Protocol):
    async def get(self, session_id: UUID) -> SandboxBinding | None: ...

    async def upsert(self, binding: SandboxBinding) -> SandboxBinding: ...


class SandboxPlatform(Protocol):
    """Minimal provider surface; unit tests inject fakes."""

    def get(self, sandbox_id: str) -> Any | None: ...

    def create(
        self,
        *,
        volume_id: str,
        mount_path: str,
        volume_subpath: str,
        labels: dict[str, str] | None = None,
    ) -> Any: ...

    def delete(self, sandbox_id: str) -> None: ...

    def stop(self, sandbox_id: str, *, timeout: float = 60, force: bool = False) -> None: ...


def _sandbox_id(sandbox: Any) -> str:
    sid = getattr(sandbox, "id", None)
    if sid is None:
        raise DaytonaAdapterError(message="sandbox missing id", cause_type="SandboxIdentityError")
    return str(sid)


def _build_interpreter(sandbox: Any) -> DaytonaCodeInterpreter:
    """Attach a code-interpreter backend when the sandbox exposes one."""
    if hasattr(sandbox, "code_interpreter"):
        return DaytonaCodeInterpreter(backend=sandbox_backend(sandbox))
    # Fake/test sandboxes may already carry an interpreter attribute.
    existing = getattr(sandbox, "interpreter", None)
    if isinstance(existing, DaytonaCodeInterpreter):
        return existing
    return DaytonaCodeInterpreter(backend=getattr(sandbox, "backend", None))


def _mount_field(mount: Any, key: str) -> str | None:
    if isinstance(mount, dict):
        value = mount.get(key)
    else:
        value = getattr(mount, key, None)
    if value is None:
        return None
    return str(value)


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


def verify_sandbox_workspace_mount(
    sandbox: Any,
    expected: ExpectedWorkspaceMount,
) -> None:
    """Fail closed when the live Sandbox mount/labels disagree with expected scope."""
    labels = getattr(sandbox, "labels", None)
    if isinstance(labels, dict) and labels:
        labeled = str(labels.get("workspace_id") or "").strip()
        if labeled and labeled != str(expected.workspace_id):
            raise DaytonaAdapterError(
                message="sandbox workspace label does not match lease workspace",
                cause_type="WorkspaceMountMismatch",
            )

    mounts = getattr(sandbox, "volumes", None)
    if mounts is None:
        mounts = getattr(sandbox, "mounts", None)
    if not mounts:
        # Fake/limited sandboxes may only expose flat fields.
        flat_sub = getattr(sandbox, "volume_subpath", None)
        flat_vid = getattr(sandbox, "volume_id", None)
        flat_mount = getattr(sandbox, "mount_path", None)
        if flat_sub is None and flat_vid is None and flat_mount is None:
            return
        mounts = [
            {
                "volume_id": flat_vid,
                "mount_path": flat_mount,
                "subpath": flat_sub,
            }
        ]

    for mount in mounts:
        vid = _mount_field(mount, "volume_id")
        mpath = _mount_field(mount, "mount_path")
        sub = _mount_field(mount, "subpath") or _mount_field(mount, "volume_subpath")
        if vid == expected.volume_id and mpath == expected.mount_path and sub == expected.volume_subpath:
            return

    raise DaytonaAdapterError(
        message="sandbox volume mount does not match workspace scope",
        cause_type="WorkspaceMountMismatch",
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
        cleanup: TurnCleanupSupervisor | None = None,
    ) -> None:
        self._platform = platform
        self._volume_client = volume_client
        self._volume_config = volume_config
        self._bindings = bindings
        self._admission = admission or DaytonaAdmission()
        self._sandbox_spec = sandbox_spec
        self._cleanup = cleanup or TurnCleanupSupervisor()

    def _expected_mount(self, *, volume_id: str, workspace_id: UUID) -> ExpectedWorkspaceMount:
        require_non_zero_workspace_id(workspace_id)
        spec = volume_mount_spec(self._volume_config, volume_id, workspace_id=workspace_id)
        return ExpectedWorkspaceMount(
            volume_id=spec["volume_id"],
            volume_subpath=spec["subpath"],
            mount_path=spec["mount_path"],
            workspace_id=workspace_id,
        )

    async def acquire(self, request: LeaseRequest, *, deadline: float) -> InterpreterLease:
        """Ensure a running Sandbox with Workspace Volume Scope; return a lease."""
        from fleet_rlm.daytona.active_leases import get_active_lease_registry

        require_non_zero_workspace_id(request.workspace_id)
        run_id = request.run_id or uuid4()
        session_id = request.session_id
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
                raise DaytonaLeaseAcquisitionTimeout("Daytona lease acquisition timed out") from None
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
        while not acquisition.done():
            try:
                await asyncio.shield(acquisition)
            except asyncio.CancelledError:
                continue
        return acquisition.result()

    def _adopt_late_acquisition(
        self,
        acquisition: asyncio.Task[InterpreterLease],
        permit: DaytonaAdmissionPermit,
        request: LeaseRequest,
        run_id: UUID,
    ) -> None:
        from fleet_rlm.daytona.active_leases import get_active_lease_registry

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

        await self._bindings.upsert(
            SandboxBinding(
                session_id=request.session_id,
                sandbox_id=lease.sandbox_id,
                workspace_id=request.workspace_id,
                volume_id=lease.volume_id,
                volume_subpath=lease.volume_subpath or workspace_volume_subpath(request.workspace_id),
                mount_path=lease.mount_path,
                provider_state="fencing",
                last_verified_at=datetime.now(UTC),
            )
        )

        def stop_target(target: Any, *, platform_call: bool) -> None:
            try:
                if platform_call:
                    target(lease.sandbox_id, timeout=60, force=True)
                else:
                    target(timeout=60, force=True)
            except TypeError:
                # Narrow fake/legacy adapters have no force parameters. The
                # production Daytona Sandbox surface is verified separately.
                if platform_call:
                    target(lease.sandbox_id)
                else:
                    target()

        stop = getattr(self._platform, "stop", None)
        if callable(stop):
            await asyncio.wait_for(
                asyncio.to_thread(stop_target, stop, platform_call=True),
                timeout=60,
            )
        else:
            sandbox = await asyncio.to_thread(self._platform.get, lease.sandbox_id)
            if sandbox is None:
                return
            await asyncio.wait_for(
                asyncio.to_thread(stop_target, sandbox.stop, platform_call=False),
                timeout=60,
            )
        await self._bindings.upsert(
            SandboxBinding(
                session_id=request.session_id,
                sandbox_id=lease.sandbox_id,
                workspace_id=request.workspace_id,
                volume_id=lease.volume_id,
                volume_subpath=lease.volume_subpath or workspace_volume_subpath(request.workspace_id),
                mount_path=lease.mount_path,
                provider_state="quarantined",
                last_verified_at=datetime.now(UTC),
            )
        )
        if lease.created_sandbox:
            deletion = asyncio.to_thread(self._platform.delete, lease.sandbox_id)
            try:
                self._cleanup.submit(deletion)
            except TurnCleanupUnavailable:
                deletion.close()

    async def fence_session(self, session_id: UUID) -> None:
        """Fence a Sandbox retained by a settling Run during startup recovery."""
        binding = await self._bindings.get(session_id)
        if binding is None or not binding.sandbox_id:
            return
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
        stop = getattr(self._platform, "stop", None)
        if callable(stop):
            try:
                await asyncio.wait_for(
                    asyncio.to_thread(stop, binding.sandbox_id, timeout=60, force=True),
                    timeout=60,
                )
            except TypeError:
                await asyncio.wait_for(asyncio.to_thread(stop, binding.sandbox_id), timeout=60)
        else:
            sandbox = await asyncio.to_thread(self._platform.get, binding.sandbox_id)
            if sandbox is None:
                return
            try:
                await asyncio.wait_for(
                    asyncio.to_thread(sandbox.stop, timeout=60, force=True),
                    timeout=60,
                )
            except TypeError:
                await asyncio.wait_for(asyncio.to_thread(sandbox.stop), timeout=60)
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
        from fleet_rlm.daytona.active_leases import get_active_lease_registry

        def _clear_active() -> None:
            try:
                permit.release()
            finally:
                get_active_lease_registry().release(session_id, run_id)

        lease._on_release = _clear_active  # noqa: SLF001

    async def _acquire_provider(self, request: LeaseRequest, *, run_id: UUID) -> InterpreterLease:
        """Complete provider work after admission; caller owns settlement."""
        session_id = request.session_id
        volume_id = await self._resolve_volume_id()
        expected = self._expected_mount(volume_id=volume_id, workspace_id=request.workspace_id)
        binding = await self._bindings.get(session_id)

        if binding is not None and binding.provider_state == "fencing":
            raise DaytonaAdapterError(
                message="sandbox execution fence is not confirmed",
                cause_type="SandboxFenceUnconfirmed",
            )

        sandbox: Any | None = None
        if (
            binding is not None
            and binding.sandbox_id
            and binding.provider_state not in {"quarantined", "unrecoverable"}
        ):
            if not binding_matches_expected(binding, expected):
                raise DaytonaAdapterError(
                    message="sandbox binding does not match workspace scope",
                    cause_type="WorkspaceMountMismatch",
                )
            try:
                sandbox = await asyncio.to_thread(self._platform.get, binding.sandbox_id)
            except ProviderRequestError:
                raise
            except DaytonaAdapterError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise map_provider_error(exc) from exc

        if sandbox is not None:
            try:
                verify_sandbox_workspace_mount(sandbox, expected)
                verify_sandbox_spec(sandbox, self._sandbox_spec)
                state = sandbox_state(sandbox)
                sandbox = await self._ensure_running(
                    sandbox,
                    state,
                    volume_id=expected.volume_id,
                    mount_path=expected.mount_path,
                )
                verify_sandbox_workspace_mount(sandbox, expected)
                verify_sandbox_spec(sandbox, self._sandbox_spec)
            except ProviderRequestError:
                raise
            except DaytonaAdapterError as exc:
                if exc.cause_type not in {"SandboxUnrecoverable", "SandboxSnapshotMismatch"}:
                    raise
                if binding is not None:
                    binding = await self.replace(
                        SandboxBinding(
                            session_id=session_id,
                            sandbox_id=binding.sandbox_id,
                            workspace_id=request.workspace_id,
                            volume_id=expected.volume_id,
                            volume_subpath=expected.volume_subpath,
                            mount_path=expected.mount_path,
                            provider_state="unrecoverable",
                        ),
                        workspace_id=request.workspace_id,
                        user_id=request.user_id,
                    )
                    sandbox = await asyncio.to_thread(self._platform.get, binding.sandbox_id or "")
                else:
                    sandbox = None
            except LifecycleCapabilityError:
                if binding is not None:
                    binding = await self.replace(
                        SandboxBinding(
                            session_id=session_id,
                            sandbox_id=binding.sandbox_id,
                            workspace_id=request.workspace_id,
                            volume_id=expected.volume_id,
                            volume_subpath=expected.volume_subpath,
                            mount_path=expected.mount_path,
                            provider_state="unrecoverable",
                        ),
                        workspace_id=request.workspace_id,
                        user_id=request.user_id,
                    )
                    sandbox = await asyncio.to_thread(self._platform.get, binding.sandbox_id or "")
                else:
                    sandbox = None
        created_sandbox = False
        if sandbox is None:
            sandbox = await asyncio.to_thread(
                self._create_sandbox,
                volume_id=expected.volume_id,
                mount_path=expected.mount_path,
                volume_subpath=expected.volume_subpath,
                request=request,
            )
            created_sandbox = True

        try:
            verify_sandbox_workspace_mount(sandbox, expected)
            verify_sandbox_spec(sandbox, self._sandbox_spec)
            await asyncio.to_thread(
                ensure_volume_layout,
                sandbox,
                self._volume_config.paths(),
                session_id=session_id,
                run_id=run_id,
            )
        except BaseException:
            if created_sandbox:
                try:
                    await asyncio.to_thread(self._platform.delete, _sandbox_id(sandbox))
                except Exception:  # noqa: BLE001 - preserve the acquisition failure
                    pass
            raise

        sid = _sandbox_id(sandbox)
        now = datetime.now(UTC)
        await self._bindings.upsert(
            SandboxBinding(
                session_id=session_id,
                sandbox_id=sid,
                workspace_id=request.workspace_id,
                volume_id=expected.volume_id,
                volume_subpath=expected.volume_subpath,
                mount_path=expected.mount_path,
                provider_state="running",
                last_verified_at=now,
            )
        )

        interpreter = _build_interpreter(sandbox)
        interpreter_id = f"interp-{sid}-{uuid4().hex[:8]}"
        return InterpreterLease(
            sandbox_id=sid,
            interpreter_id=interpreter_id,
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
        """Release interpreter resources only — never deletes the Sandbox."""
        lease.release()

    async def _resolve_volume_id(self) -> str:
        """Retry one safe transient failure before sandbox creation can begin."""
        for attempt in range(2):
            try:
                return await asyncio.to_thread(
                    get_or_create_volume_id,
                    self._volume_client,
                    self._volume_config,
                )
            except Exception as exc:  # noqa: BLE001
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
        volume_id = binding.volume_id or await asyncio.to_thread(
            get_or_create_volume_id, self._volume_client, self._volume_config
        )
        expected = self._expected_mount(volume_id=volume_id, workspace_id=resolved_workspace)
        if binding.sandbox_id:
            try:
                await asyncio.to_thread(self._platform.delete, binding.sandbox_id)
            except Exception:  # noqa: BLE001 - best-effort delete of broken sandbox
                pass
        request = LeaseRequest(
            session_id=binding.session_id,
            user_id=user_id,
            workspace_id=resolved_workspace,
        )
        sandbox = await asyncio.to_thread(
            self._create_sandbox,
            volume_id=expected.volume_id,
            mount_path=expected.mount_path,
            volume_subpath=expected.volume_subpath,
            request=request,
        )
        verify_sandbox_workspace_mount(sandbox, expected)
        verify_sandbox_spec(sandbox, self._sandbox_spec)
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
        if state == "stopped":
            await self._call_lifecycle(sandbox, "start")
            return sandbox
        if state == "paused":
            try:
                await self._call_lifecycle(sandbox, "resume")
            except LifecycleCapabilityError:
                await self._call_lifecycle(sandbox, "start")
            return sandbox
        if state == "archived":
            try:
                await self._call_lifecycle(sandbox, "restore")
            except LifecycleCapabilityError as exc:
                raise LifecycleCapabilityError("restore") from exc
            # After restore, may still need start
            if sandbox_state(sandbox) != "running":
                try:
                    await self._call_lifecycle(sandbox, "start")
                except LifecycleCapabilityError:
                    pass
            return sandbox
        # missing / unrecoverable → caller should create; signal by raising
        raise DaytonaAdapterError(
            message=f"sandbox unusable in state {state}",
            cause_type="SandboxUnrecoverable",
        )

    async def _call_lifecycle(self, sandbox: Any, operation: str) -> None:
        try:
            await asyncio.to_thread(call_if_supported, sandbox, operation)
        except (DaytonaAdapterError, LifecycleCapabilityError):
            raise
        except Exception as exc:  # noqa: BLE001 - normalize provider SDK failures
            raise map_provider_error(exc) from exc

    def _create_sandbox(
        self,
        *,
        volume_id: str,
        mount_path: str,
        volume_subpath: str,
        request: LeaseRequest,
    ) -> Any:
        require_non_zero_workspace_id(request.workspace_id)
        scoped = require_scoped_volume_subpath(volume_subpath, workspace_id=request.workspace_id)
        try:
            return self._platform.create(
                volume_id=volume_id,
                mount_path=mount_path,
                volume_subpath=scoped,
                labels={
                    "session_id": str(request.session_id),
                    "user_id": str(request.user_id),
                    "workspace_id": str(request.workspace_id),
                    "fleet_package": "fleet_rlm",
                    "volume_subpath": scoped,
                },
            )
        except Exception as exc:  # noqa: BLE001
            raise map_provider_error(exc) from exc


# Re-export mount helpers for SessionManager callers
__all__ = [
    "BindingStoreLike",
    "DaytonaSessionManager",
    "ExpectedWorkspaceMount",
    "LeaseRequest",
    "SandboxPlatform",
    "binding_matches_expected",
    "verify_sandbox_workspace_mount",
    "volume_mount_spec",
    "workspace_volume_subpath",
]
