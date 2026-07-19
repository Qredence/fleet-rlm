"""Workspace-scoped durable byte I/O through mounted Daytona Volumes."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path, PurePosixPath
from typing import Any, Protocol, TypeVar
from uuid import UUID, uuid4

from daytona import AsyncDaytona, CreateSandboxFromSnapshotParams, DaytonaConfig, VolumeMount

from fleet_rlm.daytona.errors import is_sandbox_not_found, map_provider_error
from fleet_rlm.daytona.paths import UnsafePathError, VolumePaths, validate_mount_path
from fleet_rlm.daytona.sandbox_spec import DaytonaSandboxSpec
from fleet_rlm.daytona.volume_fs import HostVolumeMirror, VolumeFile
from fleet_rlm.daytona.volumes import require_scoped_volume_subpath, workspace_volume_subpath

T = TypeVar("T")
logger = logging.getLogger(__name__)


class WorkspaceVolumeGateway(Protocol):
    """Async non-Turn byte I/O at the authenticated Workspace Volume Scope."""

    async def write_bytes(self, workspace_id: UUID, logical_path: str, data: bytes) -> None: ...

    async def read_bytes(self, workspace_id: UUID, logical_path: str) -> bytes: ...

    async def remove_bytes(self, workspace_id: UUID, logical_path: str) -> None: ...

    async def list_files(
        self,
        workspace_id: UUID,
        logical_root: str,
        *,
        max_depth: int,
        max_files: int,
    ) -> tuple[VolumeFile, ...]: ...


class DaytonaWorkspaceVolumeGateway:
    """Use short-lived I/O Sandboxes because Daytona Volumes have no direct file API."""

    def __init__(
        self,
        client: Any,
        *,
        volume_name: str,
        mount_path: str,
        sandbox_spec: DaytonaSandboxSpec,
    ) -> None:
        self._client = client
        self._volume_name = volume_name
        self._mount_path = mount_path
        self._sandbox_spec = sandbox_spec

    async def write_bytes(self, workspace_id: UUID, logical_path: str, data: bytes) -> None:
        path = self._validate_logical_path(logical_path)

        async def _write(sandbox: Any) -> None:
            await self._ensure_parent_folders(sandbox, path)
            await sandbox.fs.upload_file(data, path)

        try:
            await self._with_io_sandbox(workspace_id, _write)
        except Exception as exc:
            raise map_provider_error(exc) from exc

    async def read_bytes(self, workspace_id: UUID, logical_path: str) -> bytes:
        path = self._validate_logical_path(logical_path)

        async def _read(sandbox: Any) -> bytes:
            raw = await sandbox.fs.download_file(path)
            return raw if isinstance(raw, bytes) else bytes(raw)

        try:
            return await self._with_io_sandbox(workspace_id, _read)
        except Exception as exc:
            raise map_provider_error(exc) from exc

    async def remove_bytes(self, workspace_id: UUID, logical_path: str) -> None:
        path = self._validate_logical_path(logical_path)

        async def _remove(sandbox: Any) -> None:
            await sandbox.fs.delete_file(path)

        try:
            await self._with_io_sandbox(workspace_id, _remove)
        except Exception as exc:
            if is_sandbox_not_found(exc):
                return
            raise map_provider_error(exc) from exc

    async def list_files(
        self,
        workspace_id: UUID,
        logical_root: str,
        *,
        max_depth: int,
        max_files: int,
    ) -> tuple[VolumeFile, ...]:
        path = self._validate_logical_path(logical_root)
        if max_depth <= 0:
            raise ValueError("max_depth must be positive")
        if max_files <= 0:
            raise ValueError("max_files must be positive")

        async def _list(sandbox: Any) -> tuple[VolumeFile, ...]:
            entries = await sandbox.fs.list_files(path, depth=max_depth)
            result: list[VolumeFile] = []
            root = PurePosixPath(path)
            mount = validate_mount_path(self._mount_path)
            for entry in entries:
                entry_path = getattr(entry, "path", None)
                if not isinstance(entry_path, str):
                    continue
                candidate = PurePosixPath(entry_path)
                try:
                    candidate.relative_to(root)
                    candidate.relative_to(mount)
                except ValueError:
                    continue
                if bool(getattr(entry, "is_dir", False)):
                    continue
                modified_at = getattr(entry, "mod_time", None)
                if hasattr(modified_at, "timestamp"):
                    modified_at = modified_at.timestamp()
                if not isinstance(modified_at, (int, float)):
                    continue
                result.append(VolumeFile(str(candidate), float(modified_at)))
                if len(result) >= max_files:
                    break
            return tuple(result)

        try:
            return await self._with_io_sandbox(workspace_id, _list)
        except Exception as exc:
            raise map_provider_error(exc) from exc

    def _validate_logical_path(self, logical_path: str) -> str:
        mount = validate_mount_path(self._mount_path)
        path = PurePosixPath(logical_path)
        try:
            relative = path.relative_to(mount)
        except ValueError as exc:
            raise UnsafePathError("logical path escapes Workspace Volume Scope") from exc
        if not relative.parts or ".." in relative.parts:
            raise UnsafePathError("logical path escapes Workspace Volume Scope")
        return str(path)

    async def _ensure_parent_folders(self, sandbox: Any, logical_path: str) -> None:
        mount = validate_mount_path(self._mount_path)
        parent = PurePosixPath(logical_path).parent
        relative = parent.relative_to(mount)
        current = mount
        for part in relative.parts:
            current /= part
            folder = str(current)
            try:
                await sandbox.fs.get_file_info(folder)
            except Exception as exc:
                if not is_sandbox_not_found(exc):
                    raise
                try:
                    await sandbox.fs.create_folder(folder, "755")
                except Exception:
                    # A concurrent UUID-unique writer may have created a shared
                    # ancestor after the lookup; verify it exists before proceeding.
                    await sandbox.fs.get_file_info(folder)

    async def close(self) -> None:
        close = getattr(self._client, "close", None)
        if callable(close):
            await close()

    async def _with_io_sandbox(
        self,
        workspace_id: UUID,
        operation: Callable[[Any], Awaitable[T]],
    ) -> T:
        subpath = require_scoped_volume_subpath(
            workspace_volume_subpath(workspace_id),
            workspace_id=workspace_id,
        )
        volume = await self._client.volume.get(self._volume_name, create=True)
        params = CreateSandboxFromSnapshotParams(
            name=f"fleet-clean-io-{workspace_id.hex[:8]}-{uuid4().hex[:8]}",
            language="python",
            os_user="daytona",
            snapshot=self._sandbox_spec.snapshot,
            labels={
                "fleet-package": "fleet_rlm",
                "purpose": "workspace-volume-io",
                "workspace-id": str(workspace_id),
            },
            volumes=[
                VolumeMount(
                    volume_id=str(volume.id),
                    mount_path=self._mount_path,
                    subpath=subpath,
                )
            ],
            ephemeral=True,
        )
        sandbox = await self._client.create(params)
        try:
            return await operation(sandbox)
        finally:
            try:
                await self._client.delete(sandbox)
            except Exception as exc:  # noqa: BLE001 - auto-delete is the cleanup backstop
                logger.warning(
                    "workspace volume I/O Sandbox deletion failed",
                    extra={
                        "workspace_id": str(workspace_id),
                        "sandbox_id": str(getattr(sandbox, "id", "unknown")),
                        "error_type": type(exc).__name__,
                    },
                )


class HostWorkspaceVolumeGateway:
    """Private test adapter with one isolated host root per Workspace."""

    def __init__(self, root: Path | str, *, volume_paths: VolumePaths) -> None:
        self._root = Path(root)
        self._paths = volume_paths

    def _mirror(self, workspace_id: UUID) -> HostVolumeMirror:
        return HostVolumeMirror(
            self._root / "workspaces" / str(workspace_id),
            volume_paths=self._paths,
        )

    async def write_bytes(self, workspace_id: UUID, logical_path: str, data: bytes) -> None:
        self._mirror(workspace_id).write_bytes(logical_path, data)

    async def read_bytes(self, workspace_id: UUID, logical_path: str) -> bytes:
        return self._mirror(workspace_id).read_bytes(logical_path)

    async def remove_bytes(self, workspace_id: UUID, logical_path: str) -> None:
        self._mirror(workspace_id).remove(logical_path)

    async def list_files(
        self,
        workspace_id: UUID,
        logical_root: str,
        *,
        max_depth: int,
        max_files: int,
    ) -> tuple[VolumeFile, ...]:
        return self._mirror(workspace_id).list_files(
            logical_root,
            max_depth=max_depth,
            max_files=max_files,
        )


class OfflineHostVolumeGateway:
    """Adapt the shared offline Turn mirror to the async durable-store port.

    SQL metadata performs Workspace authorization before this adapter is called.
    Production continues to use physically scoped Daytona Volume subpaths.
    """

    def __init__(self, mirror: HostVolumeMirror) -> None:
        self._mirror = mirror

    async def write_bytes(self, workspace_id: UUID, logical_path: str, data: bytes) -> None:
        del workspace_id
        self._mirror.write_bytes(logical_path, data)

    async def read_bytes(self, workspace_id: UUID, logical_path: str) -> bytes:
        del workspace_id
        return self._mirror.read_bytes(logical_path)

    async def remove_bytes(self, workspace_id: UUID, logical_path: str) -> None:
        del workspace_id
        self._mirror.remove(logical_path)

    async def list_files(
        self,
        workspace_id: UUID,
        logical_root: str,
        *,
        max_depth: int,
        max_files: int,
    ) -> tuple[VolumeFile, ...]:
        del workspace_id
        return self._mirror.list_files(logical_root, max_depth=max_depth, max_files=max_files)


def create_daytona_workspace_volume_gateway(
    *,
    api_key: str,
    volume_name: str,
    mount_path: str,
    sandbox_spec: DaytonaSandboxSpec,
) -> DaytonaWorkspaceVolumeGateway:
    """Construct the SDK client behind the package import boundary."""
    return DaytonaWorkspaceVolumeGateway(
        AsyncDaytona(DaytonaConfig(api_key=api_key)),
        volume_name=volume_name,
        mount_path=mount_path,
        sandbox_spec=sandbox_spec,
    )


__all__ = [
    "DaytonaWorkspaceVolumeGateway",
    "OfflineHostVolumeGateway",
    "HostWorkspaceVolumeGateway",
    "WorkspaceVolumeGateway",
    "create_daytona_workspace_volume_gateway",
]
