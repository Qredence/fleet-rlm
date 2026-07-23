from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from fleet_rlm.daytona.errors import DaytonaAdapterError
from fleet_rlm.daytona.orphan_cleanup import cleanup_orphan_bytes
from fleet_rlm.daytona.paths import VolumePaths
from fleet_rlm.daytona.sandbox_spec import DaytonaSandboxSpec
from fleet_rlm.daytona.workspace_volume import DaytonaWorkspaceVolumeGateway


@dataclass
class DaytonaCalls:
    volume_get: int = 0
    sandbox_create: int = 0
    list_files: int = 0
    delete_file: int = 0
    sandbox_delete: int = 0


class _Fs:
    def __init__(self, calls: DaytonaCalls) -> None:
        self.calls = calls
        self.files: dict[str, tuple[bytes, float]] = {}
        self.folders = {"/home/daytona/fleet"}

    async def get_file_info(self, path: str) -> object:
        if path in self.folders:
            return SimpleNamespace(path=path, is_dir=True)
        if path in self.files:
            return SimpleNamespace(path=path, is_dir=False)
        error = RuntimeError("not found")
        error.status_code = 404  # type: ignore[attr-defined]
        raise error

    async def create_folder(self, path: str, mode: str) -> None:
        del mode
        self.folders.add(path)

    async def upload_file(self, data: bytes, path: str) -> None:
        self.files[path] = (data, 1.0)

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


class _Client:
    def __init__(self, *, fail_create: bool = False, volume_states: list[str] | None = None) -> None:
        self.calls = DaytonaCalls()
        self.fs = _Fs(self.calls)
        self.sandbox = SimpleNamespace(id="sandbox-1", fs=self.fs)
        self.volume = SimpleNamespace(get=self._get_volume)
        self.fail_create = fail_create
        self.volume_states = volume_states or []

    async def _get_volume(self, name: str, *, create: bool) -> object:
        assert name == "shared"
        assert create in {True, False}
        self.calls.volume_get += 1
        state = self.volume_states.pop(0) if self.volume_states else None
        return SimpleNamespace(id="volume-1", state=state)

    async def create(self, params: object) -> object:
        self.calls.sandbox_create += 1
        self.params = params
        if self.fail_create:
            raise RuntimeError("provider api_key=private unavailable")
        return self.sandbox

    async def delete(self, sandbox: object) -> None:
        assert sandbox is self.sandbox
        self.calls.sandbox_delete += 1


def _gateway(client: _Client) -> DaytonaWorkspaceVolumeGateway:
    return DaytonaWorkspaceVolumeGateway(
        client,
        volume_name="shared",
        mount_path="/home/daytona/fleet",
        sandbox_spec=DaytonaSandboxSpec("fleet-test-v1"),
    )


@pytest.mark.asyncio
async def test_one_workspace_context_batches_multiple_operations() -> None:
    client = _Client()
    gateway = _gateway(client)
    workspace_id = uuid4()
    path = "/home/daytona/fleet/attachments/a.bin"

    async with gateway.open_workspace(workspace_id) as volume:
        await volume.write_bytes(path, b"payload")
        assert await volume.read_bytes(path) == b"payload"
        await volume.list_files("/home/daytona/fleet/attachments", max_depth=2, max_files=10)
        await volume.remove_bytes(path)

    assert client.calls.volume_get == 1
    assert client.calls.sandbox_create == 1
    assert client.calls.sandbox_delete == 1


@pytest.mark.asyncio
async def test_convenience_methods_each_own_one_io_sandbox() -> None:
    client = _Client()
    gateway = _gateway(client)
    workspace_id = uuid4()
    path = "/home/daytona/fleet/attachments/a.bin"

    await gateway.write_bytes(workspace_id, path, b"payload")
    await gateway.read_bytes(workspace_id, path)
    await gateway.list_files(workspace_id, "/home/daytona/fleet/attachments", max_depth=2, max_files=10)
    await gateway.remove_bytes(workspace_id, path)

    assert client.calls.sandbox_create == 4
    assert client.calls.sandbox_delete == 4


@pytest.mark.asyncio
async def test_workspace_context_deletes_sandbox_when_body_fails() -> None:
    client = _Client()
    gateway = _gateway(client)

    with pytest.raises(RuntimeError, match="body failed"):
        async with gateway.open_workspace(uuid4()):
            raise RuntimeError("body failed")

    assert client.calls.sandbox_create == 1
    assert client.calls.sandbox_delete == 1


@pytest.mark.asyncio
async def test_workspace_context_does_not_delete_when_creation_fails() -> None:
    client = _Client(fail_create=True)
    gateway = _gateway(client)

    with pytest.raises(DaytonaAdapterError) as caught:
        async with gateway.open_workspace(uuid4()):
            pass

    assert "private" not in str(caught.value)
    assert client.calls.sandbox_create == 1
    assert client.calls.sandbox_delete == 0


@pytest.mark.asyncio
async def test_workspace_waits_for_volume_readiness(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _Client(volume_states=["pending_create", "ready"])
    gateway = _gateway(client)
    sleeps: list[float] = []

    async def no_wait(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr("fleet_rlm.daytona.workspace_volume.asyncio.sleep", no_wait)
    async with gateway.open_workspace(uuid4()):
        pass

    assert sleeps == [0.25]
    assert client.calls.volume_get == 2


@pytest.mark.asyncio
async def test_orphan_cleanup_uses_one_sandbox_for_two_lists_and_three_removals() -> None:
    client = _Client()
    gateway = _gateway(client)
    paths = VolumePaths.from_mount()
    workspace_id = uuid4()
    session_id = uuid4()
    for path in (
        paths.artifact_blob_path(uuid4()),
        paths.artifact_blob_path(uuid4()),
        paths.run_result_path(session_id, uuid4()),
    ):
        client.fs.files[str(path)] = (b"stale", 1.0)

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
    assert client.calls.sandbox_create == 1
    assert client.calls.list_files == 2
    assert client.calls.delete_file == 3
    assert client.calls.sandbox_delete == 1
