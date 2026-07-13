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

from fleet_rlm.daytona.bindings import SandboxBinding
from fleet_rlm.daytona.errors import DaytonaAdapterError, ProviderRequestError, map_provider_error
from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, sandbox_backend
from fleet_rlm.daytona.leases import InterpreterLease
from fleet_rlm.daytona.lifecycle import (
    LifecycleCapabilityError,
    call_if_supported,
    sandbox_state,
)
from fleet_rlm.daytona.volumes import (
    VolumeClient,
    VolumeConfig,
    get_or_create_volume_id,
    require_non_zero_workspace_id,
    require_scoped_volume_subpath,
    volume_mount_spec,
    workspace_volume_subpath,
)


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
    ) -> None:
        self._platform = platform
        self._volume_client = volume_client
        self._volume_config = volume_config
        self._bindings = bindings

    def _expected_mount(self, *, volume_id: str, workspace_id: UUID) -> ExpectedWorkspaceMount:
        require_non_zero_workspace_id(workspace_id)
        spec = volume_mount_spec(self._volume_config, volume_id, workspace_id=workspace_id)
        return ExpectedWorkspaceMount(
            volume_id=spec["volume_id"],
            volume_subpath=spec["subpath"],
            mount_path=spec["mount_path"],
            workspace_id=workspace_id,
        )

    async def acquire(self, request: LeaseRequest) -> InterpreterLease:
        """Ensure a running Sandbox with Workspace Volume Scope; return a lease."""
        from fleet_rlm.daytona.active_leases import get_active_lease_registry

        require_non_zero_workspace_id(request.workspace_id)
        run_id = request.run_id or uuid4()
        session_id = request.session_id
        get_active_lease_registry().acquire(session_id, run_id)
        try:
            volume_id = await asyncio.to_thread(get_or_create_volume_id, self._volume_client, self._volume_config)
            expected = self._expected_mount(volume_id=volume_id, workspace_id=request.workspace_id)
            binding = await self._bindings.get(session_id)

            sandbox: Any | None = None
            if binding is not None and binding.sandbox_id:
                if not binding_matches_expected(binding, expected):
                    binding = await self.replace(
                        binding,
                        workspace_id=request.workspace_id,
                        user_id=request.user_id,
                    )
                    sandbox = await asyncio.to_thread(self._platform.get, binding.sandbox_id or "")
                else:
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
                    state = sandbox_state(sandbox)
                    sandbox = await self._ensure_running(
                        sandbox,
                        state,
                        volume_id=expected.volume_id,
                        mount_path=expected.mount_path,
                    )
                    verify_sandbox_workspace_mount(sandbox, expected)
                except (DaytonaAdapterError, LifecycleCapabilityError):
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
            if sandbox is None:
                sandbox = await asyncio.to_thread(
                    self._create_sandbox,
                    volume_id=expected.volume_id,
                    mount_path=expected.mount_path,
                    volume_subpath=expected.volume_subpath,
                    request=request,
                )
                verify_sandbox_workspace_mount(sandbox, expected)

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
            lease = InterpreterLease(
                sandbox_id=sid,
                interpreter_id=interpreter_id,
                volume_id=expected.volume_id,
                mount_path=expected.mount_path,
                volume_subpath=expected.volume_subpath,
                interpreter=interpreter,
                session_id=str(session_id),
                run_id=str(run_id),
                delete_sandbox=None,
            )

            def _clear_active() -> None:
                get_active_lease_registry().release(session_id, run_id)

            lease._on_release = _clear_active  # noqa: SLF001
            return lease
        except Exception:
            get_active_lease_registry().release(session_id, run_id)
            raise

    async def release(self, lease: InterpreterLease) -> None:
        """Release interpreter resources only — never deletes the Sandbox."""
        lease.release()

    async def stop(self, sandbox_id: str) -> None:
        sandbox = self._require(sandbox_id)
        try:
            call_if_supported(sandbox, "stop")
        except LifecycleCapabilityError:
            # Fallback: some clients expose stop via platform
            stop = getattr(self._platform, "stop", None)
            if callable(stop):
                stop(sandbox_id)
            else:
                raise

    async def start(self, sandbox_id: str) -> None:
        sandbox = self._require(sandbox_id)
        try:
            call_if_supported(sandbox, "start")
        except LifecycleCapabilityError:
            start = getattr(self._platform, "start", None)
            if callable(start):
                start(sandbox_id)
            else:
                raise

    async def pause(self, sandbox_id: str) -> None:
        sandbox = self._require(sandbox_id)
        call_if_supported(sandbox, "pause")

    async def resume(self, sandbox_id: str) -> None:
        sandbox = self._require(sandbox_id)
        call_if_supported(sandbox, "resume")

    async def archive(self, sandbox_id: str) -> None:
        sandbox = self._require(sandbox_id)
        # Daytona: archive typically requires stopped first when supported.
        state = sandbox_state(sandbox)
        if state == "running":
            try:
                call_if_supported(sandbox, "stop")
            except LifecycleCapabilityError:
                pass
        call_if_supported(sandbox, "archive")

    async def restore(self, sandbox_id: str) -> None:
        sandbox = self._require(sandbox_id)
        call_if_supported(sandbox, "restore")

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

    def _require(self, sandbox_id: str) -> Any:
        try:
            sandbox = self._platform.get(sandbox_id)
        except Exception as exc:  # noqa: BLE001
            raise map_provider_error(exc) from exc
        if sandbox is None:
            raise DaytonaAdapterError(
                message=f"sandbox not found: {sandbox_id}",
                cause_type="SandboxNotFound",
            )
        return sandbox

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
            await asyncio.to_thread(call_if_supported, sandbox, "start")
            return sandbox
        if state == "paused":
            try:
                await asyncio.to_thread(call_if_supported, sandbox, "resume")
            except LifecycleCapabilityError:
                await asyncio.to_thread(call_if_supported, sandbox, "start")
            return sandbox
        if state == "archived":
            try:
                await asyncio.to_thread(call_if_supported, sandbox, "restore")
            except LifecycleCapabilityError as exc:
                raise LifecycleCapabilityError("restore") from exc
            # After restore, may still need start
            if sandbox_state(sandbox) != "running":
                try:
                    await asyncio.to_thread(call_if_supported, sandbox, "start")
                except LifecycleCapabilityError:
                    pass
            return sandbox
        # missing / unrecoverable → caller should create; signal by raising
        raise DaytonaAdapterError(
            message=f"sandbox unusable in state {state}",
            cause_type="SandboxUnrecoverable",
        )

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
