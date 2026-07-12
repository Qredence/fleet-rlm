"""DaytonaSessionManager: acquire/release leases and capability-aware lifecycle.

Release never deletes a Sandbox. Volume identity is preserved across replace.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID, uuid4

from fleet_rlm_clean.daytona.bindings import SandboxBinding
from fleet_rlm_clean.daytona.errors import DaytonaAdapterError, map_provider_error
from fleet_rlm_clean.daytona.interpreter import DaytonaCodeInterpreter, sandbox_backend
from fleet_rlm_clean.daytona.leases import InterpreterLease
from fleet_rlm_clean.daytona.lifecycle import (
    LifecycleCapabilityError,
    call_if_supported,
    sandbox_state,
)
from fleet_rlm_clean.daytona.volumes import (
    VolumeClient,
    VolumeConfig,
    get_or_create_volume_id,
    volume_mount_spec,
)


@dataclass(frozen=True, slots=True)
class LeaseRequest:
    session_id: UUID
    user_id: UUID
    workspace_id: UUID


class BindingStoreLike(Protocol):
    async def get(self, session_id: UUID) -> SandboxBinding | None: ...

    async def upsert(self, binding: SandboxBinding) -> SandboxBinding: ...


class SandboxPlatform(Protocol):
    """Minimal provider surface; unit tests inject fakes."""

    def get(self, sandbox_id: str) -> Any | None: ...

    def create(self, *, volume_id: str, mount_path: str, labels: dict[str, str] | None = None) -> Any: ...

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


class DaytonaSessionManager:
    """Owns Sandbox lifecycle policy for clean-backend sessions."""

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

    async def acquire(self, request: LeaseRequest) -> InterpreterLease:
        """Ensure a running Sandbox with Volume mounted; return an interpreter lease."""
        volume_id = await asyncio.to_thread(
            get_or_create_volume_id, self._volume_client, self._volume_config
        )
        mount_path = self._volume_config.mount_path
        binding = await self._bindings.get(request.session_id)

        sandbox: Any | None = None
        if binding and binding.sandbox_id:
            try:
                sandbox = await asyncio.to_thread(self._platform.get, binding.sandbox_id)
            except Exception as exc:  # noqa: BLE001
                raise map_provider_error(exc) from exc

        if sandbox is not None:
            state = sandbox_state(sandbox)
            try:
                sandbox = await self._ensure_running(
                    sandbox, state, volume_id=volume_id, mount_path=mount_path
                )
            except (DaytonaAdapterError, LifecycleCapabilityError):
                # Missing/unhealthy/unsupported → replace while keeping Volume.
                if binding is not None:
                    replaced = await self.replace(
                        SandboxBinding(
                            session_id=request.session_id,
                            sandbox_id=binding.sandbox_id,
                            volume_id=volume_id,
                            mount_path=mount_path,
                            provider_state="unrecoverable",
                        )
                    )
                    sandbox = await asyncio.to_thread(
                        self._platform.get, replaced.sandbox_id or ""
                    )
                else:
                    sandbox = None
        if sandbox is None:
            sandbox = await asyncio.to_thread(
                self._create_sandbox,
                volume_id=volume_id,
                mount_path=mount_path,
                request=request,
            )

        sid = _sandbox_id(sandbox)
        now = datetime.now(UTC)
        await self._bindings.upsert(
            SandboxBinding(
                session_id=request.session_id,
                sandbox_id=sid,
                volume_id=volume_id,
                mount_path=mount_path,
                provider_state="running",
                last_verified_at=now,
            )
        )

        interpreter = _build_interpreter(sandbox)
        interpreter_id = f"interp-{sid}-{uuid4().hex[:8]}"
        return InterpreterLease(
            sandbox_id=sid,
            interpreter_id=interpreter_id,
            volume_id=volume_id,
            mount_path=mount_path,
            interpreter=interpreter,
            delete_sandbox=None,  # release must never delete
        )

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

    async def replace(self, binding: SandboxBinding) -> SandboxBinding:
        """Replace an unrecoverable Sandbox; keep the same Volume id and mount path."""
        volume_id = binding.volume_id or await asyncio.to_thread(
            get_or_create_volume_id, self._volume_client, self._volume_config
        )
        mount_path = binding.mount_path or self._volume_config.mount_path
        if binding.sandbox_id:
            try:
                await asyncio.to_thread(self._platform.delete, binding.sandbox_id)
            except Exception:  # noqa: BLE001 - best-effort delete of broken sandbox
                pass
        request = LeaseRequest(
            session_id=binding.session_id,
            user_id=UUID(int=0),
            workspace_id=UUID(int=0),
        )
        sandbox = await asyncio.to_thread(
            self._create_sandbox,
            volume_id=volume_id,
            mount_path=mount_path,
            request=request,
        )
        new_binding = SandboxBinding(
            session_id=binding.session_id,
            sandbox_id=_sandbox_id(sandbox),
            volume_id=volume_id,
            mount_path=mount_path,
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
        request: LeaseRequest,
    ) -> Any:
        try:
            return self._platform.create(
                volume_id=volume_id,
                mount_path=mount_path,
                labels={
                    "session_id": str(request.session_id),
                    "workspace_id": str(request.workspace_id),
                    "fleet_package": "fleet_rlm_clean",
                },
            )
        except Exception as exc:  # noqa: BLE001
            raise map_provider_error(exc) from exc


# Re-export mount helper for SessionManager callers
__all__ = [
    "BindingStoreLike",
    "DaytonaSessionManager",
    "LeaseRequest",
    "SandboxPlatform",
    "volume_mount_spec",
]
