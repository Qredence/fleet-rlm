"""Typed host tools for the Session Workspace."""

from __future__ import annotations

from dataclasses import replace

import dspy

from fleet_rlm.files.workspace_models import WorkspaceEntry


class FakeWorkspace:
    def __init__(self) -> None:
        self.files: dict[str, str] = {}

    def list_entries(self, path: str, *, limit: int = 100) -> tuple[WorkspaceEntry, ...]:
        del path
        return tuple(
            WorkspaceEntry(name, "file", len(content.encode()), "2026-07-16T12:00:00Z")
            for name, content in sorted(self.files.items())[:limit]
        )

    def stat(self, path: str) -> WorkspaceEntry | None:
        content = self.files.get(path)
        if content is None:
            return None
        return WorkspaceEntry(path, "file", len(content.encode()), "2026-07-16T12:00:00Z")

    def read_text(self, path: str, *, max_bytes: int) -> str:
        if path not in self.files:
            raise FileNotFoundError(path)
        content = self.files[path]
        if len(content.encode()) > max_bytes:
            raise ValueError("workspace file exceeds read bound")
        return content

    def write_text(self, path: str, content: str, *, overwrite: bool) -> WorkspaceEntry:
        if path in self.files and not overwrite:
            raise FileExistsError(path)
        self.files[path] = content
        return WorkspaceEntry(path, "file", len(content.encode()), "2026-07-16T12:00:00Z")


def _tools(workspace: FakeWorkspace | None = None) -> tuple[FakeWorkspace, dict[str, dspy.Tool]]:
    from fleet_rlm.files.workspace_tools import WorkspaceToolHost

    value = workspace or FakeWorkspace()
    tools = WorkspaceToolHost(value, max_file_bytes=32).as_tools()
    return value, {str(tool.name): tool for tool in tools}


def test_exposes_exact_typed_tool_contracts() -> None:
    _, tools = _tools()

    assert tuple(tools) == (
        "list_workspace_files",
        "stat_workspace_file",
        "read_workspace_text",
        "write_workspace_text",
    )
    assert all(type(tool) is dspy.Tool for tool in tools.values())
    assert tools["list_workspace_files"].args == {
        "path": {"type": "string"},
        "limit": {"type": "integer", "minimum": 1, "maximum": 100},
    }
    assert tools["read_workspace_text"].args["max_chars"] == {
        "type": "integer",
        "minimum": 1,
        "maximum": 10_000,
    }
    assert tools["write_workspace_text"].args == {
        "path": {"type": "string"},
        "content": {"type": "string"},
        "overwrite": {"type": "boolean"},
    }


def test_round_trips_text_with_bounded_json_results() -> None:
    _, tools = _tools()

    written = tools["write_workspace_text"](
        path="notes/decision.md",
        content="durable decision",
        overwrite=False,
    )
    listed = tools["list_workspace_files"](path=".", limit=100)
    stated = tools["stat_workspace_file"](path="notes/decision.md")
    read = tools["read_workspace_text"](path="notes/decision.md", max_chars=10_000)

    assert written == {
        "ok": True,
        "path": "notes/decision.md",
        "kind": "file",
        "byte_size": 16,
        "modified_at": "2026-07-16T12:00:00Z",
    }
    assert listed["count"] == 1
    assert listed["entries"][0]["path"] == "notes/decision.md"
    assert stated["entry"]["byte_size"] == 16
    assert read == {
        "ok": True,
        "path": "notes/decision.md",
        "content": "durable decision",
        "encoding": "utf-8",
        "chars": 16,
        "byte_size": 16,
    }


def test_returns_stable_error_codes_without_exception_details() -> None:
    workspace, tools = _tools()

    assert tools["stat_workspace_file"](path="missing.txt") == {
        "ok": False,
        "error": "not_found",
    }
    assert tools["read_workspace_text"](path="missing.txt", max_chars=10)["error"] == "not_found"

    workspace.files["large.txt"] = "x" * 20
    assert tools["read_workspace_text"](path="large.txt", max_chars=10) == {
        "ok": False,
        "error": "too_large",
    }
    assert tools["write_workspace_text"](path="large.txt", content="new", overwrite=False) == {
        "ok": False,
        "error": "conflict",
    }


def test_entry_serialization_does_not_mutate_domain_value() -> None:
    entry = WorkspaceEntry("notes", "directory", None, None)
    workspace, tools = _tools()
    workspace.list_entries = lambda _path, limit=100: (entry,)  # type: ignore[method-assign]

    result = tools["list_workspace_files"](path=".", limit=1)

    assert result["entries"] == [{"path": "notes", "kind": "directory", "byte_size": None, "modified_at": None}]
    assert entry == replace(entry)
