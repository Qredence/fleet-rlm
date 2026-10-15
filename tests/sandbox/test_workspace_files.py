"""Workspace file transfer through Daytona's path overloads and the 25 MB provider limit."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from fleet_rlm.sandbox.manager import DaytonaSandboxManager


def test_daytona_path_transfer_overloads_and_25mb_provider_limits(tmp_path, monkeypatch) -> None:
    manager = DaytonaSandboxManager(api_key="key", org_id="org", volume_id="volume", session_id="session-a")
    calls: list[tuple[str, ...]] = []

    class FileSystemDouble:
        def upload_file(self, source, destination, timeout):
            calls.append(("upload", source, destination, str(timeout)))

        def download_file(self, source, destination, timeout):
            calls.append(("download", source, destination, str(timeout)))
            Path(destination).write_bytes(b"data")

    sandbox = SimpleNamespace(fs=FileSystemDouble())
    monkeypatch.setattr(DaytonaSandboxManager, "ensure_sandbox", lambda _self: sandbox)

    def workspace_metadata(_self, _sandbox, _path, *, max_bytes=None, **_kwargs):
        if max_bytes is not None and max_bytes < 4:
            raise ValueError("workspace file exceeds size limit")
        return {"size": 4}

    monkeypatch.setattr(
        DaytonaSandboxManager,
        "_workspace_file_metadata",
        workspace_metadata,
    )
    local_source = tmp_path / "source"
    local_source.write_bytes(b"data")
    local_destination = tmp_path / "destination"

    assert manager.write_workspace_file_from_path("files/input.bin", local_source) == "/workspace/files/input.bin"
    assert manager.read_workspace_file_to_path("files/input.bin", local_destination) == 4
    assert calls == [
        ("upload", str(local_source), "/workspace/files/input.bin", "1800"),
        ("download", "/workspace/files/input.bin", str(local_destination), "1800"),
    ]
    with local_source.open("wb") as oversized_source:
        oversized_source.truncate(25_000_001)
    with pytest.raises(ValueError, match="25 MB limit"):
        manager.write_workspace_file_from_path("files/too-large.bin", local_source)
    with pytest.raises(ValueError, match="size limit"):
        manager.read_workspace_file_to_path("files/too-large.bin", local_destination, max_bytes=3)
    assert len(calls) == 2
