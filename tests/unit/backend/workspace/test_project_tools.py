"""Typed host tools for browsable durable Project deliverables."""

from __future__ import annotations

import dspy
import pytest

from fleet_rlm.workspace.models import WorkspaceEntry, WorkspaceListResult, WorkspaceTextPage
from fleet_rlm.workspace.projects import ProjectToolError, ProjectToolHost


class FakeProjectFS:
    def __init__(self) -> None:
        self.files: dict[str, str] = {}
        self.directories: set[str] = {"fleet-rlm", "other-proj"}

    def list_entries(self, path: str, *, limit: int = 100, after: str | None = None) -> WorkspaceListResult:
        if path == ".":
            children = sorted(self.directories)
            return WorkspaceListResult(
                entries=tuple(WorkspaceEntry(name, "directory", None, "2026-07-16T12:00:00Z") for name in children),
            )
        items = [
            (name, content)
            for name, content in sorted(self.files.items())
            if (name == path or name.startswith(f"{path}/")) and (after is None or name > after)
        ]
        selected = items[:limit]
        return WorkspaceListResult(
            entries=tuple(
                WorkspaceEntry(name, "file", len(content.encode()), "2026-07-16T12:00:00Z")
                for name, content in selected
            ),
            truncated=len(items) > limit,
            next_cursor=selected[-1][0] if len(items) > limit else None,
        )

    def stat(self, path: str) -> WorkspaceEntry | None:
        content = self.files.get(path)
        if content is not None:
            return WorkspaceEntry(path, "file", len(content.encode()), "2026-07-16T12:00:00Z")
        if path in self.directories or path == ".":
            return WorkspaceEntry(path, "directory", None, "2026-07-16T12:00:00Z")
        return None

    def read_text_page(
        self,
        path: str,
        *,
        cursor: str | None,
        max_chars: int,
        max_bytes: int,
    ) -> WorkspaceTextPage:
        if path in self.directories or path == ".":
            raise IsADirectoryError(path)
        if path not in self.files:
            raise FileNotFoundError(path)
        content = self.files[path]
        if len(content.encode()) > max_bytes:
            raise ValueError("project file exceeds read bound")
        if cursor is not None:
            raise ValueError("project cursor is invalid")
        return WorkspaceTextPage(content[:max_chars], None, len(content.encode()), len(content) <= max_chars)

    def write_text(self, path: str, content: str, *, overwrite: bool) -> WorkspaceEntry:
        if path in self.directories or path == ".":
            raise IsADirectoryError(path)
        parent = path.split("/", 1)[0]
        self.directories.add(parent)
        if path in self.files and not overwrite:
            raise FileExistsError(path)
        self.files[path] = content
        return WorkspaceEntry(path, "file", len(content.encode()), "2026-07-16T12:00:00Z")

    def append_text(self, path: str, content: str) -> WorkspaceEntry:
        self.files[path] = self.files.get(path, "") + content
        return WorkspaceEntry(path, "file", len(self.files[path].encode()), "2026-07-16T12:00:00Z")

    def delete_path(self, path: str, *, expected_sha256: str | None = None) -> None:
        from fleet_rlm.workspace.models import WorkspaceConflictError

        if expected_sha256 is not None and path in self.files:
            import hashlib

            actual = hashlib.sha256(self.files[path].encode()).hexdigest()
            if actual != expected_sha256:
                raise WorkspaceConflictError(path, detail="checksum_mismatch")
        if path in self.directories or path == ".":
            raise WorkspaceConflictError(path, detail="not_empty")
        if path not in self.files:
            raise FileNotFoundError(path)
        del self.files[path]

    def patch_text(
        self,
        path: str,
        old: str,
        new: str,
        *,
        expected_sha256: str | None = None,
    ) -> WorkspaceEntry:
        from fleet_rlm.workspace.models import WorkspaceConflictError

        if path in self.directories or path == ".":
            raise IsADirectoryError(path)
        if path not in self.files:
            raise FileNotFoundError(path)
        if expected_sha256 is not None:
            import hashlib

            actual = hashlib.sha256(self.files[path].encode()).hexdigest()
            if actual != expected_sha256:
                raise WorkspaceConflictError(path, detail="checksum_mismatch")
        occurrences = self.files[path].count(old)
        if occurrences < 1:
            raise WorkspaceConflictError(path, detail="missing")
        if occurrences > 1:
            raise WorkspaceConflictError(path, detail="ambiguous")
        self.files[path] = self.files[path].replace(old, new, 1)
        return WorkspaceEntry(
            path,
            "file",
            len(self.files[path].encode()),
            "2026-07-16T12:00:00Z",
            checksum_sha256=None,
        )


def _tools(fs: FakeProjectFS | None = None) -> tuple[FakeProjectFS, dict[str, dspy.Tool]]:
    value = fs or FakeProjectFS()
    tools = ProjectToolHost(value, max_file_bytes=32).as_tools()
    return value, {str(tool.name): tool for tool in tools}


@pytest.mark.parametrize(
    "path",
    [
        "Fleet/review.md",
        "sessions/review.md",
        "fleet-rlm/../review.md",
        "fleet-rlm//review.md",
        "/fleet-rlm/review.md",
        "fleet-rlm",
        ".",
    ],
)
def test_write_rejects_paths_outside_the_slug_contract(path: str) -> None:
    _, tools = _tools()

    with pytest.raises(ProjectToolError) as excinfo:
        tools["write_project_text"](path=path, content="x", overwrite=False)
    assert excinfo.value.code == "invalid_path"


def test_reserved_slug_feedback_names_the_slug_contract() -> None:
    _, tools = _tools()

    with pytest.raises(ProjectToolError, match="reserved") as excinfo:
        tools["write_project_text"](path="sessions/review.md", content="x", overwrite=False)
    assert excinfo.value.code == "invalid_path"
    assert excinfo.value.public_message == "Project path is invalid: project slug is a reserved Volume root name"

    with pytest.raises(ProjectToolError, match="must name a file inside a project"):
        tools["write_project_text"](path="fleet-rlm", content="x", overwrite=False)


def test_raises_stable_safe_errors_without_exception_details() -> None:
    _, tools = _tools()

    with pytest.raises(ProjectToolError) as missing:
        tools["stat_project_file"](path="fleet-rlm/missing.md")
    assert missing.value.code == "not_found"
    with pytest.raises(ProjectToolError) as missing_read:
        tools["read_project_text"](path="fleet-rlm/missing.md", max_chars=10)
    assert missing_read.value.code == "not_found"
    with pytest.raises(ProjectToolError) as directory:
        tools["read_project_text"](path="fleet-rlm", max_chars=10)
    assert directory.value.code == "invalid_path"
    with pytest.raises(ProjectToolError) as bound:
        # dspy.Tool validates the declared maximum first; call the host func to
        # exercise the internal guard the broker path relies on.
        tools["read_project_text"].func(path="fleet-rlm/x.md", max_chars=10_001)
    assert bound.value.code == "invalid_path"
    with pytest.raises(ProjectToolError) as list_bound:
        tools["list_project_files"].func(path="fleet-rlm", limit=0)
    assert list_bound.value.code == "invalid_path"
    with pytest.raises(ProjectToolError) as too_large:
        tools["write_project_text"](path="fleet-rlm/x.md", content="x" * 33, overwrite=False)
    assert too_large.value.code == "too_large"


def test_project_event_views_expose_metadata_without_file_bodies_or_entries() -> None:
    from fleet_rlm.rlm.events import observe_tool

    fs = FakeProjectFS()
    host = ProjectToolHost(fs, max_file_bytes=64)
    tools = {str(tool.name): tool for tool in host.as_tools()}
    views = host.event_views()
    assert "append_project_text" not in views
    observed: list[object] = []

    observe_tool(tools["write_project_text"], observed.append, views["write_project_text"])(
        path="fleet-rlm/reports/private.md",
        content="private project body",
        overwrite=False,
    )
    observe_tool(tools["list_project_files"], observed.append, views["list_project_files"])(
        path="fleet-rlm",
        limit=100,
    )
    observe_tool(tools["read_project_text"], observed.append, views["read_project_text"])(
        path="fleet-rlm/reports/private.md",
        max_chars=64,
    )

    assert observed[0].input == {
        "path": "fleet-rlm/reports/private.md",
        "overwrite": False,
        "content_chars": 20,
    }
    assert observed[1].output == {
        "ok": True,
        "namespace": "project_workspace",
        "path": "fleet-rlm/reports/private.md",
        "byte_size": 20,
    }
    assert observed[3].output == {
        "ok": True,
        "path": "fleet-rlm",
        "count": 1,
        "truncated": False,
        "next_cursor": None,
    }
    assert observed[5].output == {
        "ok": True,
        "namespace": "project_workspace",
        "path": "fleet-rlm/reports/private.md",
        "next_cursor": None,
        "byte_size": 20,
        "eof": True,
    }
    assert "private project body" not in str(observed)
    assert "entries" not in str(observed)

    observed.clear()
    oversized_path = "x" * 2_000
    with pytest.raises(ProjectToolError):
        observe_tool(tools["stat_project_file"], observed.append, views["stat_project_file"])(path=oversized_path)
    assert observed[0].input == {}
    assert oversized_path not in str(observed)

    observed.clear()
    with pytest.raises(ProjectToolError):
        observe_tool(tools["stat_project_file"], observed.append, views["stat_project_file"])(
            path="/home/daytona/private"
        )
    assert observed[0].input == {}
    assert "/home/daytona" not in str(observed)


def test_delete_project_path_happy_scope_and_conflict_errors() -> None:
    fs, tools = _tools()
    fs.files["fleet-rlm/reports/stale.md"] = "old"

    deleted = tools["delete_project_path"](path="projects/fleet-rlm/reports/stale.md")

    assert deleted == {"ok": True, "namespace": "project_workspace", "path": "fleet-rlm/reports/stale.md"}
    assert "fleet-rlm/reports/stale.md" not in fs.files

    with pytest.raises(ProjectToolError) as missing:
        tools["delete_project_path"](path="fleet-rlm/reports/stale.md")
    assert missing.value.code == "not_found"

    with pytest.raises(ProjectToolError, match="not empty") as not_empty:
        tools["delete_project_path"](path="fleet-rlm")
    assert not_empty.value.code == "conflict"

    with pytest.raises(ProjectToolError) as root:
        tools["delete_project_path"](path=".")
    assert root.value.code == "invalid_path"


@pytest.mark.parametrize(
    "path",
    ["attachments/private.md", "../fleet-rlm/review.md", "Projects/fleet-rlm/review.md"],
)
def test_delete_and_edit_reject_paths_outside_the_allowlist(path: str) -> None:
    _, tools = _tools()

    with pytest.raises(ProjectToolError) as deleted:
        tools["delete_project_path"](path=path)
    assert deleted.value.code == "invalid_path"
    with pytest.raises(ProjectToolError) as edited:
        tools["edit_project_text"](path=path, old="a", new="b")
    assert edited.value.code == "invalid_path"


def test_delete_and_edit_project_event_views_expose_metadata_only() -> None:
    from fleet_rlm.rlm.events import observe_tool

    fs = FakeProjectFS()
    fs.files["fleet-rlm/reports/private.md"] = "private project fragment"
    host = ProjectToolHost(fs, max_file_bytes=64)
    tools = {str(tool.name): tool for tool in host.as_tools()}
    views = host.event_views()
    observed: list[object] = []

    observe_tool(tools["edit_project_text"], observed.append, views["edit_project_text"])(
        path="fleet-rlm/reports/private.md",
        old="private project",
        new="rewritten project",
        expected_sha256=None,
    )
    observe_tool(tools["delete_project_path"], observed.append, views["delete_project_path"])(
        path="fleet-rlm/reports/private.md",
        expected_sha256=None,
    )

    assert observed[0].input == {
        "path": "fleet-rlm/reports/private.md",
        "old_chars": 15,
        "new_chars": 17,
        "checksum_precondition": False,
    }
    assert observed[1].output == {
        "ok": True,
        "namespace": "project_workspace",
        "path": "fleet-rlm/reports/private.md",
        "byte_size": 26,
    }
    assert observed[2].input == {
        "path": "fleet-rlm/reports/private.md",
        "checksum_precondition": False,
    }
    assert observed[3].output == {
        "ok": True,
        "namespace": "project_workspace",
        "path": "fleet-rlm/reports/private.md",
    }
    assert "private project fragment" not in str(observed)
    assert "rewritten project" not in str(observed)
