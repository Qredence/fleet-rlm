from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest

from fleet_rlm.daytona.interpreter import SyncBridgeDispatcher
from fleet_rlm.workspace.host_io import DaytonaHostIO
from fleet_rlm.workspace.mounted_gateway import DaytonaWorkspaceGateway, DaytonaWorkspaceVolumeGateway
from fleet_rlm.workspace.paths import UnsafePathError


class _Fs:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}

    def get_file_info(self, path: str) -> dict[str, object]:
        if path in self.files:
            return {"type": "file"}
        raise FileNotFoundError(path)

    def download_file(self, path: str) -> bytes:
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    def upload_file(self, data: bytes, path: str) -> None:
        self.files[path] = bytes(data)

    def delete_file(self, path: str) -> None:
        self.files.pop(path, None)


class _Runtime:
    def __init__(self) -> None:
        self.sandboxes: list[SimpleNamespace] = []
        self.filesystem = _Fs()

    @asynccontextmanager
    async def open_workspace_sandbox(self, workspace_id, *, purpose):
        del workspace_id, purpose
        sandbox = SimpleNamespace(fs=self.filesystem, id=f"sandbox-{len(self.sandboxes)}")
        self.sandboxes.append(sandbox)
        yield sandbox


@pytest.mark.asyncio
async def test_volume_proxy_is_workspace_bound_and_rejects_scope_escape() -> None:
    workspace_id = uuid4()
    runtime = _Runtime()
    base = DaytonaWorkspaceGateway(
        runtime=runtime,
        paths=SimpleNamespace(mount_path="/volume", files_root=lambda: "/volume/files"),
        max_file_bytes=1024,
        map_error=lambda error: error,
    )
    volume = DaytonaWorkspaceVolumeGateway(base, mount_path="/volume")
    dispatcher = SyncBridgeDispatcher()
    loop = asyncio.get_running_loop()
    dispatcher.set_loop(loop)
    host_io = DaytonaHostIO(
        workspace_id,
        volume_gateway=volume,
        workspace_gateway=base,
        dispatcher=dispatcher,
        volume_root="/volume",
        max_file_bytes=1024,
    )

    await asyncio.to_thread(host_io.volume_fs.write_bytes, "/volume/a.bin", b"payload")
    assert await asyncio.to_thread(host_io.volume_fs.read_bytes, "/volume/a.bin") == b"payload"
    with pytest.raises(UnsafePathError):
        await asyncio.to_thread(host_io.volume_fs.read_bytes, "/outside/a.bin")
    assert len(runtime.sandboxes) == 2


@pytest.mark.asyncio
async def test_memory_proxy_uses_one_short_lived_sandbox_per_operation() -> None:
    workspace_id = uuid4()
    runtime = _Runtime()
    base = DaytonaWorkspaceGateway(
        runtime=runtime,
        paths=SimpleNamespace(mount_path="/volume", files_root=lambda: "/volume/files"),
        max_file_bytes=4096,
        map_error=lambda error: error,
    )
    dispatcher = SyncBridgeDispatcher()
    dispatcher.set_loop(asyncio.get_running_loop())
    host_io = DaytonaHostIO(
        workspace_id,
        volume_gateway=DaytonaWorkspaceVolumeGateway(base, mount_path="/volume"),
        workspace_gateway=base,
        dispatcher=dispatcher,
        volume_root="/volume",
        max_file_bytes=4096,
    )

    result = await asyncio.to_thread(host_io.memory_store.read_tail, byte_budget=1024)
    assert result.content == ""
    assert len(runtime.sandboxes) == 1


@pytest.mark.asyncio
async def test_concurrent_host_byte_operations_share_workspace_authority() -> None:
    workspace_id = uuid4()
    runtime = _Runtime()
    base = DaytonaWorkspaceGateway(
        runtime=runtime,
        paths=SimpleNamespace(mount_path="/volume", files_root=lambda: "/volume/files"),
        max_file_bytes=1024,
        map_error=lambda error: error,
    )
    dispatcher = SyncBridgeDispatcher()
    dispatcher.set_loop(asyncio.get_running_loop())
    host_io = DaytonaHostIO(
        workspace_id,
        volume_gateway=DaytonaWorkspaceVolumeGateway(base, mount_path="/volume"),
        workspace_gateway=base,
        dispatcher=dispatcher,
        volume_root="/volume",
        max_file_bytes=1024,
    )

    await asyncio.gather(
        host_io.volume_fs.awrite_bytes("/volume/a.bin", b"a"),
        host_io.volume_fs.awrite_bytes("/volume/b.bin", b"b"),
    )
    assert await host_io.volume_fs.aread_bytes("/volume/a.bin") == b"a"
    assert await host_io.volume_fs.aread_bytes("/volume/b.bin") == b"b"
    assert len(runtime.sandboxes) == 4
