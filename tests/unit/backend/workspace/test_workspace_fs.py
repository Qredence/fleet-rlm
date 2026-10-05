"""Daytona-backed Session Workspace filesystem."""

from __future__ import annotations

import errno
import os
from contextlib import redirect_stdout, suppress
from io import StringIO
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

import pytest

from fleet_rlm.paths import UnsafePathError, VolumePaths, validate_project_slug
from fleet_rlm.workspace.paths import normalize_workspace_path


class LocalProcess:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def code_run(self, code: str, **_kwargs):
        self.calls.append(code)
        output = StringIO()
        with redirect_stdout(output), suppress(SystemExit):
            exec(code, {})
        return SimpleNamespace(exit_code=0, result=output.getvalue().strip())


def _workspace(tmp_path: Path, *, max_file_bytes: int = 32, root_exists: bool = True):
    from fleet_rlm.workspace.storage import WorkspaceStorage

    volume_root = tmp_path / "volume"
    session_parent = volume_root / "sessions" / "session"
    root = session_parent / "workspace"
    if root_exists:
        root.mkdir(parents=True)
    else:
        session_parent.mkdir(parents=True)
    process = LocalProcess()
    sandbox = SimpleNamespace(process=process)
    workspace = WorkspaceStorage(
        sandbox,
        volume_root=str(volume_root),
        root=str(root),
        max_file_bytes=max_file_bytes,
    )
    return workspace, sandbox, root, process


def test_rejects_workspace_root_outside_trusted_volume() -> None:
    from fleet_rlm.workspace.storage import WorkspaceStorage

    with pytest.raises(ValueError, match="trusted volume"):
        WorkspaceStorage(
            SimpleNamespace(),
            volume_root="/home/daytona/fleet",
            root="/home/daytona/other/workspace",
            max_file_bytes=32,
        )


@pytest.mark.parametrize("reserved", ["attachments"])
def test_rejects_workspace_root_aliasing_managed_storage(reserved: str) -> None:
    from fleet_rlm.workspace.storage import WorkspaceStorage

    with pytest.raises(ValueError, match="attachment or artifact"):
        WorkspaceStorage(
            SimpleNamespace(),
            volume_root="/home/daytona/fleet",
            root=f"/home/daytona/fleet/{reserved}/session-file",
            max_file_bytes=32,
        )


def test_pages_utf8_text_from_a_direct_cursor(tmp_path: Path) -> None:
    workspace, _sandbox, root, _process = _workspace(tmp_path)
    (root / "notes.txt").write_text("éabcd", encoding="utf-8")

    first = workspace.read_text_page("notes.txt", cursor=None, max_chars=2, max_bytes=32)
    second = workspace.read_text_page("notes.txt", cursor=first.next_cursor, max_chars=2, max_bytes=32)
    third = workspace.read_text_page("notes.txt", cursor=second.next_cursor, max_chars=2, max_bytes=32)

    assert first.content == "éa"
    assert second.content == "bc"
    assert third.content == "d"
    assert first.byte_size == second.byte_size == third.byte_size == 6
    assert first.eof is False
    assert second.eof is False
    assert second.next_cursor is not None
    assert third.eof is True


def test_page_boundary_never_splits_a_multibyte_character(tmp_path: Path) -> None:
    workspace, _sandbox, root, _process = _workspace(tmp_path)
    (root / "unicode.txt").write_text("aaaaa😀b", encoding="utf-8")

    pages: list[str] = []
    cursor = None
    while True:
        page = workspace.read_text_page("unicode.txt", cursor=cursor, max_chars=1, max_bytes=32)
        pages.append(page.content)
        if page.eof:
            break
        cursor = page.next_cursor

    assert "".join(pages) == "aaaaa😀b"
    assert all(len(page) <= 1 for page in pages)


@pytest.mark.parametrize(
    "cursor_mutation",
    [lambda token: token[:-1] + "!"],
)
def test_rejects_invalid_or_path_bound_text_cursors(
    tmp_path: Path,
    cursor_mutation,
) -> None:
    workspace, _sandbox, root, _process = _workspace(tmp_path)
    (root / "notes.txt").write_text("hello", encoding="utf-8")
    (root / "other.txt").write_text("hello", encoding="utf-8")
    first = workspace.read_text_page("notes.txt", cursor=None, max_chars=1, max_bytes=32)
    assert first.next_cursor is not None

    with pytest.raises(ValueError, match="cursor"):
        workspace.read_text_page("notes.txt", cursor=cursor_mutation(first.next_cursor), max_chars=1, max_bytes=32)
    with pytest.raises(ValueError, match="cursor"):
        workspace.read_text_page("other.txt", cursor=first.next_cursor, max_chars=1, max_bytes=32)


def test_list_pages_are_lexicographic_even_when_provider_order_is_not(tmp_path: Path) -> None:
    workspace, _sandbox, root, _process = _workspace(tmp_path)
    for name in ("z.txt", "a.txt", "m.txt", "b.txt"):
        (root / name).write_text(name, encoding="utf-8")

    first = workspace.list_entries(".", limit=2)
    second = workspace.list_entries(".", limit=2, after=first.next_cursor)

    assert [entry.path for entry in first.entries] == ["a.txt", "b.txt"]
    assert first.next_cursor == "b.txt"
    assert [entry.path for entry in second.entries] == ["m.txt", "z.txt"]
    assert second.next_cursor is None

    with pytest.raises(ValueError, match="cursor"):
        workspace.list_entries("notes", limit=2, after="other.txt")


def test_append_rejects_symlink_targets(tmp_path: Path) -> None:
    workspace, _sandbox, root, _process = _workspace(tmp_path)
    secret = root / "secret.txt"
    secret.write_text("private", encoding="utf-8")
    alias = root / "alias.txt"
    alias.symlink_to(secret)

    with pytest.raises(ValueError, match="unsafe"):
        workspace.append_text("alias.txt", "x")
    assert secret.read_text(encoding="utf-8") == "private"


def test_list_truncation_limits_entries(tmp_path: Path) -> None:
    workspace, _sandbox, root, _process = _workspace(tmp_path)
    for index in range(5):
        (root / f"file-{index}.txt").write_text("x", encoding="utf-8")

    result = workspace.list_entries(".", limit=3)

    assert len(result.entries) == 3
    assert result.truncated is True


@pytest.mark.parametrize("replace_errno", [errno.EPERM, errno.ENOSYS, 38, 95])
def test_overwrite_falls_back_when_volume_rejects_atomic_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replace_errno: int,
) -> None:
    workspace, _sandbox, root, _process = _workspace(tmp_path)
    target = root / "date.txt"
    target.write_text("previous", encoding="utf-8")

    def unsupported_replace(*_args: object, **_kwargs: object) -> None:
        raise OSError(replace_errno, "rename unsupported")

    monkeypatch.setattr(os, "replace", unsupported_replace)

    workspace.write_text("date.txt", "verified", overwrite=True)

    assert target.read_text(encoding="utf-8") == "verified"
    assert workspace.last_warnings == ({"code": "non_atomic_overwrite"},)
    assert not list(root.glob(".fleet-write-*"))


def test_unrelated_atomic_replace_error_remains_unsupported_storage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, _sandbox, root, _process = _workspace(tmp_path)
    target = root / "date.txt"
    target.write_text("previous", encoding="utf-8")
    monkeypatch.setattr(
        os,
        "replace",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError(errno.EIO, "rename failed")),
    )

    from fleet_rlm.workspace.storage import WorkspaceStorageError

    with pytest.raises(WorkspaceStorageError):
        workspace.write_text("date.txt", "replacement", overwrite=True)

    assert target.read_text(encoding="utf-8") == "previous"


def test_workspace_mutation_leaves_attachment_and_artifact_siblings_untouched(tmp_path: Path) -> None:
    workspace, _sandbox, root, _process = _workspace(tmp_path)
    volume_root = root.parents[2]
    attachments = volume_root / "attachments"
    artifacts = volume_root / "artifacts"
    attachments.mkdir()
    artifacts.mkdir()
    (attachments / "input.txt").write_text("input", encoding="utf-8")
    (artifacts / "published.txt").write_text("published", encoding="utf-8")

    workspace.write_text("date.txt", "2026-07-20", overwrite=False)

    assert (root / "date.txt").read_text(encoding="utf-8") == "2026-07-20"
    assert (attachments / "input.txt").read_text(encoding="utf-8") == "input"
    assert (artifacts / "published.txt").read_text(encoding="utf-8") == "published"


def test_first_write_succeeds_when_volume_rejects_hard_links(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, _sandbox, root, _process = _workspace(tmp_path)

    def unsupported_link(*_args: object, **_kwargs: object) -> None:
        raise OSError(errno.EPERM, "hard links unsupported")

    monkeypatch.setattr(os, "link", unsupported_link)

    workspace.write_text("date.txt", "2026-07-19", overwrite=False)

    assert (root / "date.txt").read_text(encoding="utf-8") == "2026-07-19"


@pytest.mark.parametrize("error_number", [errno.EACCES, errno.EXDEV, errno.ENOSPC, errno.EIO])
def test_unrelated_link_errors_do_not_trigger_exclusive_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error_number: int,
) -> None:
    workspace, _sandbox, root, _process = _workspace(tmp_path)

    def rejected_link(*_args: object, **_kwargs: object) -> None:
        raise OSError(error_number, "link rejected")

    monkeypatch.setattr(os, "link", rejected_link)

    from fleet_rlm.workspace.storage import WorkspaceStorageError

    with pytest.raises(WorkspaceStorageError):
        workspace.write_text("date.txt", "2026-07-19", overwrite=False)
    assert not (root / "date.txt").exists()
    assert not list(root.glob(".fleet-write-*"))


def test_link_conflict_race_preserves_file_exists_error_and_cleans_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, _sandbox, root, _process = _workspace(tmp_path)

    def conflicting_link(*_args: object, **_kwargs: object) -> None:
        raise OSError(errno.EEXIST, "destination appeared during publication")

    monkeypatch.setattr(os, "link", conflicting_link)

    with pytest.raises(FileExistsError):
        workspace.write_text("date.txt", "2026-07-19", overwrite=False)
    assert not (root / "date.txt").exists()
    assert not list(root.glob(".fleet-write-*"))


def test_hard_link_publication_path_is_retained_on_capable_filesystem(tmp_path: Path) -> None:
    workspace, _sandbox, root, _process = _workspace(tmp_path)

    workspace.write_text("date.txt", "2026-07-19", overwrite=False)

    assert (root / "date.txt").read_text(encoding="utf-8") == "2026-07-19"


def test_partial_write_and_eintr_cleanup_destination(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace, _sandbox, root, _process = _workspace(tmp_path)
    original_write = os.write
    state = {"interrupted": False}

    def partial_write(fd: int, data: bytes) -> int:
        if not state["interrupted"]:
            state["interrupted"] = True
            raise InterruptedError
        return original_write(fd, data[:1])

    monkeypatch.setattr(os, "write", partial_write)
    workspace.write_text("date.txt", "partial", overwrite=False)

    assert (root / "date.txt").read_text(encoding="utf-8") == "partial"
    assert not list(root.glob(".fleet-write-*"))


def test_workspace_bounded_binary_read_is_exact_and_enforces_storage_limit(tmp_path: Path) -> None:
    workspace, _, root, _ = _workspace(tmp_path, max_file_bytes=8)
    target = root / "large.bin"
    target.write_bytes(b"12345678")

    assert workspace.read_file_bytes("large.bin", max_bytes=8) == b"12345678"
    with pytest.raises(ValueError, match="file read bound exceeded"):
        workspace.read_file_bytes("large.bin", max_bytes=7)

    target.write_bytes(b"123456789")
    with pytest.raises(ValueError, match="file read bound exceeded"):
        workspace.read_file_bytes("large.bin", max_bytes=16)


def test_enforces_write_and_read_byte_bounds_and_strict_utf8(tmp_path: Path) -> None:
    workspace, _sandbox, root, _process = _workspace(tmp_path, max_file_bytes=4)

    with pytest.raises(ValueError, match="size"):
        workspace.write_text("large.txt", "12345", overwrite=False)

    path = root / "invalid.txt"
    path.write_bytes(b"\xff")
    with pytest.raises(ValueError, match="UTF-8"):
        workspace.read_text_page("invalid.txt", cursor=None, max_chars=4, max_bytes=4)

    path.write_bytes(b"12345")
    with pytest.raises(ValueError, match="read bound"):
        workspace.read_text_page("invalid.txt", cursor=None, max_chars=4, max_bytes=4)


def test_atomic_write_rejects_symlink_target_before_io(tmp_path: Path) -> None:
    workspace, _sandbox, root, _process = _workspace(tmp_path)
    secret = root / "secret.txt"
    secret.write_text("private", encoding="utf-8")
    alias = root / "notes"
    alias.symlink_to(secret)

    with pytest.raises(ValueError, match="unsafe"):
        workspace.write_text("notes/decision.md", "private", overwrite=False)

    assert not (root / "notes" / "decision.md").exists()


def test_atomic_read_rejects_symlink_target(tmp_path: Path) -> None:
    workspace, _sandbox, root, _process = _workspace(tmp_path)
    secret = root / "secret.txt"
    secret.write_text("private", encoding="utf-8")
    alias = root / "note.txt"
    alias.symlink_to(secret)

    with pytest.raises(ValueError, match="unsafe"):
        workspace.read_text_page("note.txt", cursor=None, max_chars=32, max_bytes=32)


@pytest.mark.parametrize("link_kind", ["session_ancestor", "workspace_root", "descendant", "target"])
def test_provider_guard_rejects_symlinks_below_the_trusted_volume(
    tmp_path: Path,
    link_kind: str,
) -> None:
    from fleet_rlm.workspace.storage import WorkspaceStorage

    volume_root = tmp_path / "volume"
    sessions = volume_root / "sessions"
    session = sessions / "session"
    root = session / "workspace"
    root.mkdir(parents=True)
    inside = root / "inside"
    inside.mkdir(parents=True)
    target = inside / "decision.md"
    target.write_text("private", encoding="utf-8")
    if link_kind == "session_ancestor":
        actual_session = volume_root / "actual-session"
        actual_session.mkdir()
        actual_root = actual_session / "workspace"
        actual_root.mkdir()
        (actual_root / "decision.md").write_text("private", encoding="utf-8")
        session.rename(volume_root / "discarded-session")
        session.symlink_to(actual_session, target_is_directory=True)
        relative = "decision.md"
    elif link_kind == "workspace_root":
        actual_root = volume_root / "actual-workspace"
        actual_root.mkdir()
        (actual_root / "decision.md").write_text("private", encoding="utf-8")
        root.rename(session / "discarded-workspace")
        root.symlink_to(actual_root, target_is_directory=True)
        relative = "decision.md"
    elif link_kind == "descendant":
        (root / "alias").symlink_to(inside, target_is_directory=True)
        relative = "alias/decision.md"
    else:
        (root / "decision.md").symlink_to(target)
        relative = "decision.md"
    workspace = WorkspaceStorage(
        SimpleNamespace(process=LocalProcess()),
        volume_root=str(volume_root),
        root=str(root),
        max_file_bytes=32,
    )

    with pytest.raises(ValueError, match="unsafe"):
        workspace.stat(relative)


@pytest.mark.asyncio
async def test_async_workspace_fs_delete_and_patch_passthrough(tmp_path: Path) -> None:
    import hashlib

    from fleet_rlm.workspace.storage import AsyncWorkspaceStorage, WorkspaceStorage

    volume_root = tmp_path / "volume"
    root = volume_root / "sessions" / "session" / "workspace"
    root.mkdir(parents=True)

    class AsyncLocalProcess(LocalProcess):
        async def code_run(self, code: str, **_kwargs):
            return super().code_run(code)

    process = AsyncLocalProcess()
    workspace = AsyncWorkspaceStorage(
        WorkspaceStorage(
            SimpleNamespace(process=process),
            volume_root=str(volume_root),
            root=str(root),
            max_file_bytes=1024,
        )
    )

    await workspace.write_text("notes/report.txt", "one two one", overwrite=False)
    patched = await workspace.patch_text("notes/report.txt", "two", "three")
    assert patched.checksum_sha256 == hashlib.sha256(b"one three one").hexdigest()
    assert (root / "notes" / "report.txt").read_text(encoding="utf-8") == "one three one"

    from fleet_rlm.workspace.models import WorkspaceConflictError

    with pytest.raises(WorkspaceConflictError):
        await workspace.patch_text("notes/report.txt", "one", "x")

    await workspace.delete_path("notes/report.txt")
    assert not (root / "notes" / "report.txt").exists()
    with pytest.raises(FileNotFoundError):
        await workspace.delete_path("notes/report.txt")


# --- Daytona Sandbox Workspace Storage Symlink Safety ---
_SYMLINK_TEST_ROOT = "/workspace/sessions/session-a/workspace"


class _SymlinkFs:
    def __init__(self) -> None:
        self.directories = {
            "/",
            "/workspace",
            "/workspace/sessions",
            "/workspace/sessions/session-a",
            _SYMLINK_TEST_ROOT,
        }
        self.files: dict[str, bytes] = {}
        self.symlinks: dict[str, str] = {}
        self.listing: list[object] = []

    def get_file_info(self, path: str):
        if path in self.symlinks:
            return SimpleNamespace(path=path, is_dir=False, is_symlink=True, size=0)
        if path in self.directories:
            return SimpleNamespace(path=path, is_dir=True, size=0)
        if path in self.files:
            return SimpleNamespace(path=path, is_dir=False, size=len(self.files[path]))
        raise FileNotFoundError(path)

    def download_file(self, path: str) -> bytes:
        resolved = self.symlinks.get(path, path)
        if resolved not in self.files:
            raise FileNotFoundError(path)
        return self.files[resolved]

    def upload_file(self, data: bytes, path: str) -> None:
        self.files[path] = data

    def delete_file(self, path: str) -> None:
        self.files.pop(path, None)

    def list_files(self, _path: str, *, depth: int):
        del depth
        return self.listing


def _storage_for_symlink_test(fs: _SymlinkFs):
    from fleet_rlm.workspace.storage import DaytonaSandboxWorkspaceStorage

    return DaytonaSandboxWorkspaceStorage(SimpleNamespace(fs=fs), root=_SYMLINK_TEST_ROOT, volume_root="/workspace")


def test_daytona_storage_rejects_symlink_file_for_read_and_stat() -> None:
    from fleet_rlm.paths import UnsafePathError

    fs = _SymlinkFs()
    fs.files["/outside/secret.txt"] = b"secret"
    fs.symlinks[f"{_SYMLINK_TEST_ROOT}/secret.txt"] = "/outside/secret.txt"
    storage = _storage_for_symlink_test(fs)

    with pytest.raises(UnsafePathError, match="symlink"):
        storage.read_text("secret.txt")
    with pytest.raises(UnsafePathError, match="symlink"):
        storage.stat_path("secret.txt")


def test_daytona_storage_bounded_binary_read_is_exact_and_rejects_symlink() -> None:
    from fleet_rlm.paths import UnsafePathError

    fs = _SymlinkFs()
    path = f"{_SYMLINK_TEST_ROOT}/data.bin"
    fs.files[path] = b"12345678"
    storage = _storage_for_symlink_test(fs)

    assert storage.read_file_bytes("data.bin", max_bytes=8) == b"12345678"
    with pytest.raises(ValueError, match="file read bound exceeded"):
        storage.read_file_bytes("data.bin", max_bytes=7)

    fs.symlinks[f"{_SYMLINK_TEST_ROOT}/link.bin"] = path
    fs.symlinks[f"{_SYMLINK_TEST_ROOT}/link-dir"] = "/outside"
    with pytest.raises(UnsafePathError, match="symlink"):
        storage.read_file_bytes("link.bin", max_bytes=16)
    with pytest.raises(UnsafePathError, match="symlink"):
        storage.read_file_bytes("link-dir/data.bin", max_bytes=16)


def test_daytona_storage_recognizes_provider_octal_string_symlink_mode() -> None:
    from fleet_rlm.paths import UnsafePathError

    path = f"{_SYMLINK_TEST_ROOT}/secret.txt"

    class _ProviderMetadataFS(_SymlinkFs):
        def get_file_info(self, candidate: str):
            if candidate == path:
                return {"path": candidate, "is_dir": False, "mode": "120777"}
            return super().get_file_info(candidate)

    fs = _ProviderMetadataFS()
    fs.files[path] = b"secret"
    storage = _storage_for_symlink_test(fs)

    with pytest.raises(UnsafePathError, match="symlink"):
        storage.read_text("secret.txt")


def test_daytona_storage_rejects_symlink_component_before_outside_read_or_write() -> None:
    from fleet_rlm.paths import UnsafePathError

    fs = _SymlinkFs()
    fs.files["/outside/secret.txt"] = b"secret"
    fs.symlinks[f"{_SYMLINK_TEST_ROOT}/linked"] = "/outside"
    storage = _storage_for_symlink_test(fs)

    with pytest.raises(UnsafePathError, match="symlink"):
        storage.read_text("linked/secret.txt")
    with pytest.raises(UnsafePathError, match="symlink"):
        storage.write_text("linked/new.txt", "no")
    assert "/outside/new.txt" not in fs.files


def test_daytona_storage_rejects_symlink_and_outside_root_listing_records() -> None:
    from fleet_rlm.paths import UnsafePathError

    fs = _SymlinkFs()
    fs.listing = [SimpleNamespace(path=f"{_SYMLINK_TEST_ROOT}/link", is_dir=False, is_symlink=True, size=0)]
    storage = _storage_for_symlink_test(fs)
    with pytest.raises(UnsafePathError, match="symlink"):
        storage.list_entries()

    fs.listing = [SimpleNamespace(path="/outside/escape.txt", is_dir=False, size=1)]
    with pytest.raises(UnsafePathError, match="escapes trusted root"):
        storage.list_entries()


class _SdkNotFoundError(Exception):
    def __init__(self, path: str) -> None:
        super().__init__(f"Failed to get file info: stat {path}: no such file or directory")
        self.status_code = 404


def test_daytona_storage_writes_into_new_directories_with_sdk_not_found_errors() -> None:
    class SdkFs(_SymlinkFs):
        def get_file_info(self, path: str):
            try:
                return super().get_file_info(path)
            except FileNotFoundError:
                raise _SdkNotFoundError(path) from None

        def download_file(self, path: str) -> bytes:
            if path not in self.files:
                raise _SdkNotFoundError(path)
            return self.files[path]

    fs = SdkFs()
    storage = _storage_for_symlink_test(fs)
    storage.write_text("reports/2026/summary.md", "written")

    assert fs.files[f"{_SYMLINK_TEST_ROOT}/reports/2026/summary.md"] == b"written"


# --- Daytona Workspace Volume Gateway Contracts ---
@pytest.mark.asyncio
async def test_volume_read_maps_typed_missing_file_without_swallowing_provider_failure() -> None:
    from daytona.common.errors import DaytonaFileNotFoundError, DaytonaNotFoundError

    from fleet_rlm.workspace.storage import AsyncDaytonaVolumeFS

    class MissingFs:
        async def download_file(self, _path: str) -> bytes:
            raise DaytonaFileNotFoundError("missing", status_code=404)

    volume = AsyncDaytonaVolumeFS(SimpleNamespace(fs=MissingFs()))
    with pytest.raises(FileNotFoundError):
        await volume.read_bytes("/volume/task.json")

    class BrokenFs:
        async def download_file(self, _path: str) -> bytes:
            raise RuntimeError("provider unavailable")

    volume = AsyncDaytonaVolumeFS(SimpleNamespace(fs=BrokenFs()))
    with pytest.raises(RuntimeError, match="provider unavailable"):
        await volume.read_bytes("/volume/task.json")

    class MissingProviderRouteFs:
        async def download_file(self, _path: str) -> bytes:
            raise DaytonaNotFoundError("provider route missing", status_code=404)

    volume = AsyncDaytonaVolumeFS(SimpleNamespace(fs=MissingProviderRouteFs()))
    with pytest.raises(DaytonaNotFoundError, match="provider route missing"):
        await volume.read_bytes("/volume/task.json")


class _GatewayFakeFs:
    def __init__(self, *, fail_upload: bool = False) -> None:
        self.data: dict[str, bytes] = {}
        self.fail_upload = fail_upload

    async def create_folder(self, path: str, mode: str | None = None) -> None:
        del path, mode

    async def upload_file(self, data: bytes, path: str) -> None:
        if self.fail_upload:
            raise RuntimeError("provider unavailable")
        self.data[path] = bytes(data)

    async def download_file(self, path: str) -> bytes:
        return self.data[path]

    async def delete_file(self, path: str) -> None:
        self.data.pop(path, None)

    async def list_files(self, path: str, *, depth: int) -> list[object]:
        del depth
        return [
            SimpleNamespace(path=value, is_dir=False, mod_time=1.0)
            for value in sorted(self.data)
            if value.startswith(path + "/")
        ]


class _MountedGateway:
    def __init__(self, *, fail_upload: bool = False) -> None:
        from uuid import UUID

        self.sandbox = SimpleNamespace(fs=_GatewayFakeFs(fail_upload=fail_upload))
        self.opens: list[tuple[UUID, str]] = []
        self.closes = 0

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def open_sandbox(self, workspace_id, *, purpose: str):
        self.opens.append((workspace_id, purpose))
        try:
            yield self.sandbox
        finally:
            self.closes += 1


@pytest.mark.asyncio
async def test_gateway_uses_one_shared_mounted_scope_for_grouped_byte_operations() -> None:
    from uuid import uuid4

    from fleet_rlm.workspace.mounted_gateway import DaytonaWorkspaceVolumeGateway

    mounted = _MountedGateway()
    gateway = DaytonaWorkspaceVolumeGateway(
        mounted,  # ty: ignore[invalid-argument-type]
        mount_path="/home/daytona/fleet",
    )
    workspace_id = uuid4()
    path = "/home/daytona/fleet/attachments/a.bin"

    async with gateway.open_workspace(workspace_id) as volume:
        await volume.write_bytes(path, b"payload")
        assert await volume.read_bytes(path) == b"payload"
        assert await volume.list_files(
            "/home/daytona/fleet/attachments",
            max_depth=2,
            max_files=10,
        )
        await volume.remove_bytes(path)

    assert mounted.opens == [(workspace_id, "workspace-volume-io")]
    assert mounted.closes == 1


@pytest.mark.asyncio
async def test_gateway_can_list_the_mount_root() -> None:
    from uuid import uuid4

    from fleet_rlm.workspace.mounted_gateway import DaytonaWorkspaceVolumeGateway

    mounted = _MountedGateway()
    mounted.sandbox.fs.data["/home/daytona/fleet/files/notes.md"] = b"notes"
    gateway = DaytonaWorkspaceVolumeGateway(
        mounted,  # ty: ignore[invalid-argument-type]
        mount_path="/home/daytona/fleet",
    )

    files = await gateway.list_files(
        uuid4(),
        "/home/daytona/fleet",
        max_depth=8,
        max_files=10,
    )

    assert [file.path for file in files] == ["/home/daytona/fleet/files/notes.md"]


@pytest.mark.asyncio
async def test_gateway_releases_mounted_scope_when_operation_fails() -> None:
    from uuid import uuid4

    from fleet_rlm.workspace.mounted_gateway import DaytonaWorkspaceVolumeGateway

    mounted = _MountedGateway(fail_upload=True)
    gateway = DaytonaWorkspaceVolumeGateway(
        mounted,  # ty: ignore[invalid-argument-type]
        mount_path="/home/daytona/fleet",
    )

    with pytest.raises(RuntimeError, match="unavailable"):
        await gateway.write_bytes(
            uuid4(),
            "/home/daytona/fleet/attachments/a.bin",
            b"payload",
        )

    assert mounted.closes == 1


@pytest.mark.asyncio
async def test_gateway_rejects_paths_outside_workspace_mount_before_file_io() -> None:
    from uuid import uuid4

    from fleet_rlm.paths import UnsafePathError
    from fleet_rlm.workspace.mounted_gateway import DaytonaWorkspaceVolumeGateway

    mounted = _MountedGateway()
    gateway = DaytonaWorkspaceVolumeGateway(
        mounted,  # ty: ignore[invalid-argument-type]
        mount_path="/home/daytona/fleet",
    )

    with pytest.raises(UnsafePathError):
        await gateway.write_bytes(
            uuid4(),
            "/home/daytona/other/foreign.bin",
            b"payload",
        )

    assert mounted.sandbox.fs.data == {}


@pytest.mark.asyncio
async def test_stat_preserves_an_explicit_false_checksum_request() -> None:
    from fleet_rlm.workspace.models import WorkspaceEntry
    from fleet_rlm.workspace.mounted_gateway import DaytonaWorkspaceFileSession

    calls: list[bool | None] = []

    class Workspace:
        async def stat(self, path: str, *, include_checksum: bool | None = None) -> WorkspaceEntry:
            calls.append(include_checksum)
            return WorkspaceEntry(path, "file", 3, None, None)

    session = DaytonaWorkspaceFileSession(Workspace(), max_file_bytes=1024)
    entry = await session.stat("note.txt", include_checksum=False)

    assert calls == [False]
    assert entry is not None
    assert entry.checksum_sha256 is None


# ---------------------------------------------------------------------------
# Workspace & project path normalization and safety contracts
# ---------------------------------------------------------------------------


def _normalize(path: str, *, allow_root: bool = False) -> str:
    return normalize_workspace_path(path, allow_root=allow_root)


@pytest.mark.parametrize(
    "path",
    [
        "",
        "/absolute.txt",
        "../escape.txt",
        "notes/../escape.txt",
        "notes\\escape.txt",
        ".fleet/config",
        "notes/.fleet/config",
        "./notes.txt",
    ],
)
def test_rejects_unsafe_file_paths(path: str) -> None:
    with pytest.raises(ValueError):
        _normalize(path)


def test_enforces_segment_and_total_utf8_bounds_without_an_arbitrary_depth_cap() -> None:
    deep = "/".join(["a"] * 9)
    assert _normalize(deep) == deep

    assert _normalize("a" * 255) == "a" * 255
    with pytest.raises(ValueError):
        _normalize("a" * 256)

    bounded = "/".join(["a" * 127] * 8)
    assert len(bounded.encode("utf-8")) == 1023
    assert _normalize(bounded) == bounded
    assert _normalize(bounded + "a") == bounded + "a"
    with pytest.raises(ValueError):
        _normalize(bounded + "aa")


def test_bounds_use_utf8_bytes_not_code_points() -> None:
    assert _normalize("é" * 127) == "é" * 127
    with pytest.raises(ValueError):
        _normalize("é" * 128)


def test_projects_root_is_a_volume_sibling() -> None:
    paths = VolumePaths.from_mount()

    assert paths.projects_root() == PurePosixPath("/home/daytona/fleet/projects")
    assert paths.project_dir("fleet-rlm") == PurePosixPath("/home/daytona/fleet/projects/fleet-rlm")


@pytest.mark.parametrize(
    "slug",
    ["", "Fleet", "a/b", "a\\b", "sessions"],
)
def test_rejects_invalid_slugs(slug: str) -> None:
    with pytest.raises(UnsafePathError):
        validate_project_slug(slug)
    with pytest.raises(UnsafePathError):
        VolumePaths.from_mount().project_dir(slug)


def test_rejects_nul_and_non_string_slugs() -> None:
    with pytest.raises(UnsafePathError):
        validate_project_slug("fleet\x00rlm")
    for value in (None, 7, b"fleet-rlm"):
        with pytest.raises(UnsafePathError):
            validate_project_slug(value)  # type: ignore[arg-type]
