"""Deterministic host-backed workspace adapters for tests only."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from pathlib import Path, PurePosixPath
from uuid import UUID

from fleet_rlm.paths import UnsafePathError, VolumePaths, validate_mount_path
from fleet_rlm.workspace.storage import (
    MAX_WORKSPACE_FILE_BYTES,
    AsyncStorageSession,
    AsyncVolumeStorage,
    AsyncWorkspaceStorage,
    VolumeFile,
    WorkspaceStorage,
)


class HostVolumeMirror:
    """Map trusted logical mount paths into one isolated host directory."""

    def __init__(self, host_root: Path | str, *, volume_paths: VolumePaths | None = None) -> None:
        self._paths = volume_paths or VolumePaths.from_mount()
        self._root = Path(host_root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def host_root(self) -> Path:
        return self._root

    @property
    def volume_paths(self) -> VolumePaths:
        return self._paths

    def host_path_for(self, logical_path: str) -> Path:
        mount = validate_mount_path(str(self._paths.mount_path))
        path = PurePosixPath(logical_path)
        if "\\" in logical_path or "\x00" in logical_path or ".." in path.parts or str(path) != logical_path:
            raise UnsafePathError("logical path escapes volume mount")
        try:
            relative = path.relative_to(mount)
        except ValueError as exc:
            raise UnsafePathError("logical path escapes volume mount") from exc
        if not relative.parts:
            return self._root
        return self._root.joinpath(*relative.parts)

    def write_bytes(self, logical_path: str, data: bytes, *, max_bytes: int | None = None) -> None:
        del max_bytes
        destination = self.host_path_for(logical_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)

    def read_bytes(self, logical_path: str, *, max_bytes: int | None = None) -> bytes:
        destination = self.host_path_for(logical_path)
        if not destination.is_file():
            raise FileNotFoundError(logical_path)
        data = destination.read_bytes()
        if max_bytes is not None and len(data) > max_bytes:
            data = data[:max_bytes]
        return data

    def exists(self, logical_path: str) -> bool:
        try:
            destination = self.host_path_for(logical_path)
            return destination.is_file()
        except Exception:
            return False

    def remove_bytes(self, logical_path: str) -> None:
        try:
            destination = self.host_path_for(logical_path)
            if destination.is_file():
                destination.unlink()
        except (FileNotFoundError, OSError):
            pass

    def remove(self, logical_path: str) -> None:
        self.remove_bytes(logical_path)

    def list_files(self, logical_root: str, *, max_depth: int = 10, max_files: int = 1000) -> tuple[VolumeFile, ...]:
        root = self.host_path_for(logical_root)
        if not root.exists() or not root.is_dir():
            return ()
        results: list[VolumeFile] = []
        base_depth = len(root.parts)
        for candidate in sorted(root.rglob("*")):
            if not candidate.is_file():
                continue
            if len(candidate.parts) - base_depth > max_depth:
                continue
            relative = candidate.relative_to(self._root)
            results.append(VolumeFile(str(self._paths.mount_path / relative), candidate.stat().st_mtime))
            if len(results) >= max_files:
                break
        return tuple(results)


class _HostWorkspaceVolumeSession:
    """Async compatibility view over one host Volume mirror."""

    def __init__(self, mirror: HostVolumeMirror, *, max_bytes: int = MAX_WORKSPACE_FILE_BYTES) -> None:
        self._mirror = mirror
        self._max_bytes = max_bytes

    async def write_bytes(self, logical_path: str, data: bytes, *, max_bytes: int | None = None) -> None:
        await asyncio.to_thread(self._mirror.write_bytes, logical_path, data, max_bytes=max_bytes or self._max_bytes)

    async def read_bytes(self, logical_path: str, *, max_bytes: int | None = None) -> bytes:
        return await asyncio.to_thread(self._mirror.read_bytes, logical_path, max_bytes=max_bytes or self._max_bytes)

    async def exists(self, logical_path: str) -> bool:
        return await asyncio.to_thread(self._mirror.exists, logical_path)

    async def remove_bytes(self, logical_path: str) -> None:
        await asyncio.to_thread(self._mirror.remove_bytes, logical_path)

    async def list_files(
        self,
        logical_root: str,
        *,
        max_depth: int = 10,
        max_files: int = 1000,
    ) -> tuple[VolumeFile, ...]:
        return await asyncio.to_thread(self._mirror.list_files, logical_root, max_depth=max_depth, max_files=max_files)


class OfflineHostVolumeGateway:
    """Adapt one isolated host mirror to the async Workspace Volume port."""

    def __init__(self, mirror: HostVolumeMirror | Path | str, *, max_bytes: int = MAX_WORKSPACE_FILE_BYTES) -> None:
        self._mirror = mirror if isinstance(mirror, HostVolumeMirror) else HostVolumeMirror(mirror)
        self._max_bytes = max_bytes

    @contextlib.asynccontextmanager
    async def open_workspace(
        self, workspace_id: UUID, *, purpose: str | None = None
    ) -> AsyncIterator[AsyncVolumeStorage]:
        del workspace_id, purpose
        yield _HostWorkspaceVolumeSession(self._mirror, max_bytes=self._max_bytes)

    async def write_bytes(
        self,
        workspace_id: UUID,
        logical_path: str,
        data: bytes,
        *,
        max_bytes: int | None = None,
    ) -> None:
        async with self.open_workspace(workspace_id) as volume:
            await volume.write_bytes(logical_path, data, max_bytes=max_bytes)

    async def read_bytes(self, workspace_id: UUID, logical_path: str, *, max_bytes: int | None = None) -> bytes:
        async with self.open_workspace(workspace_id) as volume:
            return await volume.read_bytes(logical_path, max_bytes=max_bytes)

    async def remove_bytes(self, workspace_id: UUID, logical_path: str) -> None:
        async with self.open_workspace(workspace_id) as volume:
            await volume.remove_bytes(logical_path)

    async def list_files(
        self,
        workspace_id: UUID,
        logical_root: str,
        *,
        max_depth: int = 10,
        max_files: int = 1000,
    ) -> tuple[VolumeFile, ...]:
        async with self.open_workspace(workspace_id) as volume:
            return await volume.list_files(logical_root, max_depth=max_depth, max_files=max_files)


class _InMemoryDaytonaFilesystem:
    """Small Daytona SDK filesystem fake backed by one in-memory volume."""

    def __init__(self, mount_path: str) -> None:
        self._roots = (PurePosixPath(mount_path), PurePosixPath("/tmp/fleet"))
        self.files: dict[str, bytes] = {}
        self.directories = {str(root) for root in self._roots}

    def _path(self, value: str) -> PurePosixPath:
        path = PurePosixPath(value)
        if not path.is_absolute() or ".." in path.parts or "\\" in value or "\x00" in value:
            raise ValueError("filesystem path is invalid")
        if not any(path == root or root in path.parents for root in self._roots):
            raise ValueError("filesystem path is outside the test mounts")
        return path

    async def get_file_info(self, value: str) -> dict[str, object]:
        path = self._path(value)
        key = str(path)
        if key in self.directories:
            return {"type": "directory", "is_dir": True, "size": 0, "mod_time": "0"}
        if key in self.files:
            return {"type": "file", "is_dir": False, "size": len(self.files[key]), "mod_time": "0"}
        raise FileNotFoundError(value)

    async def create_folder(self, value: str, mode: str | None = None) -> None:
        del mode
        path = self._path(value)
        self.directories.add(str(path))

    async def download_file(self, value: str) -> bytes:
        try:
            return self.files[str(self._path(value))]
        except KeyError as exc:
            raise FileNotFoundError(value) from exc

    async def upload_file(self, data: bytes, value: str) -> None:
        path = self._path(value)
        self.directories.update(
            str(parent)
            for parent in path.parents
            if any(parent == root or root in parent.parents for root in self._roots)
        )
        self.files[str(path)] = bytes(data)

    async def delete_file(self, value: str) -> None:
        self.files.pop(str(self._path(value)), None)

    async def list_files(self, value: str, *, depth: int = 1) -> list[dict[str, object]]:
        root = self._path(value)
        if str(root) not in self.directories:
            raise FileNotFoundError(value)
        results: list[dict[str, object]] = []
        for candidate in sorted((*self.directories, *self.files)):
            path = PurePosixPath(candidate)
            try:
                relative = path.relative_to(root)
            except ValueError:
                continue
            if not relative.parts or len(relative.parts) > depth:
                continue
            is_dir = candidate in self.directories
            results.append(
                {
                    "path": candidate,
                    "is_dir": is_dir,
                    "size": 0 if is_dir else len(self.files[candidate]),
                    "mod_time": "0",
                    "is_symlink": False,
                }
            )
        return results


class InMemoryDaytonaWorkspaceGateway:
    """Test-only mounted-Sandbox gateway for exercising the production Daytona ports."""

    def __init__(self, mount_path: str) -> None:
        from types import SimpleNamespace

        self.fs = _InMemoryDaytonaFilesystem(mount_path)
        self.sandbox = SimpleNamespace(fs=self.fs, process=SimpleNamespace())

    @contextlib.asynccontextmanager
    async def open_sandbox(self, workspace_id: UUID, *, purpose: str) -> AsyncIterator[object]:
        del workspace_id, purpose
        yield self.sandbox


def daytona_host_io_for_test_sandbox(
    sandbox: object,
    *,
    workspace_id: UUID,
    dispatcher: object,
    volume_root: str,
    max_file_bytes: int,
) -> object:
    """Bind a fake sandbox to DaytonaHostIO for focused run-sink tests."""
    from fleet_rlm.workspace.host_io import DaytonaHostIO
    from fleet_rlm.workspace.mounted_gateway import DaytonaWorkspaceVolumeGateway

    class Gateway:
        @contextlib.asynccontextmanager
        async def open_sandbox(self, _workspace_id: UUID, *, purpose: str) -> AsyncIterator[object]:
            del purpose
            yield sandbox

    workspace_gateway = Gateway()
    volume_gateway = DaytonaWorkspaceVolumeGateway(workspace_gateway, mount_path=volume_root)
    return DaytonaHostIO(
        workspace_id,
        volume_gateway=volume_gateway,
        workspace_gateway=workspace_gateway,
        dispatcher=dispatcher,
        volume_root=volume_root,
        max_file_bytes=max_file_bytes,
    )


class HostWorkspaceAccessGateway:
    """Credential-free public-files gateway over a local isolated root."""

    def __init__(self, root: Path | str, *, max_file_bytes: int = MAX_WORKSPACE_FILE_BYTES) -> None:
        self._root = Path(root).resolve()
        self._max_file_bytes = max_file_bytes

    @contextlib.asynccontextmanager
    async def open_workspace(self, workspace_id: UUID, *, purpose: str = "") -> AsyncIterator[AsyncStorageSession]:
        del purpose
        ws_root = self._root / "workspaces" / str(workspace_id) / "files"
        ws_root.mkdir(parents=True, exist_ok=True)
        yield AsyncWorkspaceStorage(
            WorkspaceStorage(
                root=ws_root,
                max_file_bytes=self._max_file_bytes,
                allow_volume_root=True,
                include_checksum_by_default=True,
            )
        )


__all__ = [
    "HostVolumeMirror",
    "HostWorkspaceAccessGateway",
    "InMemoryDaytonaWorkspaceGateway",
    "OfflineHostVolumeGateway",
    "daytona_host_io_for_test_sandbox",
]
