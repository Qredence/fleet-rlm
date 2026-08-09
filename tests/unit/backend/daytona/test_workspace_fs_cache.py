from __future__ import annotations

import asyncio
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


@pytest.mark.asyncio
async def test_shared_cache_state_does_not_store_stale_read_after_cross_adapter_mutation() -> None:
    from fleet_rlm.daytona.workspace_fs import (
        AsyncDaytonaVolumeFS,
        DaytonaSandboxVolumeFs,
        VolumeFSCacheState,
    )

    mount = "/custom/fleet"
    path = f"{mount}/artifacts/report.json"
    gate = asyncio.Event()
    fetched = asyncio.Event()

    class _BlockingAsyncFs(_AsyncFs):
        async def download_file(self, p: str) -> bytes:
            self.download_calls += 1
            if p not in self.files:
                raise FileNotFoundError(p)
            # Snapshot provider data first, then block: simulates a slow read
            # that completes after a local mutation invalidated the cache.
            data = self.files[p][0]
            fetched.set()
            await gate.wait()
            return data

    async_fs = _BlockingAsyncFs()
    sync_fs = _SyncFs()
    cache_state = VolumeFSCacheState()
    async_volume = AsyncDaytonaVolumeFS(SimpleNamespace(fs=async_fs), mount_path=mount, cache_state=cache_state)
    sync_volume = DaytonaSandboxVolumeFs(SimpleNamespace(fs=sync_fs), mount_path=mount, cache_state=cache_state)

    async_fs.files[path] = (b"first", 1.0)
    sync_fs.files[path] = b"first"

    read_task = asyncio.create_task(async_volume.read_bytes(path))
    await fetched.wait()

    # Mutate the same path through the other adapter while the read is blocked.
    sync_volume.write_bytes(path, b"second")
    async_fs.files[path] = (b"second", 2.0)

    gate.set()
    assert await read_task == b"first"

    # The stale pre-mutation value must not have been cached: the next read
    # goes back to the provider and observes the mutated content.
    gate.set()
    assert await async_volume.read_bytes(path) == b"second"
    assert async_fs.download_calls == 2
    assert sync_volume.read_bytes(path) == b"second"


@pytest.mark.asyncio
async def test_shared_cache_state_invalidates_across_adapters() -> None:
    from fleet_rlm.daytona.workspace_fs import (
        AsyncDaytonaVolumeFS,
        DaytonaSandboxVolumeFs,
        VolumeFSCacheState,
    )

    mount = "/custom/fleet"
    path = f"{mount}/artifacts/report.json"
    async_fs = _AsyncFs()
    sync_fs = _SyncFs()
    cache_state = VolumeFSCacheState()
    async_volume = AsyncDaytonaVolumeFS(SimpleNamespace(fs=async_fs), mount_path=mount, cache_state=cache_state)
    sync_volume = DaytonaSandboxVolumeFs(SimpleNamespace(fs=sync_fs), mount_path=mount, cache_state=cache_state)

    async_fs.files[path] = (b"first", 1.0)
    sync_fs.files[path] = b"first"

    # Populate the shared cache through the async adapter.
    assert await async_volume.read_bytes(path) == b"first"
    assert await async_volume.read_bytes(path) == b"first"
    assert async_fs.download_calls == 1

    # A write through the sync adapter must invalidate the async view.
    sync_volume.write_bytes(path, b"second")
    async_fs.files[path] = (b"second", 2.0)
    assert await async_volume.read_bytes(path) == b"second"
    assert async_fs.download_calls == 2


def test_lru_cache_bounds_entry_count() -> None:
    from fleet_rlm.daytona.workspace_fs import _LRUCache

    cache = _LRUCache(max_size_mb=100, max_entries=5)
    for index in range(10):
        cache.put(f"key-{index}", b"x")
    assert len(cache) == 5
    # Oldest entries were evicted; newest survive.
    assert cache.get("key-0") is None
    assert cache.get("key-9") == b"x"


def test_lru_cache_bounds_zero_byte_entries() -> None:
    from fleet_rlm.daytona.workspace_fs import _LRUCache

    cache = _LRUCache(max_size_mb=100, max_entries=3)
    for index in range(8):
        cache.put(f"empty-{index}", b"")
    assert len(cache) == 3
    assert cache.get("empty-7") == b""


def test_lru_cache_replacement_does_not_evict_other_entries() -> None:
    from fleet_rlm.daytona.workspace_fs import _LRUCache

    cache = _LRUCache(max_size_mb=100, max_entries=2)
    cache.put("a", b"1")
    cache.put("b", b"2")
    cache.put("a", b"3")
    assert len(cache) == 2
    assert cache.get("a") == b"3"
    assert cache.get("b") == b"2"


def test_modified_timestamp_parses_daytona_mod_time_strings() -> None:
    from fleet_rlm.daytona.workspace_fs import _modified_timestamp

    assert _modified_timestamp("2026-08-08 21:26:10 +0000 UTC") == 1786224370.0
    assert abs(_modified_timestamp("2026-07-30 00:05:20.290395882 +0000 UTC") - 1785369920.290395) < 1e-3
    assert abs(_modified_timestamp("2026-08-08T21:26:10.123456+00:00") - 1786224370.123456) < 1e-6
    assert _modified_timestamp("garbage") is None
    assert _modified_timestamp(None) is None
    assert _modified_timestamp(True) is None
