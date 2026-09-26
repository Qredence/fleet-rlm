"""Short-lived host I/O operations over app-authorized Daytona gateways."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Iterable, Mapping
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import UUID

from fleet_rlm.daytona.interpreter import SyncBridgeDispatcher, sync_sandbox, tombstone_sync_sandbox
from fleet_rlm.paths import UnsafePathError, VolumePaths, validate_mount_path
from fleet_rlm.workspace.mounted_gateway import DaytonaWorkspaceGateway, DaytonaWorkspaceVolumeGateway
from fleet_rlm.workspace.storage import (
    AsyncDaytonaVolumeFS,
    DaytonaSandboxVolumeFs,
    DaytonaSandboxWorkspaceStorage,
    WorkspaceMemoryStorage,
)


async def _ensure_sandbox_directories(filesystem: Any, directories: Iterable[str]) -> None:
    for directory in sorted(set(directories), key=lambda value: (value.count("/"), value)):
        create_folder = getattr(filesystem, "create_folder", None)
        if not callable(create_folder):
            raise RuntimeError("Sandbox filesystem cannot create private Run directories")
        try:
            created = create_folder(directory, "700")
            if inspect.isawaitable(created):
                await created
        except Exception as create_error:
            try:
                info = filesystem.get_file_info(directory)
                if inspect.isawaitable(info):
                    info = await info
            except Exception:
                raise create_error from None
            is_directory = info.get("is_dir", False) if isinstance(info, Mapping) else getattr(info, "is_dir", False)
            if not is_directory:
                raise create_error from None


class _VolumeBlobFs:
    """Synchronous VolumeBlobFs bound to one app-authorized Workspace."""

    def __init__(
        self,
        workspace_id: UUID,
        gateway: DaytonaWorkspaceVolumeGateway,
        dispatcher: SyncBridgeDispatcher,
        *,
        volume_root: str,
    ) -> None:
        self._workspace_id = workspace_id
        self._gateway = gateway
        self._dispatcher = dispatcher
        self._volume_root = validate_mount_path(volume_root)

    def _path(self, logical_path: str) -> str:
        path = PurePosixPath(logical_path)
        try:
            relative = path.relative_to(self._volume_root)
        except ValueError as exc:
            raise UnsafePathError("logical path escapes Workspace Volume Scope") from exc
        if not relative.parts or ".." in path.parts or "\\" in logical_path or "\x00" in logical_path:
            raise UnsafePathError("logical path escapes Workspace Volume Scope")
        return str(path)

    def write_bytes(self, logical_path: str, data: bytes, *, max_bytes: int | None = None) -> None:
        self._dispatcher.run(
            self._gateway.write_bytes(self._workspace_id, self._path(logical_path), data, max_bytes=max_bytes)
        )

    def read_bytes(self, logical_path: str, *, max_bytes: int | None = None) -> bytes:
        return self._dispatcher.run(
            self._gateway.read_bytes(self._workspace_id, self._path(logical_path), max_bytes=max_bytes)
        )

    def exists(self, logical_path: str) -> bool:
        try:
            self.read_bytes(logical_path)
        except (FileNotFoundError, KeyError):
            return False
        return True

    def remove(self, logical_path: str) -> None:
        self.remove_bytes(logical_path)

    def remove_bytes(self, logical_path: str) -> None:
        self._dispatcher.run(self._gateway.remove_bytes(self._workspace_id, self._path(logical_path)))

    async def aread_bytes(self, logical_path: str, *, max_bytes: int | None = None) -> bytes:
        return await self._gateway.read_bytes(self._workspace_id, self._path(logical_path), max_bytes=max_bytes)

    async def awrite_bytes(self, logical_path: str, data: bytes, *, max_bytes: int | None = None) -> None:
        await self._gateway.write_bytes(self._workspace_id, self._path(logical_path), data, max_bytes=max_bytes)

    async def aremove_bytes(self, logical_path: str) -> None:
        await self._gateway.remove_bytes(self._workspace_id, self._path(logical_path))


class _WorkspaceMemoryStore:
    """Sync WorkspaceMemoryStore proxy; each call owns one short I/O lease."""

    def __init__(
        self,
        workspace_id: UUID,
        gateway: DaytonaWorkspaceGateway,
        dispatcher: SyncBridgeDispatcher,
        *,
        volume_root: str,
        max_file_bytes: int,
    ) -> None:
        self._workspace_id = workspace_id
        self._gateway = gateway
        self._dispatcher = dispatcher
        self._volume_root = volume_root
        self._max_file_bytes = max_file_bytes

    def _call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        async def operation() -> Any:
            async with self._gateway.open_sandbox(self._workspace_id, purpose="workspace-memory-io") as sandbox:
                view = sync_sandbox(sandbox, asyncio.get_running_loop(), self._dispatcher)
                try:
                    storage = DaytonaSandboxWorkspaceStorage(
                        view,
                        volume_root=self._volume_root,
                        root=self._volume_root,
                        max_file_bytes=self._max_file_bytes,
                        allow_volume_root=True,
                    )
                    from fleet_rlm.workspace.memory import build_workspace_memory_store

                    store = build_workspace_memory_store(
                        WorkspaceMemoryStorage(storage), max_upload_bytes=self._max_file_bytes
                    )
                    return await asyncio.to_thread(getattr(store, method), *args, **kwargs)
                finally:
                    tombstone_sync_sandbox(view)

        return self._dispatcher.run(operation())

    def read_tail(self, *, byte_budget: int) -> Any:
        return self._call("read_tail", byte_budget=byte_budget)

    def append_record(self, record: str) -> Any:
        return self._call("append_record", record)

    def list_entries(self, *, after: str | None = None, limit: int = 10, category: str | None = None) -> Any:
        return self._call("list_entries", after=after, limit=limit, category=category)

    def edit_entry(self, memory_id: str, key_learning: str, *, category: str | None = None) -> str:
        return self._call("edit_entry", memory_id, key_learning, category=category)

    def delete_entry(self, memory_id: str) -> bool:
        return self._call("delete_entry", memory_id)

    def read_injection_digest(self, *, request: str = "") -> str:
        return self._call("read_injection_digest", request=request)

    def read_recent(self, *, limit: int = 50) -> Any:
        return self._call("read_recent", limit=limit)

    def search(self, query: str, *, category: str | None = None, limit: int = 8) -> Any:
        return self._call("search", query, category=category, limit=limit)


class _HostWorkspaceStorage:
    """One authorized host operation at a time under a fixed Volume root."""

    def __init__(
        self,
        workspace_id: UUID,
        gateway: DaytonaWorkspaceGateway,
        dispatcher: SyncBridgeDispatcher,
        *,
        volume_root: str,
        root: str,
        max_file_bytes: int,
    ) -> None:
        self._workspace_id = workspace_id
        self._gateway = gateway
        self._dispatcher = dispatcher
        self._volume_root = volume_root
        self._root = root
        self._max_file_bytes = max_file_bytes

    @property
    def root(self) -> Path:
        return Path(self._root)

    @property
    def last_warnings(self) -> tuple[Any, ...]:
        return ()

    def warnings(self) -> tuple[Any, ...]:
        return ()

    def _call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        async def operation() -> Any:
            async with self._gateway.open_sandbox(self._workspace_id, purpose="workspace-host-file-io") as sandbox:
                view = sync_sandbox(sandbox, asyncio.get_running_loop(), self._dispatcher)
                try:
                    storage = DaytonaSandboxWorkspaceStorage(
                        view,
                        volume_root=self._volume_root,
                        root=self._root,
                        max_file_bytes=self._max_file_bytes,
                    )
                    return await asyncio.to_thread(getattr(storage, method), *args, **kwargs)
                finally:
                    tombstone_sync_sandbox(view)

        return self._dispatcher.run(operation())

    def list_entries(self, path: str = ".", *, limit: int = 100, after: str | None = None) -> Any:
        return self._call("list_entries", path, limit=limit, after=after)

    def stat(self, path: str, *, include_checksum: bool | None = None) -> Any:
        return self._call("stat", path, include_checksum=include_checksum)

    def stat_path(self, path: str, *, include_checksum: bool | None = None) -> Any:
        return self._call("stat_path", path, include_checksum=include_checksum)

    def read_text(self, path: str, *, cursor: str | None = None, max_chars: int = 10_000) -> Any:
        return self._call("read_text", path, cursor=cursor, max_chars=max_chars)

    def read_text_page(
        self,
        path: str,
        *,
        cursor: str | None = None,
        max_chars: int = 64_000,
        max_bytes: int | None = None,
    ) -> Any:
        return self._call("read_text_page", path, cursor=cursor, max_chars=max_chars, max_bytes=max_bytes)

    def read_file_bytes(self, path: str, *, max_bytes: int) -> bytes:
        return self._call("read_file_bytes", path, max_bytes=max_bytes)

    def write_text(
        self,
        path: str,
        content: str,
        *,
        overwrite: bool = True,
        expected_sha256: str | None = None,
    ) -> Any:
        return self._call("write_text", path, content, overwrite=overwrite, expected_sha256=expected_sha256)

    def append_text(self, path: str, content: str, *, expected_sha256: str | None = None) -> Any:
        return self._call("append_text", path, content, expected_sha256=expected_sha256)

    def patch_text(self, path: str, old: str, new: str, *, expected_sha256: str | None = None) -> Any:
        return self._call("patch_text", path, old, new, expected_sha256=expected_sha256)

    def delete_path(self, path: str, *, expected_sha256: str | None = None) -> None:
        self._call("delete_path", path, expected_sha256=expected_sha256)


class DaytonaHostIO:
    """Host I/O views scoped by application composition to one Workspace."""

    def __init__(
        self,
        workspace_id: UUID,
        *,
        volume_gateway: DaytonaWorkspaceVolumeGateway,
        workspace_gateway: DaytonaWorkspaceGateway,
        dispatcher: SyncBridgeDispatcher,
        volume_root: str,
        max_file_bytes: int,
    ) -> None:
        self._workspace_id = workspace_id
        self._workspace_gateway = workspace_gateway
        self._dispatcher = dispatcher
        self._volume_root = volume_root
        self._max_file_bytes = max_file_bytes
        self.volume_fs = _VolumeBlobFs(workspace_id, volume_gateway, dispatcher, volume_root=volume_root)
        self.memory_store = _WorkspaceMemoryStore(
            workspace_id,
            workspace_gateway,
            dispatcher,
            volume_root=volume_root,
            max_file_bytes=max_file_bytes,
        )

    @property
    def dispatcher(self) -> SyncBridgeDispatcher:
        return self._dispatcher

    def workspace_storage(self, root: str) -> _HostWorkspaceStorage:
        return _HostWorkspaceStorage(
            self._workspace_id,
            self._workspace_gateway,
            self._dispatcher,
            volume_root=self._volume_root,
            root=root,
            max_file_bytes=self._max_file_bytes,
        )


class DaytonaRunStorage:
    """Route Run files between private Sandbox scratch and host-authorized storage."""

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
        path = PurePosixPath(location)
        root = PurePosixPath(self.scratch_root)
        return path != root and root in path.parents and ".." not in path.parts

    def _sync_target(self, logical_path: str) -> Any:
        return self._scratch_fs if self._is_scratch(logical_path) else self.host_io.volume_fs

    def result_path(self, session_id: UUID, run_id: UUID) -> str:
        return str(self._paths.run_result_path(session_id, run_id))

    async def read(self, location: str, *, max_bytes: int) -> bytes:
        value = (
            await self._files.read_bytes(location, max_bytes=max_bytes + 1)
            if self._is_scratch(location)
            else await self.host_io.volume_fs.aread_bytes(location, max_bytes=max_bytes + 1)
        )
        if len(value) > max_bytes:
            raise ValueError("value exceeds read bound")
        return value

    async def write(self, location: str, data: bytes) -> None:
        if self._is_scratch(location):
            await self._files.write_bytes(location, data)
        else:
            await self.host_io.volume_fs.awrite_bytes(location, data)

    async def remove(self, location: str) -> None:
        if self._is_scratch(location):
            await self._files.remove_bytes(location)
        else:
            await self.host_io.volume_fs.aremove_bytes(location)

    async def write_private(self, logical_path: str, data: bytes) -> None:
        if not self._is_scratch(logical_path):
            raise ValueError("Run attachment path is outside Run scratch")
        parent = PurePosixPath(logical_path).parent.relative_to(PurePosixPath(self.scratch_root))
        current = PurePosixPath(self.scratch_root)
        directories = []
        for part in parent.parts:
            current /= part
            directories.append(str(current))
        await _ensure_sandbox_directories(self._files.fs, directories)
        await self.write(logical_path, data)

    async def remove_private(self, logical_path: str) -> None:
        if not self._is_scratch(logical_path):
            raise ValueError("Run attachment path is outside Run scratch")
        await self.remove(logical_path)


class _RunVolumeFs:
    """Synchronous VolumeBlobFs view of the Run storage routing policy."""

    def __init__(self, storage: DaytonaRunStorage) -> None:
        self._storage = storage

    def read_bytes(self, logical_path: str, *, max_bytes: int | None = None) -> bytes:
        return self._storage._sync_target(logical_path).read_bytes(logical_path, max_bytes=max_bytes)

    def write_bytes(self, logical_path: str, data: bytes, *, max_bytes: int | None = None) -> None:
        self._storage._sync_target(logical_path).write_bytes(logical_path, data, max_bytes=max_bytes)

    def exists(self, logical_path: str) -> bool:
        return self._storage._sync_target(logical_path).exists(logical_path)

    def remove(self, logical_path: str) -> None:
        self._storage._sync_target(logical_path).remove(logical_path)
