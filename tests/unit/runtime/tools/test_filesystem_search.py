"""Regression tests for host and Daytona filesystem search tools."""

from __future__ import annotations

import shutil
from typing import Any

import pytest


def test_find_files_rg_cli_fallback_finds_matches(tmp_path) -> None:
    if shutil.which("rg") is None:
        pytest.skip("ripgrep CLI is not available")

    from fleet_rlm.runtime.tools.filesystem import _find_files_with_rg_cli

    target = tmp_path / "sample.py"
    target.write_text("alpha = 'sandbox budget session'\n", encoding="utf-8")

    result = _find_files_with_rg_cli(
        pattern="sandbox.*session",
        path=str(tmp_path),
        include="*.py",
    )

    assert result["status"] == "ok"
    assert result["count"] == 1
    assert result["hits"][0]["line"] == 1
    assert "sandbox budget session" in result["hits"][0]["text"]


def test_sandbox_find_in_files_rebinds_before_sdk_call() -> None:
    from fleet_rlm.runtime.tools.sandbox_filesystem import (
        _sandbox_find_in_files_impl,
        _SandboxFilesystemToolContext,
    )

    class _Fs:
        def __init__(self, session: Any) -> None:
            self.session = session

        def find_files(self, path: str, pattern: str) -> list[dict[str, Any]]:
            assert self.session.rebound is True
            return [{"file": path, "line": 7, "content": pattern}]

    class _Session:
        def __init__(self) -> None:
            self.rebound = False
            self.sandbox = type("_Sandbox", (), {})()
            self.sandbox.fs = _Fs(self)

        def _rebind_sandbox_if_needed(self) -> None:
            self.rebound = True

        _arebind_sandbox_if_needed = _rebind_sandbox_if_needed

        def _resolve_sandbox_path(self, path: str) -> str:
            return f"/workspace/{path}"

    session = _Session()
    interpreter = type("_Interpreter", (), {"_session": session})()
    ctx = _SandboxFilesystemToolContext(interpreter=interpreter)

    result = _sandbox_find_in_files_impl(ctx, path="src", pattern="budget")

    assert result == {
        "status": "ok",
        "path": "/workspace/src",
        "pattern": "budget",
        "count": 1,
        "hits": [{"file": "/workspace/src", "line": 7, "content": "budget"}],
    }


def test_sandbox_read_file_runs_async_session_accessor() -> None:
    from fleet_rlm.runtime.tools.sandbox_filesystem import (
        _sandbox_read_file_impl,
        _SandboxFilesystemToolContext,
    )

    class _Fs:
        def download_file(self, path: str) -> bytes:
            assert path == "/workspace/notes.txt"
            return b"hello"

    class _Session:
        def __init__(self) -> None:
            self.sandbox = type("_Sandbox", (), {})()
            self.sandbox.fs = _Fs()

        def _resolve_sandbox_path(self, path: str) -> str:
            return f"/workspace/{path}"

    class _Interpreter:
        _session = None

        async def aget_session(self) -> Any:
            return _Session()

    ctx = _SandboxFilesystemToolContext(interpreter=_Interpreter())

    result = _sandbox_read_file_impl(ctx, "notes.txt")

    assert result == {
        "status": "ok",
        "path": "/workspace/notes.txt",
        "content": "hello",
        "size": 5,
    }


def test_sandbox_read_file_resolves_async_sdk_download() -> None:
    from fleet_rlm.runtime.tools.sandbox_filesystem import (
        _sandbox_read_file_impl,
        _SandboxFilesystemToolContext,
    )

    class _Fs:
        async def download_file(self, path: str) -> bytes:
            assert path == "/workspace/notes.txt"
            return b"hello async"

    class _Session:
        def __init__(self) -> None:
            self.sandbox = type("_Sandbox", (), {})()
            self.sandbox.fs = _Fs()

        def _resolve_sandbox_path(self, path: str) -> str:
            return f"/workspace/{path}"

    ctx = _SandboxFilesystemToolContext(interpreter=type("_Interpreter", (), {"_session": _Session()})())

    result = _sandbox_read_file_impl(ctx, "notes.txt")

    assert result == {
        "status": "ok",
        "path": "/workspace/notes.txt",
        "content": "hello async",
        "size": 11,
    }


def test_sandbox_write_file_resolves_async_sdk_upload() -> None:
    from fleet_rlm.runtime.tools.sandbox_filesystem import (
        _sandbox_write_file_impl,
        _SandboxFilesystemToolContext,
    )

    uploads: list[tuple[bytes, str]] = []

    class _Fs:
        async def upload_file(self, data: bytes, path: str) -> None:
            uploads.append((data, path))

    class _Session:
        def __init__(self) -> None:
            self.sandbox = type("_Sandbox", (), {})()
            self.sandbox.fs = _Fs()

        def _resolve_sandbox_path(self, path: str) -> str:
            return f"/workspace/{path}"

    ctx = _SandboxFilesystemToolContext(interpreter=type("_Interpreter", (), {"_session": _Session()})())

    result = _sandbox_write_file_impl(ctx, "notes.txt", "hello")

    assert uploads == [(b"hello", "/workspace/notes.txt")]
    assert result == {
        "status": "ok",
        "path": "/workspace/notes.txt",
        "bytes_written": 5,
    }


def test_sandbox_replace_in_files_resolves_async_sdk_result() -> None:
    from fleet_rlm.runtime.tools.sandbox_filesystem import (
        _sandbox_replace_in_files_impl,
        _SandboxFilesystemToolContext,
    )

    calls: list[tuple[list[str], str, str]] = []

    class _Fs:
        async def replace_in_files(
            self,
            files: list[str],
            pattern: str,
            replacement: str,
        ) -> dict[str, Any]:
            calls.append((files, pattern, replacement))
            return {"updated": len(files)}

    class _Session:
        def __init__(self) -> None:
            self.sandbox = type("_Sandbox", (), {})()
            self.sandbox.fs = _Fs()

        def _resolve_sandbox_path(self, path: str) -> str:
            return f"/workspace/{path}"

        def _rebind_sandbox_if_needed(self) -> None:
            return None

    ctx = _SandboxFilesystemToolContext(interpreter=type("_Interpreter", (), {"_session": _Session()})())

    result = _sandbox_replace_in_files_impl(
        ctx,
        files=["a.txt", "b.txt"],
        pattern="old",
        replacement="new",
    )

    assert calls == [
        (
            [
                "/workspace/a.txt",
                "/workspace/b.txt",
            ],
            "old",
            "new",
        )
    ]
    assert result == {
        "status": "ok",
        "files": ["/workspace/a.txt", "/workspace/b.txt"],
        "pattern": "old",
        "result": {"updated": 2},
    }
