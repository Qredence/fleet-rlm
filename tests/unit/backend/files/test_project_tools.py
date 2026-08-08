"""Typed host tools for browsable durable Project deliverables."""

from __future__ import annotations

import dspy
import pytest

from fleet_rlm.files.project_tools import ProjectToolError, ProjectToolHost
from fleet_rlm.files.workspace_models import WorkspaceEntry, WorkspaceListResult, WorkspaceTextPage


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


def _tools(fs: FakeProjectFS | None = None) -> tuple[FakeProjectFS, dict[str, dspy.Tool]]:
    value = fs or FakeProjectFS()
    tools = ProjectToolHost(value, max_file_bytes=32).as_tools()
    return value, {str(tool.name): tool for tool in tools}


def test_exposes_exact_typed_tool_contracts() -> None:
    _, tools = _tools()

    assert tuple(tools) == (
        "list_project_files",
        "stat_project_file",
        "read_project_text",
        "write_project_text",
    )
    assert all(type(tool) is dspy.Tool for tool in tools.values())
    assert "projects/<slug>/" in tools["list_project_files"].desc
    assert tools["list_project_files"].args == {
        "path": {"type": "string"},
        "limit": {"type": "integer", "minimum": 1, "maximum": 100},
        "after": {"type": ["string", "null"]},
    }
    assert tools["read_project_text"].args["max_chars"] == {
        "type": "integer",
        "minimum": 1,
        "maximum": 10_000,
    }
    assert tools["write_project_text"].args == {
        "path": {"type": "string"},
        "content": {"type": "string"},
        "overwrite": {"type": "boolean"},
    }
    assert "repo/task-derived slug" in tools["write_project_text"].desc
    assert "independent of Turn Commit" in tools["write_project_text"].desc


def test_round_trips_text_under_a_named_project() -> None:
    fs, tools = _tools()

    written = tools["write_project_text"](
        path="fleet-rlm/reports/review.md",
        content="durable review",
        overwrite=False,
    )
    listed_root = tools["list_project_files"](path=".", limit=100)
    listed = tools["list_project_files"](path="fleet-rlm", limit=100)
    stated = tools["stat_project_file"](path="fleet-rlm/reports/review.md")
    stated_dir = tools["stat_project_file"](path="fleet-rlm")
    read = tools["read_project_text"](path="fleet-rlm/reports/review.md", max_chars=10_000)

    assert written == {
        "ok": True,
        "namespace": "project_workspace",
        "path": "fleet-rlm/reports/review.md",
        "kind": "file",
        "byte_size": 14,
        "modified_at": "2026-07-16T12:00:00Z",
    }
    assert fs.files["fleet-rlm/reports/review.md"] == "durable review"
    assert listed_root["count"] == 2  # the seeded project directories (fleet-rlm, other-proj)
    assert listed_root["entries"][0]["kind"] == "directory"
    assert listed["count"] == 1
    assert listed["entries"][0]["path"] == "fleet-rlm/reports/review.md"
    assert stated["entry"]["byte_size"] == 14
    assert stated_dir["entry"]["kind"] == "directory"
    assert read["content"] == "durable review"
    assert read["eof"] is True
    assert read["namespace"] == "project_workspace"


def test_accepts_the_canonical_projects_prefixed_convention() -> None:
    _, tools = _tools()

    written = tools["write_project_text"](
        path="projects/fleet-rlm/reports/review.md",
        content="durable review",
        overwrite=False,
    )
    read = tools["read_project_text"](path="projects/fleet-rlm/reports/review.md", max_chars=10_000)

    assert written["path"] == "fleet-rlm/reports/review.md"
    assert read["content"] == "durable review"
    assert read["ok"] is True


def test_write_requires_explicit_overwrite_for_replacement() -> None:
    _, tools = _tools()

    tools["write_project_text"](path="fleet-rlm/review.md", content="first", overwrite=False)
    with pytest.raises(ProjectToolError, match="overwrite=True") as conflict:
        tools["write_project_text"](path="fleet-rlm/review.md", content="second", overwrite=False)
    assert conflict.value.code == "conflict"

    replaced = tools["write_project_text"](path="fleet-rlm/review.md", content="second", overwrite=True)
    assert replaced["ok"] is True
    assert tools["read_project_text"](path="fleet-rlm/review.md", max_chars=10)["content"] == "second"


@pytest.mark.parametrize(
    "path",
    [
        "Fleet/review.md",  # uppercase slug
        "sessions/review.md",  # reserved root slug
        "memory/review.md",
        "fleet-rlm/../review.md",  # traversal component
        "fleet-rlm//review.md",  # empty segment
        "/fleet-rlm/review.md",  # absolute
        "équipe/review.md",  # unicode slug
        "fleet-rlm",  # slug without a file inside the project
        ".",  # projects root is not a file
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
    from fleet_rlm.rlm.tool_observer import observe_tool

    fs = FakeProjectFS()
    host = ProjectToolHost(fs, max_file_bytes=64)
    tools = {str(tool.name): tool for tool in host.as_tools()}
    views = host.event_views()
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
