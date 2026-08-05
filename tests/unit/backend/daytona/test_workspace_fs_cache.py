from __future__ import annotations

from types import SimpleNamespace

import pytest


class _AsyncFs:
    def __init__(self) -> None:
        self.files: dict[str, tuple[bytes, float]] = {}
        self.download_calls = 0
        self.list_calls = 0

    async def create_folder(self, _path: str, _mode: str) -> None:
        return None

    async def upload_file(self, data: bytes, path: str) -> None:
        self.files[path] = (bytes(data), 2.0)

    async def download_file(self, path: str) -> bytes:
        self.download_calls += 1
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path][0]

    async def delete_file(self, path: str) -> None:
        self.files.pop(path, None)

    async def list_files(self, path: str, *, depth: int) -> list[object]:
        del depth
        self.list_calls += 1
        return [
            SimpleNamespace(path=item, is_dir=False, mod_time=modified_at)
            for item, (_data, modified_at) in sorted(self.files.items())
            if item.startswith(path + "/")
        ]


class _SyncFs:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.download_calls = 0
        self.list_calls = 0

    def create_folder(self, _path: str, _mode: str) -> None:
        return None

    def upload_file(self, data: bytes, path: str) -> None:
        self.files[path] = bytes(data)

    def download_file(self, path: str) -> bytes:
        self.download_calls += 1
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    def delete_file(self, path: str) -> None:
        self.files.pop(path, None)

    def list_files(self, path: str, *, depth: int) -> list[object]:
        del depth
        self.list_calls += 1
        return [
            SimpleNamespace(path=item, is_dir=False, mod_time=1.0)
            for item in sorted(self.files)
            if item.startswith(path + "/")
        ]


@pytest.mark.asyncio
async def test_async_cache_is_mount_aware_and_invalidates_content_and_listings() -> None:
    from fleet_rlm.daytona.workspace_fs import AsyncDaytonaVolumeFS

    mount = "/custom/fleet"
    path = f"{mount}/artifacts/report.json"
    fs = _AsyncFs()
    volume = AsyncDaytonaVolumeFS(SimpleNamespace(fs=fs), mount_path=mount)

    await volume.write_bytes(path, b"first")
    assert await volume.read_bytes(path) == b"first"
    assert await volume.read_bytes(path) == b"first"
    assert fs.download_calls == 1

    first_listing = await volume.list_files(f"{mount}/artifacts", max_depth=2, max_files=10)
    second_listing = await volume.list_files(f"{mount}/artifacts", max_depth=2, max_files=10)
    assert first_listing == second_listing
    assert first_listing[0].modified_at == 2.0
    assert fs.list_calls == 1

    await volume.write_bytes(path, b"second")
    assert await volume.read_bytes(path) == b"second"
    assert fs.download_calls == 2
    await volume.list_files(f"{mount}/artifacts", max_depth=2, max_files=10)
    assert fs.list_calls == 2

    await volume.remove(path)
    with pytest.raises(FileNotFoundError):
        await volume.read_bytes(path)
    await volume.list_files(f"{mount}/artifacts", max_depth=2, max_files=10)
    assert fs.list_calls == 3


def test_sync_cache_is_mount_aware() -> None:
    from fleet_rlm.daytona.workspace_fs import DaytonaSandboxVolumeFs

    mount = "/custom/fleet"
    path = f"{mount}/artifacts/report.json"
    fs = _SyncFs()
    volume = DaytonaSandboxVolumeFs(SimpleNamespace(fs=fs), mount_path=mount)

    volume.write_bytes(path, b"payload")
    assert volume.read_bytes(path) == b"payload"
    assert volume.read_bytes(path) == b"payload"
    assert fs.download_calls == 1
    assert volume.list_files(f"{mount}/artifacts", max_depth=2, max_files=10)
    assert volume.list_files(f"{mount}/artifacts", max_depth=2, max_files=10)
    assert fs.list_calls == 1

    volume.write_bytes(path, b"updated")
    assert volume.read_bytes(path) == b"updated"
    assert fs.download_calls == 2
    volume.list_files(f"{mount}/artifacts", max_depth=2, max_files=10)
    assert fs.list_calls == 2

    volume.remove(path)
    with pytest.raises(FileNotFoundError):
        volume.read_bytes(path)
