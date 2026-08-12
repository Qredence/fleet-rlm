from __future__ import annotations

import asyncio
import hashlib
from contextlib import redirect_stdout, suppress
from io import StringIO
from pathlib import Path
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
async def test_mounted_gateway_cancels_delete_that_exceeds_grace_period(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import fleet_rlm.daytona.workspace_gateway as workspace_gateway

    platform = _Platform()
    delete_started = asyncio.Event()
    delete_cancelled = asyncio.Event()

    async def slow_delete(_sandbox: object) -> None:
        delete_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            delete_cancelled.set()

    monkeypatch.setattr(platform, "delete", slow_delete)
    monkeypatch.setattr(workspace_gateway, "_SANDBOX_DELETE_GRACE_SECONDS", 0.01)

    async with _core(platform).open_sandbox(uuid4(), purpose="workspace-files-read"):
        pass

    assert delete_started.is_set()
    assert delete_cancelled.is_set()


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


class _LocalProcess:
    """Exec the generated workspace-agent code against a local tmp volume."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def code_run(self, code: str, **kwargs):
        self.calls.append(code)
        output = StringIO()
        with redirect_stdout(output), suppress(SystemExit):
            exec(code, {})
        return SimpleNamespace(exit_code=0, result=output.getvalue().strip())


def _workspace_session(root: Path):
    from fleet_rlm.daytona.workspace_fs import AsyncDaytonaSessionWorkspaceFS
    from fleet_rlm.daytona.workspace_gateway import _DaytonaWorkspaceFileSession

    process = _LocalProcess()
    workspace = AsyncDaytonaSessionWorkspaceFS(
        SimpleNamespace(process=process),
        volume_root=str(root.parents[2]),
        root=str(root),
        max_file_bytes=1024,
    )
    return _DaytonaWorkspaceFileSession(workspace, max_file_bytes=1024), process


@pytest.mark.asyncio
async def test_stat_returns_agent_side_checksum_in_one_round_trip(tmp_path: Path) -> None:
    root = tmp_path / "volume" / "sessions" / "session" / "workspace"
    root.mkdir(parents=True)
    content = b"hello checksum"
    (root / "note.txt").write_bytes(content)
    (root / "notes").mkdir()
    session, process = _workspace_session(root)

    file_entry = await session.stat("note.txt")

    assert file_entry is not None
    assert file_entry.path == "note.txt"
    assert file_entry.kind == "file"
    assert file_entry.byte_size == len(content)
    assert file_entry.checksum_sha256 == hashlib.sha256(content).hexdigest()
    assert len(process.calls) == 1

    directory_entry = await session.stat("notes")
    assert directory_entry is not None
    assert directory_entry.kind == "directory"
    assert directory_entry.checksum_sha256 is None

    root_entry = await session.stat(".")
    assert root_entry is not None
    assert root_entry.path == "."
    assert root_entry.kind == "directory"
    assert root_entry.checksum_sha256 is None

    assert await session.stat("missing.txt") is None


@pytest.mark.asyncio
async def test_delete_and_patch_run_agent_side_in_one_round_trip(tmp_path: Path) -> None:
    root = tmp_path / "volume" / "sessions" / "session" / "workspace"
    root.mkdir(parents=True)
    (root / "note.txt").write_bytes(b"hello world")
    (root / "notes").mkdir()
    session, process = _workspace_session(root)

    patched = await session.patch_text(
        "note.txt", "world", "fleet", expected_sha256=hashlib.sha256(b"hello world").hexdigest()
    )
    assert patched is not None
    assert patched.kind == "file"
    assert patched.byte_size == len(b"hello fleet")
    assert patched.checksum_sha256 == hashlib.sha256(b"hello fleet").hexdigest()
    assert len(process.calls) == 1  # read+compose+publish stayed in one agent run
    assert (root / "note.txt").read_bytes() == b"hello fleet"

    deleted_sha = await session.stat("note.txt")
    assert deleted_sha is not None
    await session.delete_path("note.txt", expected_sha256=deleted_sha.checksum_sha256)
    assert len(process.calls) == 3  # patch + stat + delete; precondition ran agent-side
    assert not (root / "note.txt").exists()

    await session.delete_path("notes", expected_sha256=None)  # empty dir, no precondition
    assert not (root / "notes").exists()


@pytest.mark.asyncio
async def test_delete_and_patch_conflicts_surface_as_public_conflict_error(tmp_path: Path) -> None:
    from fleet_rlm.files.workspace_models import WorkspaceConflictError

    root = tmp_path / "volume" / "sessions" / "session" / "workspace"
    root.mkdir(parents=True)
    (root / "note.txt").write_bytes(b"hello world")
    session, _process = _workspace_session(root)

    with pytest.raises(WorkspaceConflictError) as checksum:
        await session.delete_path("note.txt", expected_sha256="0" * 64)
    assert checksum.value.detail == "checksum_mismatch"
    assert (root / "note.txt").exists()

    with pytest.raises(WorkspaceConflictError) as ambiguous:
        await session.patch_text("note.txt", "o", "0", expected_sha256=None)
    assert ambiguous.value.detail == "ambiguous"

    with pytest.raises(FileNotFoundError):
        await session.delete_path("missing.txt", expected_sha256=None)
    with pytest.raises(FileNotFoundError):
        await session.patch_text("missing.txt", "a", "b", expected_sha256=None)
