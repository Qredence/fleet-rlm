"""Daytona environment acquisition and scoped storage binding for a Turn."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from pathlib import PurePosixPath
from typing import Any
from uuid import UUID

from fleet_rlm.config.settings import Settings
from fleet_rlm.daytona.diagnostics import environment_manifest
from fleet_rlm.daytona.interpreter import SyncBridgeDispatcher, sync_sandbox
from fleet_rlm.daytona.runtime import (
    DaytonaAdmissionTimeoutError,
    DaytonaLeaseAcquisitionTimeoutError,
    DaytonaRuntime,
    DaytonaSandboxSpec,
    InterpreterLease,
    RootSessionSpec,
    _ensure_directories,
    _sandbox_filesystem,
    ensure_volume_layout,
)
from fleet_rlm.paths import VolumePaths
from fleet_rlm.sessions.run_state import ClaimedRun
from fleet_rlm.turn_preparation import (
    RunEnvironment,
    RunPreparationTimeoutError,
    RunPreparationUnavailableError,
)
from fleet_rlm.workspace.host_io import DaytonaHostIO
from fleet_rlm.workspace.storage import (
    AsyncDaytonaVolumeFS,
    DaytonaSandboxVolumeFs,
    DaytonaSandboxWorkspaceStorage,
)

logger = logging.getLogger(__name__)


class _DaytonaRunSink:
    def __init__(
        self,
        sandbox: Any,
        *,
        dispatcher: SyncBridgeDispatcher,
        paths: VolumePaths,
        host_io: DaytonaHostIO,
        run_id: UUID,
    ) -> None:
        self._sandbox = sandbox
        self._files = AsyncDaytonaVolumeFS(sandbox, mount_path="/tmp/fleet")
        self.sandbox = sync_sandbox(sandbox, asyncio.get_running_loop(), dispatcher)
        self._scratch_fs = DaytonaSandboxVolumeFs(self.sandbox, mount_path="/tmp/fleet")
        self.host_io = host_io
        self.scratch_root = f"/tmp/fleet/{run_id}"
        self._paths = paths
        self.volume_fs = _RunVolumeFs(self)

    def _is_scratch(self, location: str) -> bool:
        root = self.scratch_root
        if root is None:
            return False
        path = PurePosixPath(location)
        return path != PurePosixPath(root) and PurePosixPath(root) in path.parents and ".." not in path.parts

    def _sync_target(self, location: str) -> Any:
        if not self._is_scratch(location):
            return self.host_io.volume_fs
        return self._scratch_fs

    def result_path(self, session_id: UUID, run_id: UUID) -> str:
        return str(self._paths.run_result_path(session_id, run_id))

    async def read(self, location: str, *, max_bytes: int) -> bytes:
        if not self._is_scratch(location):
            value = await self.host_io.volume_fs.aread_bytes(location, max_bytes=max_bytes)
        else:
            value = await self._files.read_bytes(location)
        if len(value) > max_bytes:
            raise ValueError("value exceeds read bound")
        return value

    async def write(self, location: str, data: bytes) -> None:
        if not self._is_scratch(location):
            await self.host_io.volume_fs.awrite_bytes(location, data)
        else:
            await self._files.write_bytes(location, data)

    async def remove(self, location: str) -> None:
        if not self._is_scratch(location):
            await self.host_io.volume_fs.aremove_bytes(location)
        else:
            await self._files.remove_bytes(location)

    async def write_private(self, logical_path: str, data: bytes) -> None:
        if not self._is_scratch(logical_path):
            raise ValueError("Run attachment path is outside Run scratch")
        parent = PurePosixPath(logical_path).parent
        await _ensure_directories(
            _sandbox_filesystem(self._sandbox),
            (str(PurePosixPath(self.scratch_root) / "attachments"), str(parent)),
        )
        await self.write(logical_path, data)

    async def remove_private(self, logical_path: str) -> None:
        if not self._is_scratch(logical_path):
            raise ValueError("Run attachment path is outside Run scratch")
        await self.remove(logical_path)


class _RunVolumeFs:
    """Synchronous view of the Run sink's storage routing policy."""

    def __init__(self, sink: _DaytonaRunSink) -> None:
        self._sink = sink

    def read_bytes(self, logical_path: str, *, max_bytes: int | None = None) -> bytes:
        return self._sink._sync_target(logical_path).read_bytes(logical_path, max_bytes=max_bytes)

    def write_bytes(self, logical_path: str, data: bytes, *, max_bytes: int | None = None) -> None:
        self._sink._sync_target(logical_path).write_bytes(logical_path, data, max_bytes=max_bytes)

    def exists(self, logical_path: str) -> bool:
        return self._sink._sync_target(logical_path).exists(logical_path)

    def remove(self, logical_path: str) -> None:
        self._sink._sync_target(logical_path).remove(logical_path)


@dataclass(slots=True)
class _DaytonaEnvironmentProvider:
    runtime: DaytonaRuntime
    settings: Settings
    volume_paths: VolumePaths
    sandbox_spec: DaytonaSandboxSpec
    dispatcher: SyncBridgeDispatcher
    workspace_gateway: Any
    volume_gateway: Any

    async def wait_for_session_idle(
        self,
        workspace_id: UUID,
        session_id: UUID,
        *,
        deadline: float,
    ) -> None:
        """Wait for a prepared Turn before retiring its shared Session root."""
        await self.runtime.wait_for_session_idle(workspace_id, session_id, deadline=deadline)

    def _mark_provider_root_tainted(self, key: tuple[UUID, UUID]) -> None:
        """Require a fresh provider root on the next acquisition for ``key``."""
        self.runtime.mark_root_tainted(*key)

    def _taint_resident_runtime(self, run: ClaimedRun) -> None:
        """Fence a resident runtime when provider setup proves its root unhealthy."""
        self._mark_provider_root_tainted((run.access.workspace_id, run.session_id))

    @staticmethod
    def _context_key(
        run: ClaimedRun,
    ) -> tuple[tuple[str, ...], tuple[tuple[str, str], ...], str | None]:
        """Return selectors that identify the immutable manifest bound to a root."""
        attachment_ids = tuple(str(attachment_id) for attachment_id in run.input.attachment_ids)
        return (
            attachment_ids,
            tuple((str(selection.id), str(selection.expected_version)) for selection in run.input.skill_selections),
            str(run.run_id) if attachment_ids else None,
        )

    async def acquire(self, run: ClaimedRun, *, deadline: float) -> RunEnvironment:
        """Acquire resources; DaytonaRuntime owns provider-operation lifetime."""
        key = (run.access.workspace_id, run.session_id)
        release_invocation: Callable[[], None] | None = None
        owner: InterpreterLease | None = None
        try:
            try:
                release_invocation = await self.runtime.begin_root_invocation(*key, deadline=deadline)
            except TimeoutError:
                raise RunPreparationTimeoutError("Turn preparation timed out") from None
            paths = self.volume_paths
            async with self.workspace_gateway.open_sandbox(
                run.access.workspace_id, purpose="session-volume-layout"
            ) as io_sandbox:
                await ensure_volume_layout(
                    io_sandbox,
                    paths,
                    session_id=run.session_id,
                    run_id=run.run_id,
                )
            try:
                owner = await self.runtime.acquire_root_session(
                    RootSessionSpec(
                        workspace_id=key[0],
                        session_id=key[1],
                        user_id=run.access.user_id,
                        run_id=run.run_id,
                        context_fingerprint=self._context_key(run),
                        deadline=deadline,
                    )
                )
            except DaytonaAdmissionTimeoutError as exc:
                raise RunPreparationUnavailableError("Turn environment is unavailable") from exc
            except (DaytonaLeaseAcquisitionTimeoutError, TimeoutError) as exc:
                raise RunPreparationTimeoutError("Turn preparation timed out") from exc
            assert owner is not None
            lease = owner
            self.runtime.track_sandbox(lease.sandbox_id)
            sandbox = lease.sandbox
            if sandbox is None:
                raise RuntimeError("acquired Sandbox is unavailable")

            dispatcher = self.dispatcher
            host_io = DaytonaHostIO(
                run.access.workspace_id,
                volume_gateway=self.volume_gateway,
                workspace_gateway=self.workspace_gateway,
                dispatcher=dispatcher,
                volume_root=str(paths.mount_path),
                max_file_bytes=self.settings.max_upload_bytes,
            )
            sink = _DaytonaRunSink(
                sandbox,
                dispatcher=dispatcher,
                paths=paths,
                host_io=host_io,
                run_id=run.run_id,
            )
            memory_store = host_io.memory_store
            session_workspace = DaytonaSandboxWorkspaceStorage(
                sink.sandbox,
                volume_root="/workspace",
                root="/workspace",
                max_file_bytes=self.settings.max_upload_bytes,
                allow_volume_root=True,
            )
            project_workspace = host_io.workspace_storage(str(paths.projects_root()))

            async def release_preparation() -> None:
                cleanup_run_scratch = getattr(lease.interpreter, "cleanup_run_scratch", None)
                if callable(cleanup_run_scratch):
                    await asyncio.to_thread(cleanup_run_scratch)
                if release_invocation is not None:
                    release_invocation()

            child_runtime_factory = self.runtime.build_child_factory(
                volume_id=lease.volume_id,
                mount_path=self.runtime.volume_config.mount_path,
                workspace_id=run.access.workspace_id,
                session_id=run.session_id,
                run_id=run.run_id,
                deadline=deadline,
                execution_timeout_s=self.settings.rlm_execution_timeout_s,
                execution_output_cap=self.settings.rlm_max_execution_output_chars,
                is_authorized=lambda: not run.authority.revoked,
                semantic_child_available=bool(self.settings.daytona_child_snapshot),
            )

            async def write_child_result(call_index: int, relative_path: str, data: bytes) -> str:
                if run.authority.revoked or not isinstance(call_index, int) or call_index < 1:
                    raise ValueError("child result is no longer authorized")
                path = PurePosixPath(relative_path)
                if (
                    not relative_path
                    or path.is_absolute()
                    or ".." in path.parts
                    or "\\" in relative_path
                    or ":" in relative_path
                ):
                    raise ValueError("child result path is invalid")
                if sink.scratch_root is None:
                    raise RuntimeError("parent Run scratch is unavailable")
                destination = str(PurePosixPath(sink.scratch_root) / "children" / str(call_index) / path)
                await sink.write_private(destination, data)
                if run.authority.revoked:
                    raise ValueError("child result is no longer authorized")
                persisted = await sink.read(destination, max_bytes=len(data))
                if sha256(persisted).digest() != sha256(data).digest():
                    raise ValueError("child result persistence checksum mismatch")
                return destination

            image_identity = environment_manifest(self.sandbox_spec).digest
            bind_run_scratch = getattr(lease.interpreter, "bind_run_scratch", None)
            if callable(bind_run_scratch):
                bind_run_scratch(run.run_id)
            return RunEnvironment(
                interpreter=lease.interpreter,
                attachment_sink=sink,
                artifact_sink=sink,
                release=release_preparation,
                result_snapshot_sink=sink,
                child_runtime_factory=child_runtime_factory,
                child_result_writer=write_child_result,
                context_mount_path=sink.scratch_root,
                workspace_memory_store=memory_store,
                volume_fs=sink.volume_fs,
                session_workspace=session_workspace,
                project_workspace=project_workspace,
                resident_release=None,
                release_is_resident=False,
                history_format="sandbox",
                mark_tainted=lambda k=key: self._mark_provider_root_tainted(k),
                async_bridge=self.dispatcher,
                image_identity=image_identity,
            )
        except BaseException:
            self._taint_resident_runtime(run)
            if owner is not None:
                try:
                    await asyncio.shield(self.runtime.close_root_session(*key, deadline=deadline))
                except BaseException as exc:
                    logger.warning(
                        "Daytona root cleanup failed on error",
                        extra={"session_id": str(key[1]), "error_type": type(exc).__name__},
                    )
            if release_invocation is not None:
                release_invocation()
            raise


__all__ = [
    "_DaytonaEnvironmentProvider",
    "_DaytonaRunSink",
]
