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
