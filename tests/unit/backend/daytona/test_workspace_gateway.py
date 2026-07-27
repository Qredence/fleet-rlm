from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from fleet_rlm.daytona.provisioning import DaytonaSandboxSpec, VolumeConfig
from fleet_rlm.daytona.workspace_gateway import DaytonaWorkspaceGateway, DaytonaWorkspaceVolumeGateway

_SPEC = DaytonaSandboxSpec("fleet-test-v1")


class _Fs:
    def __init__(self) -> None:
        self.directories = {"/home/daytona/fleet"}
        self.data: dict[str, bytes] = {}

    async def get_file_info(self, path: str) -> object:
        if path in self.directories:
            return SimpleNamespace(path=path, is_dir=True)
        raise FileNotFoundError(path)

    async def create_folder(self, path: str, mode: str | None = None) -> None:
        del mode
        self.directories.add(path)

    async def upload_file(self, data: bytes, path: str) -> None:
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


class _Platform:
    def __init__(self) -> None:
        self.fs = _Fs()
        self.created: list[dict[str, object]] = []
        self.deleted: list[str] = []

    async def create(self, **kwargs):
        self.created.append(kwargs)
        sandbox = SimpleNamespace(
            id=f"sandbox-{len(self.created)}",
            state="running",
            snapshot=_SPEC.snapshot,
            labels=kwargs["labels"],
            volumes=[
                {
                    "volume_id": kwargs["volume_id"],
                    "mount_path": kwargs["mount_path"],
                    "subpath": kwargs["volume_subpath"],
                }
            ],
            fs=self.fs,
        )
        sandbox.refresh_data = _refresh_data
        return sandbox

    async def delete(self, sandbox: object) -> None:
        self.deleted.append(str(sandbox.id))


class _Volumes:
    async def get(self, name: str, *, create: bool = False) -> object:
        assert name == "shared"
        assert create is True
        return SimpleNamespace(id="volume-1")


async def _refresh_data() -> None:
    return None


def _core(platform: _Platform) -> DaytonaWorkspaceGateway:
    return DaytonaWorkspaceGateway(
        platform=platform,
        volume_client=_Volumes(),
        volume_config=VolumeConfig(name="shared"),
        sandbox_spec=_SPEC,
        max_file_bytes=1024,
    )


@pytest.mark.asyncio
async def test_mounted_gateway_creates_and_deletes_exactly_one_ephemeral_sandbox() -> None:
    platform = _Platform()
    workspace_id = uuid4()

    async with _core(platform).open_sandbox(workspace_id, purpose="workspace-files-read"):
        pass

    assert platform.created == [
        {
            "volume_id": "volume-1",
            "mount_path": "/home/daytona/fleet",
            "volume_subpath": f"workspaces/{workspace_id}",
            "labels": {
                "fleet-package": "fleet_rlm",
                "purpose": "workspace-files-read",
                "workspace_id": str(workspace_id),
            },
            "ephemeral": True,
        }
    ]
    assert platform.deleted == ["sandbox-1"]


@pytest.mark.asyncio
async def test_bytes_survive_across_two_different_deleted_io_sandboxes() -> None:
    platform = _Platform()
    gateway = DaytonaWorkspaceVolumeGateway(
        _core(platform),
        mount_path="/home/daytona/fleet",
    )
    workspace_id = uuid4()
    path = "/home/daytona/fleet/files/persistent.txt"

    await gateway.write_bytes(workspace_id, path, b"durable")
    assert await gateway.read_bytes(workspace_id, path) == b"durable"

    assert len(platform.created) == 2
    assert platform.deleted == ["sandbox-1", "sandbox-2"]
