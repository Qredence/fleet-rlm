from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from fleet_rlm_clean.daytona.errors import DaytonaAdapterError
from fleet_rlm_clean.daytona.paths import UnsafePathError
from fleet_rlm_clean.daytona.workspace_volume import DaytonaWorkspaceVolumeGateway


class _FakeFs:
    def __init__(self, *, fail_upload: bool = False) -> None:
        self.data: dict[str, bytes] = {}
        self.folders = {"/home/daytona/fleet"}
        self.fail_upload = fail_upload

    async def get_file_info(self, path: str) -> object:
        if path not in self.folders:
            error = RuntimeError("not found")
            error.status_code = 404  # type: ignore[attr-defined]
            raise error
        return SimpleNamespace(path=path)

    async def create_folder(self, path: str, mode: str) -> None:
        assert mode == "755"
        self.folders.add(path)

    async def upload_file(self, data: bytes, path: str) -> None:
        if self.fail_upload:
            raise RuntimeError("provider api_key=super-secret unavailable")
        self.data[path] = data

    async def download_file(self, path: str) -> bytes:
        return self.data[path]


class _FakeClient:
    def __init__(self, *, fail_upload: bool = False, fail_delete: bool = False) -> None:
        self.volume = SimpleNamespace(get=self._get_volume)
        self.sandbox = SimpleNamespace(fs=_FakeFs(fail_upload=fail_upload))
        self.params = None
        self.deleted = 0
        self.fail_delete = fail_delete

    async def _get_volume(self, name: str, *, create: bool) -> object:
        assert name == "shared"
        assert create is True
        return SimpleNamespace(id="vol-1")

    async def create(self, params: object) -> object:
        self.params = params
        return self.sandbox

    async def delete(self, sandbox: object) -> None:
        assert sandbox is self.sandbox
        if self.fail_delete:
            raise RuntimeError("api_key=private cleanup failed")
        self.deleted += 1


@pytest.mark.asyncio
async def test_gateway_mounts_only_workspace_scope_and_deletes_io_sandbox() -> None:
    client = _FakeClient()
    workspace_id = uuid4()
    gateway = DaytonaWorkspaceVolumeGateway(
        client,
        volume_name="shared",
        mount_path="/home/daytona/fleet",
    )

    await gateway.write_bytes(workspace_id, "/home/daytona/fleet/attachments/a.bin", b"payload")

    mount = client.params.volumes[0]
    assert mount.subpath == f"workspaces/{workspace_id}"
    assert mount.mount_path == "/home/daytona/fleet"
    assert client.params.ephemeral is True
    # Daytona 0.192 rejects auto_delete_interval with ephemeral=True; explicit
    # deletion is primary and ephemeral lifecycle is the provider backstop.
    assert client.params.auto_delete_interval == 0
    assert "/home/daytona/fleet/attachments" in client.sandbox.fs.folders
    assert client.deleted == 1


@pytest.mark.asyncio
async def test_gateway_deletes_io_sandbox_when_file_operation_fails() -> None:
    client = _FakeClient(fail_upload=True)
    gateway = DaytonaWorkspaceVolumeGateway(
        client,
        volume_name="shared",
        mount_path="/home/daytona/fleet",
    )

    with pytest.raises(DaytonaAdapterError) as caught:
        await gateway.write_bytes(uuid4(), "/home/daytona/fleet/attachments/a.bin", b"payload")

    assert "super-secret" not in str(caught.value)
    assert client.deleted == 1


@pytest.mark.asyncio
async def test_gateway_rejects_paths_outside_the_mounted_workspace_scope() -> None:
    client = _FakeClient()
    gateway = DaytonaWorkspaceVolumeGateway(
        client,
        volume_name="shared",
        mount_path="/home/daytona/fleet",
    )

    with pytest.raises(UnsafePathError):
        await gateway.write_bytes(uuid4(), "/home/daytona/other/foreign.bin", b"payload")

    assert client.params is None


@pytest.mark.asyncio
async def test_gateway_logs_safe_cleanup_failure_and_relies_on_ephemeral_backstop(caplog) -> None:
    client = _FakeClient(fail_delete=True)
    workspace_id = uuid4()
    gateway = DaytonaWorkspaceVolumeGateway(
        client,
        volume_name="shared",
        mount_path="/home/daytona/fleet",
    )

    with caplog.at_level("WARNING"):
        await gateway.write_bytes(
            workspace_id,
            "/home/daytona/fleet/attachments/a.bin",
            b"payload",
        )

    assert "Sandbox deletion failed" in caplog.text
    assert "api_key" not in caplog.text
