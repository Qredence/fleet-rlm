"""Daytona-backed Session Workspace filesystem."""

from __future__ import annotations

import errno
import os
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest

from fleet_rlm.files.workspace_models import WorkspaceEntry


class LocalProcess:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def code_run(self, code: str):
        self.calls.append(code)
        output = StringIO()
        with redirect_stdout(output):
            try:
                exec(code, {})  # noqa: S102 - executes only adapter-generated guard code in this test
            except SystemExit:
                pass
        return SimpleNamespace(exit_code=0, result=output.getvalue().strip())


def _workspace(tmp_path: Path, *, max_file_bytes: int = 32, root_exists: bool = True):
    from fleet_rlm.daytona.workspace_fs import DaytonaSessionWorkspaceFS

    volume_root = tmp_path / "volume"
    session_parent = volume_root / "sessions" / "session"
    root = session_parent / "workspace"
    if root_exists:
        root.mkdir(parents=True)
    else:
        session_parent.mkdir(parents=True)
    process = LocalProcess()
    sandbox = SimpleNamespace(process=process)
    workspace = DaytonaSessionWorkspaceFS(
        sandbox,
        volume_root=str(volume_root),
        root=str(root),
        max_file_bytes=max_file_bytes,
    )
    return workspace, sandbox, root, process


def test_rejects_workspace_root_outside_trusted_volume() -> None:
    from fleet_rlm.daytona.workspace_fs import DaytonaSessionWorkspaceFS

    with pytest.raises(ValueError, match="trusted volume"):
        DaytonaSessionWorkspaceFS(
            SimpleNamespace(),
            volume_root="/home/daytona/fleet",
            root="/home/daytona/other/workspace",
            max_file_bytes=32,
        )


@pytest.mark.parametrize("reserved", ["attachments", "artifacts"])
def test_rejects_workspace_root_aliasing_managed_storage(reserved: str) -> None:
    from fleet_rlm.daytona.workspace_fs import DaytonaSessionWorkspaceFS

    with pytest.raises(ValueError, match="attachment or artifact"):
        DaytonaSessionWorkspaceFS(
            SimpleNamespace(),
            volume_root="/home/daytona/fleet",
            root=f"/home/daytona/fleet/{reserved}/session-file",
            max_file_bytes=32,
        )


def test_lists_immediate_entries_sorted_when_observation_window_is_complete(tmp_path: Path) -> None:
    workspace, _sandbox, root, _process = _workspace(tmp_path)
    (root / "notes").mkdir()
    (root / "z.txt").write_text("z", encoding="utf-8")
    (root / "a.txt").write_text("a", encoding="utf-8")
    (root / "notes" / "nested.txt").write_text("nested", encoding="utf-8")

    result = workspace.list_entries(".", limit=3)

    assert [(entry.path, entry.kind, entry.byte_size) for entry in result.entries] == [
        ("a.txt", "file", 1),
        ("notes", "directory", None),
        ("z.txt", "file", 1),
    ]
    assert result.truncated is False


def test_list_observes_only_one_entry_beyond_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace, _sandbox, root, process = _workspace(tmp_path)
    for index in range(5):
        (root / f"file-{index}.txt").write_text("x", encoding="utf-8")

    observed = 0
    original_scandir = os.scandir

    class CountingScanner:
        def __init__(self, iterator: os.ScandirIterator[os.DirEntry[str]]) -> None:
            self._iterator = iterator

        def __enter__(self) -> "CountingScanner":
            self._iterator.__enter__()
            return self

        def __exit__(self, *args: object) -> None:
            self._iterator.__exit__(*args)

        def __iter__(self) -> "CountingScanner":
            return self

        def __next__(self) -> os.DirEntry[str]:
            nonlocal observed
            observed += 1
            return next(self._iterator)

    def counting_scandir(path: int | str | bytes) -> CountingScanner:
        return CountingScanner(original_scandir(path))

    monkeypatch.setattr(os, "scandir", counting_scandir)
    result = workspace.list_entries(".", limit=3)

    assert len(result.entries) == 3
    assert result.truncated is True
    assert len(process.calls) == 1
    assert observed == 4


def test_stat_returns_relative_metadata_or_none(tmp_path: Path) -> None:
    workspace, _sandbox, root, _process = _workspace(tmp_path)
    (root / "note.txt").write_text("hello", encoding="utf-8")

    entry = workspace.stat("note.txt")

    assert entry is not None
    assert entry.path == "note.txt"
    assert entry.kind == "file"
    assert entry.byte_size == 5
    assert entry.modified_at is not None
    assert workspace.stat("missing.txt") is None


def test_write_creates_parents_and_honors_overwrite(tmp_path: Path) -> None:
    workspace, _sandbox, root, _process = _workspace(tmp_path)

    created = workspace.write_text("notes/decision.md", "first", overwrite=False)

    assert created.path == "notes/decision.md"
    assert created.byte_size == 5
    assert workspace.read_text("notes/decision.md", max_bytes=32) == "first"
    with pytest.raises(FileExistsError):
        workspace.write_text("notes/decision.md", "second", overwrite=False)
    replaced = workspace.write_text("notes/decision.md", "second", overwrite=True)
    assert replaced.byte_size == 6
    assert (root / "notes" / "decision.md").read_text(encoding="utf-8") == "second"


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


def test_overwrite_reports_parent_directory_fsync_warning_after_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, _sandbox, root, _process = _workspace(tmp_path)
    (root / "date.txt").write_text("previous", encoding="utf-8")
    original_fsync = os.fsync

    def volume_fsync(fd: int) -> None:
        if os.path.isdir(f"/dev/fd/{fd}"):
            raise OSError(errno.EPERM, "directory fsync unsupported")
        original_fsync(fd)

    monkeypatch.setattr(os, "fsync", volume_fsync)

    workspace.write_text("date.txt", "2026-07-19", overwrite=True)

    assert (root / "date.txt").read_text(encoding="utf-8") == "2026-07-19"
    assert workspace.last_cleanup_warning == {"errno": errno.EPERM}


def test_failed_overwrite_preserves_previous_contents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, _sandbox, root, _process = _workspace(tmp_path)
    target = root / "date.txt"
    target.write_text("previous", encoding="utf-8")
    original_fsync = os.fsync
    fsync_calls = 0

    def fail_staged_file_fsync(fd: int) -> None:
        nonlocal fsync_calls
        fsync_calls += 1
        if fsync_calls == 1:
            raise OSError(errno.EIO, "simulated staged-write failure")
        original_fsync(fd)

    monkeypatch.setattr(os, "fsync", fail_staged_file_fsync)

    from fleet_rlm.daytona.workspace_fs import WorkspaceStorageError

    with pytest.raises(WorkspaceStorageError):
        workspace.write_text("date.txt", "replacement", overwrite=True)

    assert target.read_text(encoding="utf-8") == "previous"
    assert not list(root.glob(".fleet-write-*"))


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

    from fleet_rlm.daytona.workspace_fs import WorkspaceStorageError

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
    workspace, _sandbox, root, process = _workspace(tmp_path)

    workspace.write_text("date.txt", "2026-07-19", overwrite=False)

    assert (root / "date.txt").read_text(encoding="utf-8") == "2026-07-19"
    assert "os.link(" in process.calls[0]


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


def test_partial_failure_fsync_cleans_destination_and_temporary_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, _sandbox, root, _process = _workspace(tmp_path)
    original_fsync = os.fsync
    fsync_calls = 0

    def fail_direct_file_fsync(fd: int) -> None:
        nonlocal fsync_calls
        fsync_calls += 1
        if fsync_calls == 2:
            raise OSError(errno.EIO, "simulated direct-create failure")
        original_fsync(fd)

    monkeypatch.setattr(
        os,
        "link",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError(errno.EPERM, "hard links unsupported")),
    )
    monkeypatch.setattr(os, "fsync", fail_direct_file_fsync)

    from fleet_rlm.daytona.workspace_fs import WorkspaceStorageError

    with pytest.raises(WorkspaceStorageError):
        workspace.write_text("date.txt", "partial", overwrite=False)

    assert not (root / "date.txt").exists()
    assert not list(root.glob(".fleet-write-*"))


def test_partial_failure_does_not_remove_replacement_destination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, _sandbox, root, _process = _workspace(tmp_path)
    original_fsync = os.fsync
    fsync_calls = 0
    original_stat = os.stat
    replaced = False
    stat_calls: list[tuple[object, dict[str, object]]] = []

    def fail_direct_file_fsync(fd: int) -> None:
        nonlocal fsync_calls
        fsync_calls += 1
        if fsync_calls == 2:
            raise OSError(errno.EIO, "simulated direct-create failure")
        original_fsync(fd)

    def replace_before_cleanup(path: str | bytes, *args: object, **kwargs: object) -> os.stat_result:
        nonlocal replaced
        stat_calls.append((path, kwargs))
        result = original_stat(path, *args, **kwargs)
        if not replaced and path == "date.txt" and kwargs.get("dir_fd") is not None:
            replacement = root / "replacement.txt"
            replacement.write_text("safe", encoding="utf-8")
            (root / "date.txt").unlink()
            replacement.rename(root / "date.txt")
            replaced = True
            result = original_stat(path, *args, **kwargs)
        return result

    monkeypatch.setattr(
        os,
        "link",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError(errno.EPERM, "hard links unsupported")),
    )
    monkeypatch.setattr(os, "fsync", fail_direct_file_fsync)
    monkeypatch.setattr(os, "stat", replace_before_cleanup)

    from fleet_rlm.daytona.workspace_fs import WorkspaceStorageError

    with pytest.raises(WorkspaceStorageError):
        workspace.write_text("date.txt", "partial", overwrite=False)

    assert replaced, (fsync_calls, stat_calls)
    assert (root / "date.txt").read_text(encoding="utf-8") == "safe"
    assert not list(root.glob(".fleet-write-*"))


def test_first_write_creates_missing_workspace_root(tmp_path: Path) -> None:
    workspace, _sandbox, root, _process = _workspace(tmp_path, root_exists=False)

    workspace.write_text("notes/decision.md", "first", overwrite=False)

    assert root.exists()
    assert (root / "notes" / "decision.md").read_text(encoding="utf-8") == "first"


def test_first_root_level_write_creates_missing_workspace_root(tmp_path: Path) -> None:
    workspace, _sandbox, root, _process = _workspace(tmp_path, root_exists=False)

    workspace.write_text("decision.md", "first", overwrite=False)

    assert root.exists()
    assert (root / "decision.md").read_text(encoding="utf-8") == "first"


def test_missing_workspace_root_behaves_as_an_empty_virtual_directory(tmp_path: Path) -> None:
    workspace, _sandbox, _root, _process = _workspace(tmp_path, root_exists=False)

    listing = workspace.list_entries(".")
    assert listing.entries == ()
    assert listing.truncated is False
    assert workspace.stat(".") == WorkspaceEntry(".", "directory", None, None)


def test_real_guard_allows_a_missing_virtual_workspace_root(tmp_path: Path) -> None:
    from fleet_rlm.daytona.workspace_fs import DaytonaSessionWorkspaceFS

    volume_root = tmp_path / "volume"
    volume_root.mkdir()
    root = volume_root / "sessions" / "session" / "workspace"
    workspace = DaytonaSessionWorkspaceFS(
        SimpleNamespace(process=LocalProcess()),
        volume_root=str(volume_root),
        root=str(root),
        max_file_bytes=32,
    )

    listing = workspace.list_entries(".")
    assert listing.entries == ()
    assert listing.truncated is False
    assert workspace.stat(".") == WorkspaceEntry(".", "directory", None, None)


def test_enforces_write_and_read_byte_bounds_and_strict_utf8(tmp_path: Path) -> None:
    workspace, _sandbox, root, _process = _workspace(tmp_path, max_file_bytes=4)

    with pytest.raises(ValueError, match="size"):
        workspace.write_text("large.txt", "12345", overwrite=False)

    path = root / "invalid.txt"
    path.write_bytes(b"\xff")
    with pytest.raises(ValueError, match="UTF-8"):
        workspace.read_text("invalid.txt", max_bytes=4)

    path.write_bytes(b"12345")
    with pytest.raises(ValueError, match="read bound"):
        workspace.read_text("invalid.txt", max_bytes=4)


def test_rejects_directories_as_text_and_files_as_list_roots(tmp_path: Path) -> None:
    workspace, _sandbox, root, _process = _workspace(tmp_path)
    (root / "notes").mkdir()
    (root / "note.txt").write_text("hello", encoding="utf-8")

    with pytest.raises(IsADirectoryError):
        workspace.read_text("notes", max_bytes=32)
    with pytest.raises(NotADirectoryError):
        workspace.list_entries("note.txt")


def test_atomic_write_rejects_symlink_target_before_io(tmp_path: Path) -> None:
    workspace, _sandbox, root, process = _workspace(tmp_path)
    secret = root / "secret.txt"
    secret.write_text("private", encoding="utf-8")
    alias = root / "notes"
    alias.symlink_to(secret)

    with pytest.raises(ValueError, match="unsafe"):
        workspace.write_text("notes/decision.md", "private", overwrite=False)

    assert not (root / "notes" / "decision.md").exists()
    assert len(process.calls) == 1
    assert "O_NOFOLLOW" in process.calls[0] or "os.open" in process.calls[0]


def test_atomic_read_uses_single_code_run_and_rejects_symlink_target(tmp_path: Path) -> None:
    workspace, _sandbox, root, process = _workspace(tmp_path)
    secret = root / "secret.txt"
    secret.write_text("private", encoding="utf-8")
    alias = root / "note.txt"
    alias.symlink_to(secret)

    with pytest.raises(ValueError, match="unsafe"):
        workspace.read_text("note.txt", max_bytes=32)

    assert len(process.calls) == 1


def test_final_replacement_race_never_reads_outside_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, _sandbox, root, _process = _workspace(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    target = root / "note.txt"
    target.write_text("private", encoding="utf-8")
    original_stat = os.stat
    replaced = False

    def racing_stat(
        path: int | str | bytes,
        *args: object,
        **kwargs: object,
    ) -> os.stat_result:
        nonlocal replaced
        result = original_stat(path, *args, **kwargs)
        if not replaced and path == "note.txt" and kwargs.get("dir_fd") is not None:
            target.unlink()
            target.symlink_to(outside)
            replaced = True
        return result

    monkeypatch.setattr(os, "stat", racing_stat)

    with pytest.raises(ValueError, match="unsafe"):
        workspace.read_text("note.txt", max_bytes=32)

    assert outside.read_text(encoding="utf-8") == "secret"


def test_intermediate_replacement_race_stays_on_open_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, _sandbox, root, _process = _workspace(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    notes = root / "notes"
    notes.mkdir()
    original_open = os.open
    replaced = False

    def racing_open(
        path: str | bytes,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal replaced
        fd = original_open(path, flags, mode, dir_fd=dir_fd)
        if not replaced and path == "notes" and dir_fd is not None:
            notes.rename(root / "detached-notes")
            notes.symlink_to(outside, target_is_directory=True)
            replaced = True
        return fd

    monkeypatch.setattr(os, "open", racing_open)

    created = workspace.write_text("notes/decision.md", "private", overwrite=False)

    assert created.path == "notes/decision.md"
    assert not (outside / "decision.md").exists()
    assert (root / "detached-notes" / "decision.md").read_text(encoding="utf-8") == "private"


@pytest.mark.parametrize("link_kind", ["session_ancestor", "workspace_root", "descendant", "target"])
def test_provider_guard_rejects_symlinks_below_the_trusted_volume(
    tmp_path: Path,
    link_kind: str,
) -> None:
    from fleet_rlm.daytona.workspace_fs import DaytonaSessionWorkspaceFS

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
    workspace = DaytonaSessionWorkspaceFS(
        SimpleNamespace(process=LocalProcess()),
        volume_root=str(volume_root),
        root=str(root),
        max_file_bytes=32,
    )

    with pytest.raises(ValueError, match="unsafe"):
        workspace.stat(relative)
