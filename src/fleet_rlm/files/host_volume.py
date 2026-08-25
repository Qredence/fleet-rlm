"""Host-backed mounted Workspace adapters for deterministic local tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path, PurePosixPath
from uuid import UUID

from fleet_rlm.files.volume_paths import (
    UnsafePathError,
    VolumePaths,
    validate_mount_path,
)
from fleet_rlm.files.volume_storage import (
    VolumeFile,
    WorkspaceVolumeSession,
)


class HostVolumeMirror:
    """Map logical mounted paths into one isolated host directory."""

    def __init__(
        self,
        host_root: Path | str,
        *,
        volume_paths: VolumePaths | None = None,
    ) -> None:
        self._paths = volume_paths or VolumePaths.from_mount()
        self._root = Path(host_root)
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def host_root(self) -> Path:
        return self._root

    @property
    def volume_paths(self) -> VolumePaths:
        return self._paths

    def host_path_for(self, logical_path: str) -> Path:
        mount = self._paths.mount_path
        validate_mount_path(str(mount))
        logical = PurePosixPath(logical_path)
        try:
            relative = logical.relative_to(mount)
        except ValueError as exc:
            raise UnsafePathError("logical path escapes volume mount") from exc
        if ".." in relative.parts:
            raise UnsafePathError("logical path escapes volume mount")
        return self._root.joinpath(*relative.parts)

    def write_bytes(self, logical_path: str, data: bytes) -> None:
        destination = self.host_path_for(logical_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)

    def read_bytes(self, logical_path: str, *, use_cache: bool = True) -> bytes:
        del use_cache
        destination = self.host_path_for(logical_path)
        if not destination.is_file():
            raise FileNotFoundError(f"volume path not found: {logical_path}")
        return destination.read_bytes()

    def exists(self, logical_path: str) -> bool:
        return self.host_path_for(logical_path).is_file()

    def remove(self, logical_path: str) -> None:
        self.host_path_for(logical_path).unlink(missing_ok=True)

    def list_files(
        self,
        logical_root: str,
        *,
        max_depth: int,
        max_files: int,
    ) -> tuple[VolumeFile, ...]:
        if max_depth <= 0:
            raise ValueError("max_depth must be positive")
        if max_files <= 0:
            raise ValueError("max_files must be positive")
        root = self.host_path_for(logical_root)
        if not root.is_dir():
            return ()
        results: list[VolumeFile] = []
        base_depth = len(root.parts)
        for candidate in sorted(root.rglob("*")):
            if len(results) >= max_files:
                break
            if candidate.is_symlink() or not candidate.is_file():
                continue
            if len(candidate.parts) - base_depth > max_depth:
                continue
            try:
                candidate.relative_to(self._root)
            except ValueError as exc:
                raise UnsafePathError("enumerated path escapes volume root") from exc
            logical = self._paths.mount_path / candidate.relative_to(self._root)
            results.append(VolumeFile(str(logical), candidate.stat().st_mtime))
        return tuple(results)


class _HostWorkspaceVolumeSession:
    def __init__(self, mirror: HostVolumeMirror) -> None:
        self._mirror = mirror

    async def write_bytes(self, logical_path: str, data: bytes) -> None:
        self._mirror.write_bytes(logical_path, data)

    async def read_bytes(self, logical_path: str) -> bytes:
        return self._mirror.read_bytes(logical_path)

    async def remove_bytes(self, logical_path: str) -> None:
        self._mirror.remove(logical_path)

    async def list_files(
        self,
        logical_root: str,
        *,
        max_depth: int,
        max_files: int,
    ) -> tuple[VolumeFile, ...]:
        return self._mirror.list_files(
            logical_root,
            max_depth=max_depth,
            max_files=max_files,
        )


class OfflineHostVolumeGateway:
    """Adapt one shared local mirror to the async durable-store port."""

    def __init__(self, mirror: HostVolumeMirror) -> None:
        self._mirror = mirror

    @asynccontextmanager
    async def open_workspace(self, workspace_id: UUID) -> AsyncIterator[WorkspaceVolumeSession]:
        del workspace_id
        yield _HostWorkspaceVolumeSession(self._mirror)

    async def write_bytes(self, workspace_id: UUID, logical_path: str, data: bytes) -> None:
        async with self.open_workspace(workspace_id) as volume:
            await volume.write_bytes(logical_path, data)

    async def read_bytes(self, workspace_id: UUID, logical_path: str) -> bytes:
        async with self.open_workspace(workspace_id) as volume:
            return await volume.read_bytes(logical_path)

    async def remove_bytes(self, workspace_id: UUID, logical_path: str) -> None:
        async with self.open_workspace(workspace_id) as volume:
            await volume.remove_bytes(logical_path)

    async def list_files(
        self,
        workspace_id: UUID,
        logical_root: str,
        *,
        max_depth: int,
        max_files: int,
    ) -> tuple[VolumeFile, ...]:
        async with self.open_workspace(workspace_id) as volume:
            return await volume.list_files(
                logical_root,
                max_depth=max_depth,
                max_files=max_files,
            )


__all__ = [
    "HostVolumeMirror",
    "OfflineHostVolumeGateway",
]
