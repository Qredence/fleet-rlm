"""Daytona-backed Session Workspace filesystem."""

from __future__ import annotations

import json
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

import pytest

from fleet_rlm.files.workspace_models import WorkspaceEntry


class FakeFs:
    def __init__(self, root: str, *, root_exists: bool = True) -> None:
        self.directories = {root} if root_exists else set()
        self.files: dict[str, bytes] = {}

    def get_file_info(self, path: str):
        if path in self.directories:
            return SimpleNamespace(
                path=path,
                name=PurePosixPath(path).name,
                is_dir=True,
                size=0,
                modified_at="2026-07-16T12:00:00Z",
            )
        if path in self.files:
            return SimpleNamespace(
                path=path,
                name=PurePosixPath(path).name,
                is_dir=False,
                size=len(self.files[path]),
                modified_at="2026-07-16T12:00:00Z",
            )
        raise FileNotFoundError(path)

    def list_files(self, path: str, depth: int | None = None):
        assert depth == 1
        prefix = f"{path.rstrip('/')}/"
        children = []
        for candidate in sorted(self.directories | set(self.files)):
            if candidate == path or not candidate.startswith(prefix):
                continue
            relative = candidate[len(prefix) :]
            if "/" not in relative:
                children.append(self.get_file_info(candidate))
        return list(reversed(children))

    def create_folder(self, path: str, mode: str) -> None:
        assert mode == "700"
        current = PurePosixPath("/")
        for part in PurePosixPath(path).parts[1:]:
            current /= part
            self.directories.add(str(current))

    def upload_file(self, content: bytes, path: str) -> None:
        if str(PurePosixPath(path).parent) not in self.directories:
            raise FileNotFoundError(path)
        self.files[path] = bytes(content)

    def download_file(self, path: str) -> bytes:
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]


class FakeProcess:
    def __init__(self) -> None:
        self.safe = True
        self.calls: list[str] = []

    def code_run(self, code: str):
        self.calls.append(code)
        payload = {"safe": self.safe, "reason": None if self.safe else "symlink"}
        return SimpleNamespace(exit_code=0, result=json.dumps(payload))


class LocalProcess:
    def code_run(self, code: str):
        output = StringIO()
        with redirect_stdout(output):
            exec(code, {})  # noqa: S102 - executes only adapter-generated guard code in this test
        return SimpleNamespace(exit_code=0, result=output.getvalue().strip())


def _workspace(*, max_file_bytes: int = 32, root_exists: bool = True):
    from fleet_rlm.daytona.workspace_fs import DaytonaSessionWorkspaceFS

    volume_root = "/home/daytona/fleet"
    root = "/home/daytona/fleet/sessions/session/workspace"
    sandbox = SimpleNamespace(fs=FakeFs(root, root_exists=root_exists), process=FakeProcess())
    return (
        DaytonaSessionWorkspaceFS(
            sandbox,
            volume_root=volume_root,
            root=root,
            max_file_bytes=max_file_bytes,
        ),
        sandbox,
    )


def test_rejects_workspace_root_outside_trusted_volume() -> None:
    from fleet_rlm.daytona.workspace_fs import DaytonaSessionWorkspaceFS

    with pytest.raises(ValueError, match="trusted volume"):
        DaytonaSessionWorkspaceFS(
            SimpleNamespace(),
            volume_root="/home/daytona/fleet",
            root="/home/daytona/other/workspace",
            max_file_bytes=32,
        )


def test_lists_immediate_entries_deterministically_with_limit() -> None:
    workspace, sandbox = _workspace()
    sandbox.fs.directories.add(f"{workspace.root}/notes")
    sandbox.fs.files[f"{workspace.root}/z.txt"] = b"z"
    sandbox.fs.files[f"{workspace.root}/a.txt"] = b"a"
    sandbox.fs.files[f"{workspace.root}/notes/nested.txt"] = b"nested"

    entries = workspace.list_entries(".", limit=2)

    assert [(entry.path, entry.kind, entry.byte_size) for entry in entries] == [
        ("a.txt", "file", 1),
        ("notes", "directory", None),
    ]


def test_stat_returns_relative_metadata_or_none() -> None:
    workspace, sandbox = _workspace()
    sandbox.fs.files[f"{workspace.root}/note.txt"] = b"hello"

    entry = workspace.stat("note.txt")

    assert entry is not None
    assert entry.path == "note.txt"
    assert entry.kind == "file"
    assert entry.byte_size == 5
    assert entry.modified_at == "2026-07-16T12:00:00Z"
    assert workspace.stat("missing.txt") is None


def test_write_creates_parents_and_honors_overwrite() -> None:
    workspace, sandbox = _workspace()

    created = workspace.write_text("notes/decision.md", "first", overwrite=False)

    assert created.path == "notes/decision.md"
    assert created.byte_size == 5
    assert workspace.read_text("notes/decision.md", max_bytes=32) == "first"
    with pytest.raises(FileExistsError):
        workspace.write_text("notes/decision.md", "second", overwrite=False)
    replaced = workspace.write_text("notes/decision.md", "second", overwrite=True)
    assert replaced.byte_size == 6
    assert sandbox.fs.files[f"{workspace.root}/notes/decision.md"] == b"second"


def test_first_write_creates_missing_workspace_root() -> None:
    workspace, sandbox = _workspace(root_exists=False)

    workspace.write_text("notes/decision.md", "first", overwrite=False)

    assert workspace.root in sandbox.fs.directories
    assert sandbox.fs.files[f"{workspace.root}/notes/decision.md"] == b"first"


def test_first_root_level_write_creates_missing_workspace_root() -> None:
    workspace, sandbox = _workspace(root_exists=False)

    workspace.write_text("decision.md", "first", overwrite=False)

    assert workspace.root in sandbox.fs.directories
    assert sandbox.fs.files[f"{workspace.root}/decision.md"] == b"first"


def test_missing_workspace_root_behaves_as_an_empty_virtual_directory() -> None:
    workspace, _sandbox = _workspace(root_exists=False)

    assert workspace.list_entries(".") == ()
    assert workspace.stat(".") == WorkspaceEntry(".", "directory", None, None)


def test_real_guard_allows_a_missing_virtual_workspace_root(tmp_path: Path) -> None:
    from fleet_rlm.daytona.workspace_fs import DaytonaSessionWorkspaceFS

    volume_root = tmp_path / "volume"
    volume_root.mkdir()
    root = volume_root / "sessions" / "session" / "workspace"
    workspace = DaytonaSessionWorkspaceFS(
        SimpleNamespace(fs=FakeFs(str(root), root_exists=False), process=LocalProcess()),
        volume_root=str(volume_root),
        root=str(root),
        max_file_bytes=32,
    )

    assert workspace.list_entries(".") == ()
    assert workspace.stat(".") == WorkspaceEntry(".", "directory", None, None)


def test_enforces_write_and_read_byte_bounds_and_strict_utf8() -> None:
    workspace, sandbox = _workspace(max_file_bytes=4)

    with pytest.raises(ValueError, match="size"):
        workspace.write_text("large.txt", "12345", overwrite=False)

    path = f"{workspace.root}/invalid.txt"
    sandbox.fs.files[path] = b"\xff"
    with pytest.raises(ValueError, match="UTF-8"):
        workspace.read_text("invalid.txt", max_bytes=4)

    sandbox.fs.files[path] = b"12345"
    with pytest.raises(ValueError, match="read bound"):
        workspace.read_text("invalid.txt", max_bytes=4)


def test_rejects_directories_as_text_and_files_as_list_roots() -> None:
    workspace, sandbox = _workspace()
    sandbox.fs.directories.add(f"{workspace.root}/notes")
    sandbox.fs.files[f"{workspace.root}/note.txt"] = b"hello"

    with pytest.raises(IsADirectoryError):
        workspace.read_text("notes", max_bytes=32)
    with pytest.raises(NotADirectoryError):
        workspace.list_entries("note.txt")


def test_provider_guard_blocks_symlink_or_root_escape_before_io() -> None:
    workspace, sandbox = _workspace()
    sandbox.process.safe = False

    with pytest.raises(ValueError, match="unsafe"):
        workspace.write_text("notes/decision.md", "private", overwrite=False)

    assert sandbox.fs.files == {}
    assert "realpath" in sandbox.process.calls[0]
    assert "lstat" in sandbox.process.calls[0]


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
        SimpleNamespace(fs=SimpleNamespace(), process=LocalProcess()),
        volume_root=str(volume_root),
        root=str(root),
        max_file_bytes=32,
    )

    with pytest.raises(ValueError, match="unsafe"):
        workspace.stat(relative)
