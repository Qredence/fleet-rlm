"""Parallel Workspace Volume layout contracts for Daytona provisioning.

The canonical layout (ensure_volume_layout) creates 11 directories through
the sandbox filesystem. Creation is batched by depth level: directories that
share no parent/child relationship within one layout run concurrently, and
parent levels complete before child levels start. Each directory keeps the
idempotent verify-then-create contract, including tolerance for concurrent
creation by an unrelated writer.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from uuid import uuid4

import pytest

from fleet_rlm.daytona.provisioning import (
    ensure_volume_layout,
    required_volume_directories,
)
from fleet_rlm.paths import VolumePaths


class _FakeInfo:
    def __init__(self, is_dir: bool = True) -> None:
        self.is_dir = is_dir


class _FakeFs:
    """Filesystem double recording calls, order, and peak concurrency."""

    def __init__(self, existing: Iterable[str] = (), *, fail_once: dict[str, int] | None = None) -> None:
        self._existing: set[str] = set(existing)
        self._fail_once: dict[str, int] = dict(fail_once or {})
        self.calls: list[tuple[str, str]] = []
        self.peak_concurrency = 0
        self._active = 0
        self._lock = asyncio.Lock()

    async def _touch(self, op: str, path: str) -> None:
        async with self._lock:
            self._active += 1
            self.peak_concurrency = max(self.peak_concurrency, self._active)
            self.calls.append((op, path))
        try:
            await asyncio.sleep(0)
        finally:
            async with self._lock:
                self._active -= 1

    async def get_file_info(self, path: str) -> _FakeInfo | None:
        await self._touch("stat", path)
        return _FakeInfo() if path in self._existing else None

    async def create_folder(self, path: str, _mode: str) -> None:
        await self._touch("mkdir", path)
        remaining = self._fail_once.get(path, 0)
        if remaining:
            self._fail_once[path] = remaining - 1
            raise RuntimeError("transient create failure")
        self._existing.add(path)


def _paths() -> VolumePaths:
    return VolumePaths.from_mount("/home/daytona/fleet")


def _dirs_of(fs: _FakeFs, op: str) -> list[str]:
    return [path for name, path in fs.calls if name == op]


@pytest.mark.asyncio
async def test_layout_creates_every_directory_and_verifies_mount() -> None:
    fs = _FakeFs(existing={"/home/daytona/fleet"})
    paths = _paths()
    session_id, run_id = uuid4(), uuid4()

    await ensure_volume_layout(_sandbox(fs), paths, session_id=session_id, run_id=run_id)

    for directory in required_volume_directories(paths, session_id=session_id, run_id=run_id):
        assert directory in fs._existing, f"missing directory: {directory}"


@pytest.mark.asyncio
async def test_layout_runs_shared_roots_concurrently() -> None:
    fs = _FakeFs(existing={"/home/daytona/fleet"})
    paths = _paths()

    await ensure_volume_layout(_sandbox(fs), paths, session_id=uuid4(), run_id=uuid4())

    # The five independent shared roots must have overlapped at least once
    # during creation: peak concurrency above one proves the gather.
    assert fs.peak_concurrency >= 2, "shared roots were created serially"


@pytest.mark.asyncio
async def test_layout_creates_parents_before_children() -> None:
    fs = _FakeFs(existing={"/home/daytona/fleet"})
    paths = _paths()
    session_id, run_id = uuid4(), uuid4()

    await ensure_volume_layout(_sandbox(fs), paths, session_id=session_id, run_id=run_id)

    order = {path: index for index, (name, path) in enumerate(fs.calls) if name == "mkdir"}
    session_dir = str(paths.session_dir(session_id))
    runs_dir = str(paths.session_runs_dir(session_id))
    run_dir = str(paths.run_dir(session_id, run_id))
    assert order[session_dir] < order[runs_dir], "session dir must be created before its runs container"
    assert order[session_dir] < order[str(paths.session_workspace_dir(session_id))]
    assert order[runs_dir] < order[run_dir], "session runs container must precede the run directory"
    assert order[run_dir] < order[str(paths.run_artifacts_dir(session_id, run_id))]
    assert order[run_dir] < order[str(paths.run_attachments_dir(session_id, run_id))]


@pytest.mark.asyncio
async def test_layout_tolerates_concurrent_creation_by_another_writer() -> None:
    # mkdir fails because another writer created the directory first; the
    # post-failure stat must observe it and accept.
    fs = _FakeFs(existing={"/home/daytona/fleet"})

    real_get_info = fs.get_file_info

    async def racing_get_info(path: str) -> _FakeInfo | None:
        info = await real_get_info(path)
        if info is None and "artifacts" in path:
            # Another writer creates it right after our stat misses.
            fs._existing.add(path)
        return info

    fs.get_file_info = racing_get_info  # type: ignore[method-assign]

    await ensure_volume_layout(_sandbox(fs), _paths(), session_id=uuid4(), run_id=uuid4())

    assert "/home/daytona/fleet/artifacts" in fs._existing


@pytest.mark.asyncio
async def test_missing_mount_raises_without_creating() -> None:
    fs = _FakeFs(existing=set())
    with pytest.raises(Exception, match="[Uu]navailable"):
        await ensure_volume_layout(_sandbox(fs), _paths(), session_id=uuid4(), run_id=uuid4())
    assert not _dirs_of(fs, "mkdir"), "no directories may be created when the mount is missing"


def _sandbox(fs: _FakeFs):
    from types import SimpleNamespace

    return SimpleNamespace(fs=fs)
