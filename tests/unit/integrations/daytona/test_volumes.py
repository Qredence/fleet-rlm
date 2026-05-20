from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
from enum import Enum
from types import SimpleNamespace

import pytest

from fleet_rlm.integrations.daytona.diagnostics import (
    DaytonaDiagnosticError,
    VolumeNotReadyError,
)
from fleet_rlm.integrations.daytona.sdk_ops import (
    alist_daytona_volume_tree,
    aread_daytona_volume_file_text,
    list_daytona_volume_tree,
    read_daytona_volume_file_text,
)
from fleet_rlm.integrations.daytona.sdk_ops import (
    await_volume_ready as _await_volume_ready,
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

    @contextmanager
    def _fake_mounted_daytona_volume(volume_name: str):
        assert volume_name == "tenant-a"
        yield SimpleNamespace(fs=_FakeFs())

    monkeypatch.setattr(
        "fleet_rlm.integrations.daytona.sdk_ops._mounted_daytona_volume",
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
    assert root["children"][1]["children"][0]["children"][0]["path"] == ("/artifacts/docs/notes.md")


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

    @contextmanager
    def _fake_mounted_daytona_volume(volume_name: str):
        assert volume_name == "tenant-a"
        yield SimpleNamespace(fs=_FakeFs())

    monkeypatch.setattr(
        "fleet_rlm.integrations.daytona.sdk_ops._mounted_daytona_volume",
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

    @contextmanager
    def _fake_mounted_daytona_volume(volume_name: str):
        assert volume_name == "tenant-a"
        yield SimpleNamespace(fs=_FakeFs())

    monkeypatch.setattr(
        "fleet_rlm.integrations.daytona.sdk_ops._mounted_daytona_volume",
        _fake_mounted_daytona_volume,
    )

    with pytest.raises(RuntimeError, match="Is a directory"):
        read_daytona_volume_file_text("tenant-a", "/artifacts/docs")


def test_alist_daytona_volume_tree_runs_sync_impl_off_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str, int]] = []

    def _fake_list_daytona_volume_tree(
        volume_name: str,
        root_path: str = "/",
        max_depth: int = 4,
    ) -> dict[str, object]:
        calls.append((volume_name, root_path, max_depth))
        return {
            "volume_name": volume_name,
            "root_path": root_path,
            "nodes": [],
            "total_files": 0,
            "total_dirs": 0,
            "truncated": False,
        }

    monkeypatch.setattr(
        "fleet_rlm.integrations.daytona.sdk_ops.list_daytona_volume_tree",
        _fake_list_daytona_volume_tree,
    )

    payload = asyncio.run(alist_daytona_volume_tree("tenant-a", root_path="/docs", max_depth=2))

    assert calls == [("tenant-a", "/docs", 2)]
    assert payload["volume_name"] == "tenant-a"
    assert payload["root_path"] == "/docs"


def test_aread_daytona_volume_file_text_runs_sync_impl_off_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str, int]] = []

    def _fake_read_daytona_volume_file_text(
        volume_name: str,
        path: str,
        max_bytes: int = 200_000,
    ) -> dict[str, object]:
        calls.append((volume_name, path, max_bytes))
        return {
            "path": path,
            "mime": "text/plain",
            "size": 5,
            "content": "hello",
            "truncated": False,
        }

    monkeypatch.setattr(
        "fleet_rlm.integrations.daytona.sdk_ops.read_daytona_volume_file_text",
        _fake_read_daytona_volume_file_text,
    )

    payload = asyncio.run(aread_daytona_volume_file_text("tenant-a", "/docs/readme.txt", max_bytes=5))

    assert calls == [("tenant-a", "/docs/readme.txt", 5)]
    assert payload == {
        "path": "/docs/readme.txt",
        "mime": "text/plain",
        "size": 5,
        "content": "hello",
        "truncated": False,
    }


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


class _VolumeStateEnum(Enum):
    READY = "ready"


class _ValueOnlyState:
    value = "ready"

    def __str__(self) -> str:
        return "custom-value-state"


class _NameOnlyState:
    name = "READY"

    def __str__(self) -> str:
        return "custom-name-state"


def test_await_volume_ready_returns_immediately_when_ready() -> None:
    """When the volume is already ``ready``, no polling occurs."""
    volume = SimpleNamespace(id="vol-1", state="ready")
    client = _FakeVolumeClient([])

    result = _await_volume_ready(client, "test-vol", volume)
    assert result is volume
    assert client._call_count == 0


def test_await_volume_ready_accepts_id_only_handles_without_state() -> None:
    volume = SimpleNamespace(id="vol-1")
    client = _FakeVolumeClient([])

    result = _await_volume_ready(client, "test-vol", volume)
    assert result is volume
    assert client._call_count == 0


@pytest.mark.parametrize("state", ["VolumeState.READY", "volumestate.ready"])
def test_await_volume_ready_accepts_enum_like_ready_strings(state: str) -> None:
    volume = SimpleNamespace(id="vol-1", state=state)
    client = _FakeVolumeClient([])

    result = _await_volume_ready(client, "test-vol", volume)
    assert result is volume
    assert client._call_count == 0


def test_await_volume_ready_accepts_enum_value_objects() -> None:
    volume = SimpleNamespace(id="vol-1", state=_VolumeStateEnum.READY)
    client = _FakeVolumeClient([])

    result = _await_volume_ready(client, "test-vol", volume)
    assert result is volume
    assert client._call_count == 0


def test_await_volume_ready_accepts_value_only_objects() -> None:
    volume = SimpleNamespace(id="vol-1", state=_ValueOnlyState())
    client = _FakeVolumeClient([])

    result = _await_volume_ready(client, "test-vol", volume)
    assert result is volume
    assert client._call_count == 0


def test_await_volume_ready_accepts_name_only_objects() -> None:
    volume = SimpleNamespace(id="vol-1", state=_NameOnlyState())
    client = _FakeVolumeClient([])

    result = _await_volume_ready(client, "test-vol", volume)
    assert result is volume
    assert client._call_count == 0


def test_await_volume_ready_polls_until_ready() -> None:
    """Volume starts in ``creating`` and transitions to ``ready`` after two polls."""
    volume = SimpleNamespace(id="vol-1", state="creating")
    client = _FakeVolumeClient(["pending_create", "ready"])

    result = _await_volume_ready(client, "test-vol", volume, timeout=30.0)
    assert result.state == "ready"
    assert client._call_count == 2


def test_await_volume_ready_timeout_raises_volume_not_ready_error() -> None:
    """When the volume never becomes ready, ``VolumeNotReadyError`` is raised."""
    volume = SimpleNamespace(id="vol-1", state="pending_create")
    client = _FakeVolumeClient(["pending_create"] * 50)

    with pytest.raises(VolumeNotReadyError, match="pending_create") as exc_info:
        (_await_volume_ready(client, "test-vol", volume, timeout=0.1))
    err = exc_info.value
    assert err.volume_name == "test-vol"
    assert err.volume_state == "pending_create"
    assert err.raw_volume_state == "pending_create"
    assert err.timeout_seconds == 0.1


def test_await_volume_ready_timeout_error_includes_raw_and_normalized_states() -> None:
    volume = SimpleNamespace(id="vol-1", state="VolumeState.PENDING_CREATE")
    client = _FakeVolumeClient(["VolumeState.PENDING_CREATE"] * 50)

    with pytest.raises(VolumeNotReadyError, match="raw='VolumeState.PENDING_CREATE'"):
        (_await_volume_ready(client, "test-vol", volume, timeout=0.1))


def test_await_volume_ready_error_state_raises_diagnostic_error() -> None:
    """An error state raises ``DaytonaDiagnosticError`` immediately."""
    volume = SimpleNamespace(id="vol-1", state="error")
    client = _FakeVolumeClient([])

    with pytest.raises(DaytonaDiagnosticError, match="error state"):
        (_await_volume_ready(client, "test-vol", volume))


def test_await_volume_ready_error_during_polling_raises() -> None:
    """If volume transitions to ``failed`` during polling, error is raised."""
    volume = SimpleNamespace(id="vol-1", state="creating")
    client = _FakeVolumeClient(["pending_create", "failed"])

    with pytest.raises(DaytonaDiagnosticError, match="error state"):
        (_await_volume_ready(client, "test-vol", volume, timeout=30.0))
