from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from fleet_rlm.daytona.workspace_gateway import DaytonaWorkspaceVolumeGateway, cleanup_orphan_bytes
from fleet_rlm.files.volume_paths import VolumePaths


@dataclass
class DaytonaCalls:
    sandbox_create: int = 0
    list_files: int = 0
    delete_file: int = 0
    sandbox_delete: int = 0


class _Fs:
    def __init__(self, calls: DaytonaCalls) -> None:
        self.calls = calls
        self.files: dict[str, tuple[bytes, float]] = {}

    async def create_folder(self, path: str, mode: str | None = None) -> None:
        del mode
        del path

    async def upload_file(self, data: bytes, path: str) -> None:
        self.files[path] = (bytes(data), 1.0)

    async def download_file(self, path: str) -> bytes:
        return self.files[path][0]

    async def delete_file(self, path: str) -> None:
        self.calls.delete_file += 1
        self.files.pop(path, None)

    async def list_files(self, path: str, *, depth: int) -> list[object]:
        del depth
        self.calls.list_files += 1
        return [
            SimpleNamespace(path=file_path, is_dir=False, mod_time=modified_at)
            for file_path, (_, modified_at) in sorted(self.files.items())
            if file_path.startswith(path + "/")
        ]


class _MountedGateway:
    def __init__(self) -> None:
        self.calls = DaytonaCalls()
        self.fs = _Fs(self.calls)
        self.sandbox = SimpleNamespace(fs=self.fs)

    @asynccontextmanager
    async def open_sandbox(self, workspace_id: UUID, *, purpose: str):
        del workspace_id
        assert purpose == "workspace-volume-io"
        self.calls.sandbox_create += 1
        try:
            yield self.sandbox
        finally:
            self.calls.sandbox_delete += 1


def _gateway(mounted: _MountedGateway) -> DaytonaWorkspaceVolumeGateway:
    return DaytonaWorkspaceVolumeGateway(
        mounted,  # ty: ignore[invalid-argument-type] - mounted gateway fake
        mount_path="/home/daytona/fleet",
    )


@pytest.mark.asyncio
async def test_one_workspace_context_batches_multiple_operations() -> None:
    mounted = _MountedGateway()
    gateway = _gateway(mounted)
    workspace_id = uuid4()
    path = "/home/daytona/fleet/attachments/a.bin"

    async with gateway.open_workspace(workspace_id) as volume:
        await volume.write_bytes(path, b"payload")
        assert await volume.read_bytes(path) == b"payload"
        await volume.list_files(
            "/home/daytona/fleet/attachments",
            max_depth=2,
            max_files=10,
        )
        await volume.remove_bytes(path)

    assert mounted.calls.sandbox_create == 1
    assert mounted.calls.sandbox_delete == 1


@pytest.mark.asyncio
async def test_convenience_methods_each_own_one_io_sandbox() -> None:
    mounted = _MountedGateway()
    gateway = _gateway(mounted)
    workspace_id = uuid4()
    path = "/home/daytona/fleet/attachments/a.bin"

    await gateway.write_bytes(workspace_id, path, b"payload")
    await gateway.read_bytes(workspace_id, path)
    await gateway.list_files(
        workspace_id,
        "/home/daytona/fleet/attachments",
        max_depth=2,
        max_files=10,
    )
    await gateway.remove_bytes(workspace_id, path)

    assert mounted.calls.sandbox_create == 4
    assert mounted.calls.sandbox_delete == 4


@pytest.mark.asyncio
async def test_orphan_cleanup_uses_one_sandbox_for_two_lists_and_three_removals() -> None:
    mounted = _MountedGateway()
    gateway = _gateway(mounted)
    paths = VolumePaths.from_mount()
    workspace_id = uuid4()
    session_id = uuid4()
    for path in (
        paths.artifact_blob_path(uuid4()),
        paths.artifact_blob_path(uuid4()),
        paths.run_result_path(session_id, uuid4()),
    ):
        mounted.fs.files[str(path)] = (b"stale", 1.0)

    report = await cleanup_orphan_bytes(
        gateway,
        workspace_id=workspace_id,
        paths=paths,
        committed_storage_refs=(),
        completed_runs=(),
        now=datetime.fromtimestamp(7200, UTC),
        max_files=20,
    )

    assert report.removed == 3
    assert mounted.calls.sandbox_create == 1
    assert mounted.calls.list_files == 2
    assert mounted.calls.delete_file == 3
    assert mounted.calls.sandbox_delete == 1
