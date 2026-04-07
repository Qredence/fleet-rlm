from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from fleet_rlm.integrations.daytona.diagnostics import (
    DaytonaDiagnosticError,
    VolumeNotReadyError,
)
from fleet_rlm.integrations.daytona.runtime_helpers import (
    _await_volume_ready,
)
from fleet_rlm.integrations.daytona.volumes import (
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
        "fleet_rlm.integrations.daytona.volumes._amounted_daytona_volume",
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
        "fleet_rlm.integrations.daytona.volumes._amounted_daytona_volume",
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
        "fleet_rlm.integrations.daytona.volumes._amounted_daytona_volume",
        _fake_mounted_daytona_volume,
    )

    with pytest.raises(RuntimeError, match="Is a directory"):
        read_daytona_volume_file_text("tenant-a", "/artifacts/docs")


# ---------------------------------------------------------------------------
# _await_volume_ready tests
# ---------------------------------------------------------------------------


class _FakeVolumeClient:
    """Stub Daytona client whose ``volume.get`` returns canned states."""

    def __init__(self, states: list[str]) -> None:
        self._states = list(states)
        self._call_count = 0

    @property
    def volume(self) -> _FakeVolumeClient:
        return self

    def get(self, name: str) -> SimpleNamespace:
        self._call_count += 1
        state = self._states.pop(0) if self._states else "ready"
        return SimpleNamespace(id=f"vol-{name}", state=state)


def test_await_volume_ready_returns_immediately_when_ready() -> None:
    """When the volume is already ``ready``, no polling occurs."""
    volume = SimpleNamespace(id="vol-1", state="ready")
    client = _FakeVolumeClient([])

    result = asyncio.run(_await_volume_ready(client, "test-vol", volume))
    assert result is volume
    assert client._call_count == 0


def test_await_volume_ready_polls_until_ready() -> None:
    """Volume starts in ``creating`` and transitions to ``ready`` after two polls."""
    volume = SimpleNamespace(id="vol-1", state="creating")
    client = _FakeVolumeClient(["pending_create", "ready"])

    result = asyncio.run(_await_volume_ready(client, "test-vol", volume, timeout=30.0))
    assert result.state == "ready"
    assert client._call_count == 2


def test_await_volume_ready_timeout_raises_volume_not_ready_error() -> None:
    """When the volume never becomes ready, ``VolumeNotReadyError`` is raised."""
    volume = SimpleNamespace(id="vol-1", state="pending_create")
    client = _FakeVolumeClient(["pending_create"] * 50)

    with pytest.raises(VolumeNotReadyError, match="pending_create") as exc_info:
        asyncio.run(_await_volume_ready(client, "test-vol", volume, timeout=0.1))
    err = exc_info.value
    assert err.volume_name == "test-vol"
    assert err.volume_state == "pending_create"
    assert err.timeout_seconds == 0.1


def test_await_volume_ready_error_state_raises_diagnostic_error() -> None:
    """An error state raises ``DaytonaDiagnosticError`` immediately."""
    volume = SimpleNamespace(id="vol-1", state="error")
    client = _FakeVolumeClient([])

    with pytest.raises(DaytonaDiagnosticError, match="error state"):
        asyncio.run(_await_volume_ready(client, "test-vol", volume))


def test_await_volume_ready_error_during_polling_raises() -> None:
    """If volume transitions to ``failed`` during polling, error is raised."""
    volume = SimpleNamespace(id="vol-1", state="creating")
    client = _FakeVolumeClient(["pending_create", "failed"])

    with pytest.raises(DaytonaDiagnosticError, match="error state"):
        asyncio.run(_await_volume_ready(client, "test-vol", volume, timeout=30.0))
