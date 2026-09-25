"""Daytona Workspace I/O projection and volume-gateway contracts.

* ``test_workspace_gateway.py``: Focused contracts for Daytona Workspace I/O projection.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from fleet_rlm.daytona.runtime import SandboxLeasePolicy
from fleet_rlm.workspace.models import WorkspaceEntry
from fleet_rlm.workspace.mounted_gateway import DaytonaWorkspaceVolumeGateway, _DaytonaWorkspaceFileSession
from fleet_rlm.workspace.paths import UnsafePathError


# --- from test_workspace_volume_gateway.py ----------------------------
class _FakeFs:
    def __init__(self, *, fail_upload: bool = False) -> None:
        self.data: dict[str, bytes] = {}
        self.fail_upload = fail_upload

    async def create_folder(self, path: str, mode: str | None = None) -> None:
        del path, mode

    async def upload_file(self, data: bytes, path: str) -> None:
        if self.fail_upload:
            raise RuntimeError("provider unavailable")
        self.data[path] = bytes(data)

    async def download_file(self, path: str) -> bytes:
        return self.data[path]

    async def delete_file(self, path: str) -> None:
        self.data.pop(path, None)

    async def list_files(self, path: str, *, depth: int) -> list[object]:
        del depth
        return [
            SimpleNamespace(path=value, is_dir=False, mod_time=1.0)
            for value in sorted(self.data)
            if value.startswith(path + "/")
        ]


class _MountedGateway:
    def __init__(self, *, fail_upload: bool = False) -> None:
        self.sandbox = SimpleNamespace(fs=_FakeFs(fail_upload=fail_upload))
        self.opens: list[tuple[UUID, str]] = []
        self.closes = 0

    @asynccontextmanager
    async def open_sandbox(self, workspace_id: UUID, *, purpose: str):
        self.opens.append((workspace_id, purpose))
        try:
            yield self.sandbox
        finally:
            self.closes += 1


@pytest.mark.asyncio
async def test_gateway_uses_one_shared_mounted_scope_for_grouped_byte_operations() -> None:
    mounted = _MountedGateway()
    gateway = DaytonaWorkspaceVolumeGateway(
        mounted,  # ty: ignore[invalid-argument-type] - focused mounted gateway fake
        mount_path="/home/daytona/fleet",
    )
    workspace_id = uuid4()
    path = "/home/daytona/fleet/attachments/a.bin"

    async with gateway.open_workspace(workspace_id) as volume:
        await volume.write_bytes(path, b"payload")
        assert await volume.read_bytes(path) == b"payload"
        assert await volume.list_files(
            "/home/daytona/fleet/attachments",
            max_depth=2,
            max_files=10,
        )
        await volume.remove_bytes(path)

    assert mounted.opens == [(workspace_id, "workspace-volume-io")]
    assert mounted.closes == 1


@pytest.mark.asyncio
async def test_gateway_can_list_the_mount_root() -> None:
    mounted = _MountedGateway()
    mounted.sandbox.fs.data["/home/daytona/fleet/files/notes.md"] = b"notes"
    gateway = DaytonaWorkspaceVolumeGateway(
        mounted,  # ty: ignore[invalid-argument-type]
        mount_path="/home/daytona/fleet",
    )

    files = await gateway.list_files(
        uuid4(),
        "/home/daytona/fleet",
        max_depth=8,
        max_files=10,
    )

    assert [file.path for file in files] == ["/home/daytona/fleet/files/notes.md"]


@pytest.mark.asyncio
async def test_gateway_releases_mounted_scope_when_operation_fails() -> None:
    mounted = _MountedGateway(fail_upload=True)
    gateway = DaytonaWorkspaceVolumeGateway(
        mounted,  # ty: ignore[invalid-argument-type]
        mount_path="/home/daytona/fleet",
    )

    with pytest.raises(RuntimeError, match="unavailable"):
        await gateway.write_bytes(
            uuid4(),
            "/home/daytona/fleet/attachments/a.bin",
            b"payload",
        )

    assert mounted.closes == 1


@pytest.mark.asyncio
async def test_gateway_rejects_paths_outside_workspace_mount_before_file_io() -> None:
    mounted = _MountedGateway()
    gateway = DaytonaWorkspaceVolumeGateway(
        mounted,  # ty: ignore[invalid-argument-type]
        mount_path="/home/daytona/fleet",
    )

    with pytest.raises(UnsafePathError):
        await gateway.write_bytes(
            uuid4(),
            "/home/daytona/other/foreign.bin",
            b"payload",
        )

    assert mounted.sandbox.fs.data == {}


# --- from test_workspace_gateway.py -----------------------------------
@pytest.mark.asyncio
async def test_stat_preserves_an_explicit_false_checksum_request() -> None:
    calls: list[bool | None] = []

    class Workspace:
        async def stat(self, path: str, *, include_checksum: bool | None = None) -> WorkspaceEntry:
            calls.append(include_checksum)
            return WorkspaceEntry(path, "file", 3, None, None)

    session = _DaytonaWorkspaceFileSession(Workspace(), max_file_bytes=1024)
    entry = await session.stat("note.txt", include_checksum=False)

    assert calls == [False]
    assert entry is not None
    assert entry.checksum_sha256 is None


def test_volume_io_policy_preserves_explicit_confirmation_bounds() -> None:
    policy = SandboxLeasePolicy(
        kind="volume_io",
        confirm_timeout_s=7.0,
        confirm_poll_interval_s=0.25,
    )

    assert policy.confirm_absence is True
    assert policy.confirm_timeout_s == 7.0
    assert policy.confirm_poll_interval_s == 0.25
