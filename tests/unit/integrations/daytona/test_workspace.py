"""Tests for workspace.py context-path staging helpers."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from fleet_rlm.integrations.daytona.diagnostics import DaytonaDiagnosticError
import fleet_rlm.integrations.daytona.workspace as workspace_module
from fleet_rlm.integrations.daytona.workspace import _astage_context_paths


# ---------------------------------------------------------------------------
# Minimal fake sandbox that satisfies _astage_context_paths
# ---------------------------------------------------------------------------


class _FakeFs:
    def __init__(self) -> None:
        self.created_folders: list[str] = []
        self.uploaded: dict[str, bytes] = {}

    async def create_folder(self, path: str, mode: str) -> None:
        self.created_folders.append(path)

    async def upload_file(self, content: bytes, path: str) -> None:
        self.uploaded[path] = content


def _make_sandbox() -> SimpleNamespace:
    return SimpleNamespace(fs=_FakeFs())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_stage_context_paths_raises_for_nonexistent_path(tmp_path) -> None:
    """A nonexistent local path should abort staging with a diagnostic error."""
    sandbox = _make_sandbox()
    missing_path = tmp_path / "missing.txt"

    with pytest.raises(DaytonaDiagnosticError, match="Context path does not exist"):
        asyncio.run(
            _astage_context_paths(
                sandbox=sandbox,
                workspace_path="/workspace/ws",
                context_paths=[str(missing_path)],
            )
        )
    assert sandbox.fs.uploaded == {}


def test_stage_context_paths_skips_url_paths() -> None:
    """URL-form context paths (http/https) are silently filtered before staging."""
    sandbox = _make_sandbox()
    result = asyncio.run(
        _astage_context_paths(
            sandbox=sandbox,
            workspace_path="/workspace/ws",
            context_paths=["http://localhost:3000/health", "https://example.com/doc"],
        )
    )
    assert result == []


def test_stage_context_paths_wraps_unexpected_resolution_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unexpected resolver failures should surface as DaytonaDiagnosticError."""

    def _boom(_path: str):
        raise RuntimeError("boom")

    monkeypatch.setattr(workspace_module, "_resolve_local_context_path", _boom)
    sandbox = _make_sandbox()

    with pytest.raises(
        DaytonaDiagnosticError,
        match="Failed to stage context path 'bad-path': boom",
    ) as exc_info:
        asyncio.run(
            _astage_context_paths(
                sandbox=sandbox,
                workspace_path="/workspace/ws",
                context_paths=["bad-path"],
            )
        )

    assert exc_info.value.category == "context_stage_error"
    assert exc_info.value.phase == "context_stage"
    assert isinstance(exc_info.value.__cause__, RuntimeError)


def test_stage_context_paths_empty_returns_empty() -> None:
    sandbox = _make_sandbox()
    result = asyncio.run(
        _astage_context_paths(
            sandbox=sandbox,
            workspace_path="/workspace/ws",
            context_paths=[],
        )
    )
    assert result == []


def test_stage_context_paths_stages_valid_file(tmp_path) -> None:
    """A valid local file should be staged and appear in the returned sources."""
    test_file = tmp_path / "doc.txt"
    test_file.write_text("hello context")

    sandbox = _make_sandbox()
    result = asyncio.run(
        _astage_context_paths(
            sandbox=sandbox,
            workspace_path="/workspace/ws",
            context_paths=[str(test_file)],
        )
    )
    assert len(result) == 1
    assert result[0].kind == "file"
    assert result[0].host_path == str(test_file)
