"""impl-07: Volume mount defaults and safe path layout (no live Daytona)."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from uuid import uuid4

import pytest

from fleet_rlm.daytona.runtime import (
    VolumeConfig,
    get_or_create_volume_id,
)
from fleet_rlm.paths import (
    UnsafePathError,
    VolumePaths,
    resolve_under_root,
    validate_mount_path,
    validate_path_id,
)


def test_removed_volume_namespaces_have_no_production_references() -> None:
    source_root = Path(__file__).parents[4] / "src" / "fleet_rlm"
    forbidden = (
        "skills_root",
        "memory_root",
        "session_exports_dir",
        "session_staging_dir",
        "run_staging_dir",
        "_ensure_skill_tree",
    )
    references = [
        f"{path}:{term}" for path in source_root.rglob("*.py") for term in forbidden if term in path.read_text()
    ]
    assert references == []


def test_validate_mount_path_rejects_unsafe() -> None:
    with pytest.raises(UnsafePathError):
        validate_mount_path("/")
    with pytest.raises(UnsafePathError):
        validate_mount_path("relative/path")
    with pytest.raises(UnsafePathError):
        validate_mount_path("/home/daytona/../etc")
    with pytest.raises(UnsafePathError):
        validate_mount_path("/etc/fleet")
    with pytest.raises(UnsafePathError):
        validate_mount_path("/home//daytona/fleet")
    with pytest.raises(UnsafePathError):
        validate_mount_path("")


def test_validate_path_id_rejects_traversal_and_separators() -> None:
    with pytest.raises(UnsafePathError):
        validate_path_id("../etc")
    with pytest.raises(UnsafePathError):
        validate_path_id("a/b")
    with pytest.raises(UnsafePathError):
        validate_path_id("not-a-uuid")
    with pytest.raises(UnsafePathError):
        validate_path_id("")
    with pytest.raises(UnsafePathError):
        validate_path_id("abc\x00def")
    sid = uuid4()
    assert validate_path_id(sid) == str(sid)
    assert validate_path_id(str(sid)) == str(sid)


def test_session_workspace_root_is_session_scoped() -> None:
    paths = VolumePaths.from_mount()
    session_id = uuid4()

    workspace = paths.session_workspace_dir(session_id)

    assert workspace == paths.session_dir(session_id) / "workspace"


def test_session_and_run_container_paths_are_canonical() -> None:
    paths = VolumePaths.from_mount()
    session_id = uuid4()
    run_id = uuid4()

    assert paths.session_runs_dir(session_id) == paths.session_dir(session_id) / "runs"
    assert paths.run_attachments_dir(session_id, run_id) == paths.run_dir(session_id, run_id) / "attachments"


def test_resolve_under_root_rejects_escape() -> None:
    root = PurePosixPath("/home/daytona/fleet")
    with pytest.raises(UnsafePathError):
        resolve_under_root(root, "..")
    with pytest.raises(UnsafePathError):
        resolve_under_root(root, "sessions", "../x")
    ok = resolve_under_root(root, "sessions", str(uuid4()))
    assert str(ok).startswith("/home/daytona/fleet/sessions/")


@pytest.mark.asyncio
async def test_get_or_create_volume_id_uses_injected_client() -> None:
    class _Vol:
        id = "vid-1"

    class _Client:
        def __init__(self) -> None:
            self.calls: list[tuple[str, bool]] = []

        async def get(self, name: str, *, create: bool = False) -> _Vol:
            self.calls.append((name, create))
            return _Vol()

    client = _Client()
    vid = await get_or_create_volume_id(client, VolumeConfig(name="my-vol"))
    assert vid == "vid-1"
    assert client.calls == [("my-vol", True)]


@pytest.mark.asyncio
async def test_get_or_create_volume_id_recovers_concurrent_create_conflict() -> None:
    from daytona.common.errors import DaytonaConflictError

    class _Client:
        def __init__(self) -> None:
            self.calls: list[bool] = []

        async def get(self, name: str, *, create: bool = False) -> object:
            assert name == "my-vol"
            self.calls.append(create)
            if create:
                raise DaytonaConflictError("Volume already exists", status_code=409)
            return type("Volume", (), {"id": "vid-1"})()

    client = _Client()
    assert await get_or_create_volume_id(client, VolumeConfig(name="my-vol")) == "vid-1"
    assert client.calls == [True, False]


# --- Parallel Workspace Volume Layout Contracts ---
class _FakeInfo:
    def __init__(self, is_dir: bool = True) -> None:
        self.is_dir = is_dir


class _FakeFs:
    def __init__(self, existing: Iterable[str] = (), *, fail_once: dict[str, int] | None = None) -> None:
        import asyncio

        self._existing: set[str] = set(existing)
        self._fail_once: dict[str, int] = dict(fail_once or {})
        self.calls: list[tuple[str, str]] = []
        self.peak_concurrency = 0
        self._active = 0
        self._lock = asyncio.Lock()

    async def _touch(self, op: str, path: str) -> None:
        import asyncio

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


def _dirs_of(fs: _FakeFs, op: str) -> list[str]:
    return [path for name, path in fs.calls if name == op]


def _sandbox(fs: _FakeFs):
    from types import SimpleNamespace

    return SimpleNamespace(fs=fs)


@pytest.mark.asyncio
async def test_execution_mount_must_be_visible_to_python() -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from fleet_rlm.daytona.errors import DaytonaAdapterError
    from fleet_rlm.daytona.runtime import (
        SESSION_WORKSPACE_MOUNT_PATH,
        verify_execution_mount,
    )

    process = SimpleNamespace(exec=AsyncMock(return_value=SimpleNamespace(exit_code=2)))
    with pytest.raises(DaytonaAdapterError) as error:
        await verify_execution_mount(SimpleNamespace(process=process))
    assert error.value.cause_type == "ExecutionMountNotVisible"
    process.exec.assert_awaited_once()
    assert SESSION_WORKSPACE_MOUNT_PATH in process.exec.await_args.args[0]

    process.exec.return_value = SimpleNamespace(exit_code=0)
    await verify_execution_mount(SimpleNamespace(process=process))


@pytest.mark.asyncio
async def test_layout_creates_every_directory_and_verifies_mount() -> None:

    from fleet_rlm.daytona.runtime import (
        ensure_volume_layout,
        required_volume_directories,
    )

    fs = _FakeFs(existing={"/home/daytona/fleet"})
    paths = VolumePaths.from_mount("/home/daytona/fleet")
    session_id, run_id = uuid4(), uuid4()

    await ensure_volume_layout(_sandbox(fs), paths, session_id=session_id, run_id=run_id)

    for directory in required_volume_directories(paths, session_id=session_id, run_id=run_id):
        assert directory in fs._existing, f"missing directory: {directory}"


@pytest.mark.asyncio
async def test_layout_runs_shared_roots_concurrently() -> None:
    from fleet_rlm.daytona.runtime import ensure_volume_layout

    fs = _FakeFs(existing={"/home/daytona/fleet"})
    paths = VolumePaths.from_mount("/home/daytona/fleet")

    await ensure_volume_layout(_sandbox(fs), paths, session_id=uuid4(), run_id=uuid4())

    assert fs.peak_concurrency >= 2, "shared roots were created serially"


@pytest.mark.asyncio
async def test_layout_creates_parents_before_children() -> None:
    from fleet_rlm.daytona.runtime import ensure_volume_layout

    fs = _FakeFs(existing={"/home/daytona/fleet"})
    paths = VolumePaths.from_mount("/home/daytona/fleet")
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
    from fleet_rlm.daytona.runtime import ensure_volume_layout

    artifact_root = "/home/daytona/fleet/artifacts"
    fs = _FakeFs(existing={"/home/daytona/fleet"}, fail_once={artifact_root: 1})

    real_create_folder = fs.create_folder

    async def racing_create_folder(path: str, mode: str) -> None:
        try:
            await real_create_folder(path, mode)
        except RuntimeError:
            fs._existing.add(path)
            raise

    fs.create_folder = racing_create_folder  # type: ignore[method-assign]

    paths = VolumePaths.from_mount("/home/daytona/fleet")
    await ensure_volume_layout(_sandbox(fs), paths, session_id=uuid4(), run_id=uuid4())

    assert artifact_root in fs._existing
    assert _dirs_of(fs, "mkdir").count(artifact_root) == 1
    assert _dirs_of(fs, "stat").count(artifact_root) == 1


@pytest.mark.asyncio
async def test_missing_mount_raises_without_creating() -> None:
    from fleet_rlm.daytona.runtime import ensure_volume_layout

    fs = _FakeFs(existing=set())
    paths = VolumePaths.from_mount("/home/daytona/fleet")
    with pytest.raises(Exception, match=r"[Uu]navailable"):
        await ensure_volume_layout(_sandbox(fs), paths, session_id=uuid4(), run_id=uuid4())
    assert not _dirs_of(fs, "mkdir"), "no directories may be created when the mount is missing"
