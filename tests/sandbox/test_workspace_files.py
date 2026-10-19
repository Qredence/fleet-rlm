"""Workspace file transfer through Daytona's path overloads and the 25 MB provider limit."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from fleet_rlm.sandbox.manager import DaytonaSandboxManager


@pytest.mark.parametrize("case", ["file", "empty", "missing", "symlink", "parent_symlink", "oversized", "changed"])
def test_remote_stat_checks_size_digest_and_paths_without_downloads(monkeypatch, tmp_path, case):
    import contextlib
    import hashlib
    import io
    import os

    root = tmp_path / "workspace"
    (root / "files").mkdir(parents=True)
    target = root / "files/data.txt"
    raw = "selected café\n".encode() if case != "empty" else b""
    target.write_bytes(raw)
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"outside")
    if case == "missing":
        target.unlink()
    elif case == "symlink":
        target.unlink()
        target.symlink_to(outside)
    elif case == "parent_symlink":
        (root / "skills").symlink_to(tmp_path, target_is_directory=True)
    elif case == "oversized":
        with target.open("wb") as handle:
            handle.truncate(25_000_001)
    calls = []
    fstats = []

    def stat(descriptor):
        actual = os.fstat(descriptor)
        fstats.append(actual)
        if case == "changed" and len(fstats) == 2:
            return SimpleNamespace(st_mtime_ns=actual.st_mtime_ns + 1, st_ctime_ns=actual.st_ctime_ns)
        return actual

    remote_os = SimpleNamespace(**{**vars(os), "fstat": stat})

    def execute(_self, _sandbox, code):
        calls.append(code)
        out = io.StringIO()
        code = code.replace("import base64,hashlib,json,os", "import base64,hashlib,json")
        code = code.replace("os.path.realpath('/workspace')", f"os.path.realpath({str(root)!r})")
        with contextlib.redirect_stdout(out):
            exec(code, {"os": remote_os})
        return out.getvalue()

    manager = DaytonaSandboxManager("key", "org", "volume", "stat-test")
    # No file-transfer methods exist on this double: metadata must stay remote.
    monkeypatch.setattr(DaytonaSandboxManager, "ensure_sandbox", lambda _self: SimpleNamespace(fs=object()))
    monkeypatch.setattr(DaytonaSandboxManager, "_remote_exec", execute)
    if case in {"file", "empty"}:
        assert manager.stat_workspace_file("files/data.txt") == {
            "path": "files/data.txt",
            "size_bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
    elif case == "missing":
        with pytest.raises(FileNotFoundError):
            manager.stat_workspace_file("files/data.txt")
    else:
        message = "changed" if case == "changed" else "size limit" if case == "oversized" else "symbolic link"
        with pytest.raises(ValueError, match=message):
            manager.stat_workspace_file("skills/outside.txt" if case == "parent_symlink" else "files/data.txt")
    assert len(calls) == 1
    with pytest.raises(ValueError):
        manager.stat_workspace_file("../outside.txt")
    assert len(calls) == 1


@pytest.mark.parametrize(
    "metadata",
    [
        {"size": 1, "sha256": "invalid"},
        {"size": True, "sha256": "a" * 64},
        {"size": -1, "sha256": "a" * 64},
        {"size": None},
    ],
)
def test_remote_stat_rejects_invalid_metadata(monkeypatch, metadata):
    import json

    monkeypatch.setattr(DaytonaSandboxManager, "ensure_sandbox", lambda _self: object())
    monkeypatch.setattr(DaytonaSandboxManager, "_remote_exec", lambda *_args: json.dumps([metadata]))
    with pytest.raises(RuntimeError, match="Invalid workspace file metadata"):
        DaytonaSandboxManager("key", "org", "volume", "stat-test").stat_workspace_file("files/data.txt")


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


def test_delete_checks_the_path_remotely_before_using_the_file_api(monkeypatch) -> None:
    manager = DaytonaSandboxManager(api_key="key", org_id="org", volume_id="volume", session_id="session-a")
    calls: list[tuple[str, str]] = []
    sandbox = SimpleNamespace(fs=SimpleNamespace(delete_file=lambda path: calls.append(("delete", path))))
    monkeypatch.setattr(DaytonaSandboxManager, "ensure_sandbox", lambda _self: sandbox)

    def workspace_metadata(_self, _sandbox, path, **_kwargs):
        calls.append(("check", path))
        if path.endswith("link.txt"):
            raise ValueError("workspace target may not be a symbolic link")
        return {"size": 1}

    monkeypatch.setattr(DaytonaSandboxManager, "_workspace_file_metadata", workspace_metadata)
    manager.delete_workspace_file("files/a.txt")
    with pytest.raises(ValueError, match="symbolic link"):
        manager.delete_workspace_file("files/link.txt")
    with pytest.raises(ValueError):
        manager.delete_workspace_file("../escape")
    assert calls == [("check", "files/a.txt"), ("delete", "/workspace/files/a.txt"), ("check", "files/link.txt")]
