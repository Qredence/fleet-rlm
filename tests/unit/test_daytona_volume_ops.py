from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from fleet_rlm.integrations.providers.daytona.volumes import (
    list_daytona_volume_tree,
    read_daytona_volume_file_text,
)


def test_list_daytona_volume_tree_uses_native_fs_listing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class _FakeFs:
        def list_files(self, path: str):
            calls.append(path)
            if path == "/home/daytona/memory":
                return [
                    SimpleNamespace(
                        name="memory",
                        is_dir=True,
                        mod_time=datetime(2024, 1, 1, tzinfo=timezone.utc),
                    ),
                    SimpleNamespace(
                        name="artifacts",
                        is_dir=True,
                        mod_time=None,
                    ),
                    SimpleNamespace(
                        name="buffers",
                        is_dir=True,
                        mod_time=None,
                    ),
                    SimpleNamespace(
                        name="meta",
                        is_dir=True,
                        mod_time=None,
                    ),
                ]
            if path == "/home/daytona/memory/artifacts":
                return [
                    SimpleNamespace(
                        name="docs",
                        is_dir=True,
                        mod_time=None,
                    ),
                    SimpleNamespace(
                        name="hello.txt",
                        is_dir=False,
                        size=5,
                        mod_time=None,
                    ),
                ]
            if path == "/home/daytona/memory/artifacts/docs":
                return [
                    SimpleNamespace(
                        name="notes.md",
                        is_dir=False,
                        size=12,
                        mod_time=None,
                    )
                ]
            if path in {
                "/home/daytona/memory/memory",
                "/home/daytona/memory/buffers",
                "/home/daytona/memory/meta",
            }:
                return []
            raise AssertionError(f"unexpected list path: {path}")

    @asynccontextmanager
    async def _fake_mounted_daytona_volume(volume_name: str):
        assert volume_name == "tenant-a"
        yield SimpleNamespace(fs=_FakeFs())

    monkeypatch.setattr(
        "fleet_rlm.integrations.providers.daytona.volumes._amounted_daytona_volume",
        _fake_mounted_daytona_volume,
    )

    payload = list_daytona_volume_tree("tenant-a", root_path="/", max_depth=3)

    assert calls == [
        "/home/daytona/memory",
        "/home/daytona/memory/memory",
        "/home/daytona/memory/artifacts",
        "/home/daytona/memory/artifacts/docs",
        "/home/daytona/memory/buffers",
        "/home/daytona/memory/meta",
    ]
    assert payload["volume_name"] == "tenant-a"
    assert payload["root_path"] == "/"
    assert payload["total_files"] == 2
    assert payload["total_dirs"] == 5
    assert payload["truncated"] is False

    root = payload["nodes"][0]
    assert root["type"] == "volume"
    assert root["path"] == "/"
    assert [child["path"] for child in root["children"]] == [
        "/memory",
        "/artifacts",
        "/buffers",
        "/meta",
    ]
    assert root["children"][0]["modified_at"] == "2024-01-01T00:00:00+00:00"
    assert root["children"][1]["children"][0]["path"] == "/artifacts/docs"
    assert root["children"][1]["children"][1]["path"] == "/artifacts/hello.txt"
    assert root["children"][1]["children"][0]["children"][0]["path"] == (
        "/artifacts/docs/notes.md"
    )


def test_list_daytona_volume_tree_rejects_path_traversal() -> None:
    with pytest.raises(ValueError, match="Path traversal not allowed"):
        list_daytona_volume_tree("tenant-a", root_path="/../etc")


def test_read_daytona_volume_file_text_uses_native_fs_download(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class _FakeFs:
        def download_file(self, path: str) -> bytes:
            calls.append(path)
            return b"abcdefghij"

    @asynccontextmanager
    async def _fake_mounted_daytona_volume(volume_name: str):
        assert volume_name == "tenant-a"
        yield SimpleNamespace(fs=_FakeFs())

    monkeypatch.setattr(
        "fleet_rlm.integrations.providers.daytona.volumes._amounted_daytona_volume",
        _fake_mounted_daytona_volume,
    )

    payload = read_daytona_volume_file_text(
        "tenant-a",
        "/artifacts/docs/readme.txt",
        max_bytes=6,
    )

    assert calls == ["/home/daytona/memory/artifacts/docs/readme.txt"]
    assert payload == {
        "path": "/artifacts/docs/readme.txt",
        "mime": "text/plain",
        "size": 10,
        "content": "abcdef",
        "truncated": True,
    }


def test_read_daytona_volume_file_text_rejects_path_traversal() -> None:
    with pytest.raises(ValueError, match="Path traversal not allowed"):
        read_daytona_volume_file_text("tenant-a", "/../etc/passwd")


def test_read_daytona_volume_file_text_preserves_native_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeFs:
        def download_file(self, path: str) -> bytes:
            _ = path
            raise RuntimeError("Is a directory")

    @asynccontextmanager
    async def _fake_mounted_daytona_volume(volume_name: str):
        assert volume_name == "tenant-a"
        yield SimpleNamespace(fs=_FakeFs())

    monkeypatch.setattr(
        "fleet_rlm.integrations.providers.daytona.volumes._amounted_daytona_volume",
        _fake_mounted_daytona_volume,
    )

    with pytest.raises(RuntimeError, match="Is a directory"):
        read_daytona_volume_file_text("tenant-a", "/artifacts/docs")
