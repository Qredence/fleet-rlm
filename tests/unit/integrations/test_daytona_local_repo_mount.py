"""Tests for the local-repo mount into the Daytona sandbox."""

from __future__ import annotations

import io
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from fleet_rlm.integrations.daytona._repo import (
    _build_repo_tarball,
    amount_local_repo_tree,
)


class _FakeSandbox:
    """Captures fs.upload_file and process.exec calls."""

    def __init__(self) -> None:
        self.uploads: list[tuple[bytes, str]] = []
        self.execs: list[str] = []
        self.fs = SimpleNamespace(upload_file=self._upload)
        self.process = SimpleNamespace(exec=self._exec)

    def _upload(self, data: bytes, path: str) -> None:
        self.uploads.append((data, path))

    def _exec(self, cmd: str) -> SimpleNamespace:
        self.execs.append(cmd)
        return SimpleNamespace(exit_code=0, std_out="", std_err="")


def _make_repo(tmp_path: Path) -> Path:
    """Create a minimal project root in tmp_path."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    (tmp_path / "src" / "fleet_rlm" / "__init__.py").parent.mkdir(parents=True)
    (tmp_path / "src" / "fleet_rlm" / "__init__.py").write_text("VERSION = '0'\n")
    (tmp_path / "src" / "fleet_rlm" / "runtime.py").write_text("def f():\n    return 1\n")
    return tmp_path


def test_amount_local_repo_tree_uploads_and_extracts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    monkeypatch.chdir(repo)
    monkeypatch.delenv("FLEET_RLM_MOUNT_LOCAL_REPO", raising=False)

    sandbox = _FakeSandbox()
    mounted = amount_local_repo_tree(sandbox=sandbox, workspace_path="/ws/daytona-workspace")

    assert mounted is True
    # Tarball uploaded to the workspace.
    assert len(sandbox.uploads) == 1
    data, path = sandbox.uploads[0]
    assert path == "/ws/daytona-workspace/_repo.tar.gz"
    # tar extracted at the workspace root, then the tarball removed.
    assert any(
        "tar xzf" in c and "/ws/daytona-workspace/_repo.tar.gz" in c and "-C /ws/daytona-workspace" in c
        for c in sandbox.execs
    )
    assert any(c.startswith("rm -f") for c in sandbox.execs)
    # The tarball actually contains the source tree.
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        names = tar.getnames()
    assert any("src/fleet_rlm/__init__.py" in n for n in names), names


def test_build_repo_tarball_excludes_venv_and_pycache(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    (repo / ".venv" / "lib").mkdir(parents=True)
    (repo / ".venv" / "lib" / "junk.py").write_text("X = 1\n")
    (repo / "src" / "fleet_rlm" / "__pycache__").mkdir(parents=True)
    (repo / "src" / "fleet_rlm" / "__pycache__" / "runtime.cpython-313.pyc").write_text("bytecode")

    data = _build_repo_tarball(repo, paths=("src", "pyproject.toml"))
    assert data is not None
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        names = tar.getnames()
    assert not any(".venv" in n for n in names), f".venv should be excluded: {names}"
    assert not any("__pycache__" in n for n in names), f"__pycache__ should be excluded: {names}"
    assert any("src/fleet_rlm/runtime.py" in n for n in names)


def test_amount_local_repo_tree_skipped_when_not_project_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # tmp_path with no pyproject.toml / .git → not a project root.
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("FLEET_RLM_MOUNT_LOCAL_REPO", raising=False)

    sandbox = _FakeSandbox()
    mounted = amount_local_repo_tree(sandbox=sandbox, workspace_path="/ws")

    assert mounted is False
    assert sandbox.uploads == []
    assert sandbox.execs == []


def test_amount_local_repo_tree_disabled_by_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    monkeypatch.chdir(repo)
    monkeypatch.setenv("FLEET_RLM_MOUNT_LOCAL_REPO", "false")

    sandbox = _FakeSandbox()
    mounted = amount_local_repo_tree(sandbox=sandbox, workspace_path="/ws")

    assert mounted is False
    assert sandbox.uploads == []


def test_amount_local_repo_tree_never_raises_on_sandbox_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A sandbox error must not propagate — session creation must not break."""
    repo = _make_repo(tmp_path)
    monkeypatch.chdir(repo)
    monkeypatch.delenv("FLEET_RLM_MOUNT_LOCAL_REPO", raising=False)

    class _BrokenSandbox:
        fs = SimpleNamespace(upload_file=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        process = SimpleNamespace(exec=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))

    mounted = amount_local_repo_tree(sandbox=_BrokenSandbox(), workspace_path="/ws")
    assert mounted is False
