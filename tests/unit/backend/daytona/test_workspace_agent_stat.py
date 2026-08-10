"""Workspace-agent ``stat`` checksum capability (RC-5)."""

from __future__ import annotations

import hashlib
from contextlib import redirect_stdout, suppress
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest


class LocalProcess:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def code_run(self, code: str):
        self.calls.append(code)
        output = StringIO()
        with redirect_stdout(output), suppress(SystemExit):
            exec(code, {})
        return SimpleNamespace(exit_code=0, result=output.getvalue().strip())


def _layout(tmp_path: Path) -> tuple[Path, Path]:
    volume_root = tmp_path / "volume"
    root = volume_root / "sessions" / "session" / "workspace"
    root.mkdir(parents=True, exist_ok=True)
    return volume_root, root


def _run_stat(tmp_path: Path, relative: str, **overrides: object) -> tuple[dict[str, object], LocalProcess]:
    from fleet_rlm.daytona.workspace_agent import run_workspace_agent

    volume_root, root = _layout(tmp_path)
    process = LocalProcess()
    arguments = {
        "volume_root": str(volume_root),
        "root": str(root),
        "operation": "stat",
        "relative": relative,
        "allow_missing": True,
        "max_bytes": 0,
        "limit": 0,
        "overwrite": False,
        "content_b64": "",
        **overrides,
    }
    return run_workspace_agent(SimpleNamespace(process=process), **arguments), process


def test_stat_checksum_flag_is_repr_embedded_like_other_operation_params() -> None:
    from fleet_rlm.daytona.workspace_agent import build_workspace_agent_code

    base = {
        "volume_root": "/home/daytona/fleet",
        "root": "/home/daytona/fleet/sessions/s/workspace",
        "operation": "stat",
        "relative": "note.txt",
        "allow_missing": True,
        "max_bytes": 0,
        "limit": 0,
        "overwrite": False,
        "content_b64": "",
    }

    assert "checksum = False" in build_workspace_agent_code(**base)
    assert "checksum = True" in build_workspace_agent_code(**base, checksum=True)
    assert "import base64, datetime, errno, fcntl, hashlib, json, os, re, stat, time" in build_workspace_agent_code(
        **base
    )


def test_stat_with_checksum_hashes_regular_file_bytes(tmp_path: Path) -> None:
    _volume_root, root = _layout(tmp_path)
    content = b"checksum me \xf0\x9f\x98\x80"
    (root / "note.txt").write_bytes(content)

    payload, process = _run_stat(tmp_path, "note.txt", checksum=True, max_bytes=1024)

    entry = payload["entry"]
    assert isinstance(entry, dict)
    assert entry["kind"] == "file"
    assert entry["checksum"] == hashlib.sha256(content).hexdigest()
    assert "hashlib.sha256()" in process.calls[0]


def test_stat_without_checksum_never_hashes(tmp_path: Path) -> None:
    _volume_root, root = _layout(tmp_path)
    (root / "note.txt").write_bytes(b"plain")

    payload, process = _run_stat(tmp_path, "note.txt")

    entry = payload["entry"]
    assert isinstance(entry, dict)
    assert "checksum" not in entry
    assert "checksum = False" in process.calls[0]


def test_stat_checksum_skips_directories_and_missing_entries(tmp_path: Path) -> None:
    _volume_root, root = _layout(tmp_path)
    (root / "notes").mkdir()

    directory = _run_stat(tmp_path, "notes", checksum=True, max_bytes=1024)[0]["entry"]
    assert isinstance(directory, dict)
    assert directory["kind"] == "directory"
    assert "checksum" not in directory

    root_entry = _run_stat(tmp_path, ".", checksum=True, max_bytes=1024)[0]["entry"]
    assert isinstance(root_entry, dict)
    assert root_entry["kind"] == "directory"
    assert "checksum" not in root_entry

    assert _run_stat(tmp_path, "missing.txt", checksum=True, max_bytes=1024)[0]["entry"] is None


def test_stat_checksum_rejects_files_beyond_max_bytes(tmp_path: Path) -> None:
    _volume_root, root = _layout(tmp_path)
    (root / "large.txt").write_bytes(b"x" * 33)

    with pytest.raises(ValueError, match="read bound"):
        _run_stat(tmp_path, "large.txt", checksum=True, max_bytes=32)


def test_stat_checksum_spans_chunked_reads(tmp_path: Path) -> None:
    _volume_root, root = _layout(tmp_path)
    content = b"y" * (1_048_576 + 17)
    (root / "big.txt").write_bytes(content)

    payload, _process = _run_stat(tmp_path, "big.txt", checksum=True, max_bytes=len(content))

    entry = payload["entry"]
    assert isinstance(entry, dict)
    assert entry["checksum"] == hashlib.sha256(content).hexdigest()


def test_sync_workspace_fs_stat_passthrough_exposes_checksum(tmp_path: Path) -> None:
    from fleet_rlm.daytona.workspace_fs import DaytonaSessionWorkspaceFS

    volume_root, root = _layout(tmp_path)
    content = b"via fs"
    (root / "note.txt").write_bytes(content)
    process = LocalProcess()
    workspace = DaytonaSessionWorkspaceFS(
        SimpleNamespace(process=process),
        volume_root=str(volume_root),
        root=str(root),
        max_file_bytes=1024,
    )

    entry = workspace.stat("note.txt", include_checksum=True)
    assert entry is not None
    assert entry.checksum_sha256 == hashlib.sha256(content).hexdigest()

    plain = workspace.stat("note.txt")
    assert plain is not None
    assert plain.checksum_sha256 is None
    assert "checksum = False" in process.calls[-1]
