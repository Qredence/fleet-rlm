"""Cross-gateway Workspace write/append CAS characterization."""

from __future__ import annotations

import asyncio
import hashlib
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from fleet_rlm.daytona.workspace_fs import AsyncDaytonaSessionWorkspaceFS
from fleet_rlm.daytona.workspace_gateway import _DaytonaWorkspaceFileSession
from fleet_rlm.files.workspace_access import WorkspaceFileConflictError
from fleet_rlm.files.workspace_models import WorkspaceConflictError as ProviderWorkspaceConflictError
from fleet_rlm.files.workspace_models import WorkspaceEntry


class _MutationGate:
    """Ensure two provider-boundary mutation calls are scheduled together."""

    def __init__(self) -> None:
        self.started: list[str] = []
        self._barrier = asyncio.Event()

    async def wait(self, label: str) -> None:
        self.started.append(label)
        if len(self.started) >= 2:
            self._barrier.set()
        await self._barrier.wait()
        await asyncio.sleep(0.02)


@dataclass(slots=True)
class _SharedProvider:
    """In-memory provider double used only to make host TOCTOU deterministic."""

    files: dict[str, str]
    gate: _MutationGate

    async def stat(self, path: str, *_args: object, include_checksum: bool = False) -> WorkspaceEntry | None:
        del include_checksum
        content = self.files.get(path)
        if content is None:
            return None
        data = content.encode("utf-8")
        return WorkspaceEntry(
            path,
            "file",
            len(data),
            "2026-01-01T00:00:00+00:00",
            hashlib.sha256(data).hexdigest(),
        )

    async def read_text(self, path: str, *, max_bytes: int) -> str:
        assert max_bytes > 0
        return self.files[path]

    async def write_text(
        self,
        path: str,
        content: str,
        *,
        overwrite: bool,
        expected_sha256: str | None = None,
    ) -> WorkspaceEntry:
        del overwrite
        await self.gate.wait(f"write:{content}")
        if expected_sha256 is not None:
            current = self.files.get(path)
            actual = hashlib.sha256(current.encode("utf-8")).hexdigest() if current is not None else None
            if actual != expected_sha256:
                raise WorkspaceFileConflictError("provider checksum mismatch")
        self.files[path] = content
        data = content.encode("utf-8")
        return WorkspaceEntry(path, "file", len(data), None, hashlib.sha256(data).hexdigest())

    async def append_text(
        self,
        path: str,
        content: str,
        *,
        expected_sha256: str | None = None,
    ) -> WorkspaceEntry:
        await self.gate.wait(f"append:{content}")
        if expected_sha256 is not None:
            current = self.files.get(path)
            actual = hashlib.sha256(current.encode("utf-8")).hexdigest() if current is not None else None
            if actual != expected_sha256:
                raise WorkspaceFileConflictError("provider checksum mismatch")
        final = self.files.get(path, "") + content
        self.files[path] = final
        data = final.encode("utf-8")
        return WorkspaceEntry(path, "file", len(data), None, hashlib.sha256(data).hexdigest())


def _sessions(provider: _SharedProvider) -> tuple[_DaytonaWorkspaceFileSession, _DaytonaWorkspaceFileSession]:
    return (
        _DaytonaWorkspaceFileSession(provider, max_file_bytes=1024),
        _DaytonaWorkspaceFileSession(provider, max_file_bytes=1024),
    )


@pytest.mark.asyncio
async def test_two_gateway_write_callers_cannot_share_one_stale_checksum() -> None:
    provider = _SharedProvider({"note.txt": "alpha"}, _MutationGate())
    writer_one, writer_two = _sessions(provider)
    checksum = hashlib.sha256(b"alpha").hexdigest()

    results = await asyncio.gather(
        writer_one.write_text("note.txt", "beta", overwrite=True, expected_sha256=checksum),
        writer_two.write_text("note.txt", "gamma", overwrite=True, expected_sha256=checksum),
        return_exceptions=True,
    )

    assert sum(not isinstance(result, Exception) for result in results) == 1
    assert sum(isinstance(result, WorkspaceFileConflictError) for result in results) == 1
    assert provider.files["note.txt"] in {"beta", "gamma"}


@pytest.mark.asyncio
async def test_two_gateway_append_callers_cannot_share_one_stale_checksum() -> None:
    provider = _SharedProvider({"note.txt": "alpha"}, _MutationGate())
    writer_one, writer_two = _sessions(provider)
    checksum = hashlib.sha256(b"alpha").hexdigest()

    results = await asyncio.gather(
        writer_one.append_text("note.txt", "-beta", expected_sha256=checksum),
        writer_two.append_text("note.txt", "-gamma", expected_sha256=checksum),
        return_exceptions=True,
    )

    assert sum(not isinstance(result, Exception) for result in results) == 1
    assert sum(isinstance(result, WorkspaceFileConflictError) for result in results) == 1
    assert provider.files["note.txt"] in {"alpha-beta", "alpha-gamma"}


class _SubprocessAgentProcess:
    """Execute the real generated Workspace agent as concurrent OS processes."""

    def __init__(self, *, mutation_delay: str) -> None:
        self.calls = 0
        self._barrier = asyncio.Event()
        self._mutation_delay = mutation_delay

    async def code_run(self, code: str, **_kwargs) -> SimpleNamespace:
        self.calls += 1
        if self.calls >= 2:
            self._barrier.set()
        await self._barrier.wait()
        delayed = code.replace(
            f"    if operation == {self._mutation_delay!r}:",
            f"    time.sleep(0.12)\n    if operation == {self._mutation_delay!r}:",
            1,
        )
        completed = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-c", delayed],
            check=False,
            capture_output=True,
            text=True,
        )
        return SimpleNamespace(exit_code=completed.returncode, result=completed.stdout)


class _DelayedDeleteBeforeUnlinkAgentProcess:
    """Sleep between guarded-delete checksum comparison and unlink."""

    def __init__(self, coordination_path: Path) -> None:
        self.calls = 0
        self._barrier = asyncio.Event()
        self._coordination_path = coordination_path

    async def code_run(self, code: str, **_kwargs) -> SimpleNamespace:
        self.calls += 1
        if self.calls >= 2:
            self._barrier.set()
        await self._barrier.wait()
        delayed = code
        gate = str(self._coordination_path)
        if "operation = 'delete'" in code:
            unlink_line = "                os.unlink(relative_parts[-1], dir_fd=parent_fd)"
            inserted = f"                open({gate!r}, 'w').close()\n                time.sleep(0.25)\n{unlink_line}"
            delayed = delayed.replace(unlink_line, inserted, 1)
        if "operation = 'write'" in code:
            branch = "    if operation == 'write':"
            waiter = f"    while not os.path.exists({gate!r}):\n        time.sleep(0.01)\n{branch}"
            delayed = delayed.replace(branch, waiter, 1)
        completed = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-c", delayed],
            check=False,
            capture_output=True,
            text=True,
        )
        return SimpleNamespace(exit_code=completed.returncode, result=completed.stdout)


@dataclass(slots=True)
class _SubprocessAgentSandbox:
    process: _SubprocessAgentProcess


def _subprocess_sessions(
    tmp_path: Path, *, operation: str | tuple[str, ...], process: object | None = None
) -> tuple[_DaytonaWorkspaceFileSession, _DaytonaWorkspaceFileSession]:
    volume_root = tmp_path / "volume"
    root = volume_root / "workspaces" / "shared" / "files"
    root.mkdir(parents=True)
    (root / "note.txt").write_text("alpha", encoding="utf-8")
    if process is None:
        process = _SubprocessAgentProcess(mutation_delay=operation)
    sandbox = _SubprocessAgentSandbox(process)
    workspace = AsyncDaytonaSessionWorkspaceFS(
        sandbox,
        volume_root=str(volume_root),
        root=str(root),
        max_file_bytes=1024,
    )
    return (
        _DaytonaWorkspaceFileSession(workspace, max_file_bytes=1024),
        _DaytonaWorkspaceFileSession(workspace, max_file_bytes=1024),
    )


@pytest.mark.asyncio
async def test_generated_write_agent_serializes_stale_checksum_across_processes(tmp_path: Path) -> None:
    writer_one, writer_two = _subprocess_sessions(tmp_path, operation="write")
    checksum = hashlib.sha256(b"alpha").hexdigest()

    results = await asyncio.gather(
        writer_one.write_text("note.txt", "beta", overwrite=True, expected_sha256=checksum),
        writer_two.write_text("note.txt", "gamma", overwrite=True, expected_sha256=checksum),
        return_exceptions=True,
    )

    assert sum(not isinstance(result, Exception) for result in results) == 1
    assert sum(isinstance(result, ProviderWorkspaceConflictError) for result in results) == 1
    assert (tmp_path / "volume" / "workspaces" / "shared" / "files" / "note.txt").read_text(encoding="utf-8") in {
        "beta",
        "gamma",
    }


@pytest.mark.asyncio
async def test_generated_append_agent_serializes_stale_checksum_across_processes(tmp_path: Path) -> None:
    writer_one, writer_two = _subprocess_sessions(tmp_path, operation="append")
    checksum = hashlib.sha256(b"alpha").hexdigest()

    results = await asyncio.gather(
        writer_one.append_text("note.txt", "-beta", expected_sha256=checksum),
        writer_two.append_text("note.txt", "-gamma", expected_sha256=checksum),
        return_exceptions=True,
    )

    assert sum(not isinstance(result, Exception) for result in results) == 1
    assert sum(isinstance(result, ProviderWorkspaceConflictError) for result in results) == 1
    assert (tmp_path / "volume" / "workspaces" / "shared" / "files" / "note.txt").read_text(encoding="utf-8") in {
        "alpha-beta",
        "alpha-gamma",
    }


@pytest.mark.asyncio
async def test_generated_delete_agent_cannot_remove_a_newer_revision_after_a_stale_checksum(
    tmp_path: Path,
) -> None:
    first, second = _subprocess_sessions(
        tmp_path,
        operation=("delete", "write"),
        process=_DelayedDeleteBeforeUnlinkAgentProcess(tmp_path / "delete-compared"),
    )
    checksum = hashlib.sha256(b"alpha").hexdigest()

    results = await asyncio.gather(
        first.delete_path("note.txt", expected_sha256=checksum),
        second.write_text("note.txt", "beta", overwrite=True, expected_sha256=checksum),
        return_exceptions=True,
    )

    assert sum(not isinstance(result, Exception) for result in results) == 1
    assert sum(isinstance(result, ProviderWorkspaceConflictError) for result in results) == 1
    note = tmp_path / "volume" / "workspaces" / "shared" / "files" / "note.txt"
    if isinstance(results[1], Exception):
        assert not note.exists()
    else:
        assert note.read_text(encoding="utf-8") == "beta"


@pytest.mark.asyncio
async def test_generated_delete_agent_allows_only_one_owner_of_one_revision(tmp_path: Path) -> None:
    first, second = _subprocess_sessions(tmp_path, operation=("delete", "delete"))
    checksum = hashlib.sha256(b"alpha").hexdigest()

    results = await asyncio.gather(
        first.delete_path("note.txt", expected_sha256=checksum),
        second.delete_path("note.txt", expected_sha256=checksum),
        return_exceptions=True,
    )

    assert sum(not isinstance(result, Exception) for result in results) == 1
    assert sum(isinstance(result, FileNotFoundError) for result in results) == 1
    assert not (tmp_path / "volume" / "workspaces" / "shared" / "files" / "note.txt").exists()
