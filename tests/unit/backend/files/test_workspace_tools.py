"""Typed host tools for the Session Workspace."""

from __future__ import annotations

from dataclasses import replace

import dspy
import pytest

from fleet_rlm.files.workspace_models import WorkspaceEntry, WorkspaceListResult
from fleet_rlm.rlm.tool_observer import observe_tool


class FakeWorkspace:
    def __init__(self) -> None:
        self.files: dict[str, str] = {}

    def list_entries(self, path: str, *, limit: int = 100) -> WorkspaceListResult:
        del path
        items = sorted(self.files.items())
        return WorkspaceListResult(
            entries=tuple(
                WorkspaceEntry(name, "file", len(content.encode()), "2026-07-16T12:00:00Z")
                for name, content in items[:limit]
            ),
            truncated=len(items) > limit,
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
        "namespace": "session_workspace",
        "path": "notes/decision.md",
        "kind": "file",
        "byte_size": 16,
        "modified_at": "2026-07-16T12:00:00Z",
    }
    assert listed["count"] == 1
    assert listed["truncated"] is False
    assert listed["entries"][0]["path"] == "notes/decision.md"
    assert stated["entry"]["byte_size"] == 16
    assert read == "durable decision"


def test_workspace_event_views_expose_metadata_without_file_bodies_or_entries() -> None:
    from fleet_rlm.files.workspace_tools import WorkspaceToolHost

    workspace = FakeWorkspace()
    host = WorkspaceToolHost(workspace, max_file_bytes=64)
    tools = {str(tool.name): tool for tool in host.as_tools()}
    views = host.event_views()
    observed: list[object] = []

    observe_tool(tools["write_workspace_text"], observed.append, views["write_workspace_text"])(
        path="notes/private.md",
        content="private workspace body",
        overwrite=False,
    )
    observe_tool(tools["list_workspace_files"], observed.append, views["list_workspace_files"])(
        path=".",
        limit=100,
    )
    observe_tool(tools["read_workspace_text"], observed.append, views["read_workspace_text"])(
        path="notes/private.md",
        max_chars=64,
    )

    assert observed[0].input == {
        "path": "notes/private.md",
        "overwrite": False,
        "content_chars": 22,
    }
    assert observed[1].output == {
        "ok": True,
        "namespace": "session_workspace",
        "path": "notes/private.md",
        "byte_size": 22,
    }
    assert observed[3].output == {"ok": True, "path": ".", "count": 1, "truncated": False}
    assert observed[5].output == {"ok": True, "namespace": "session_workspace"}
    assert "private workspace body" not in str(observed)
    assert "entries" not in str(observed)

    observed.clear()
    oversized_path = "x" * 2_000
    from fleet_rlm.files.workspace_tools import WorkspaceToolError

    with pytest.raises(WorkspaceToolError):
        observe_tool(tools["stat_workspace_file"], observed.append, views["stat_workspace_file"])(path=oversized_path)
    assert observed[0].input == {}
    assert oversized_path not in str(observed)

    observed.clear()
    with pytest.raises(WorkspaceToolError):
        observe_tool(tools["stat_workspace_file"], observed.append, views["stat_workspace_file"])(
            path="/home/daytona/private"
        )
    assert observed[0].input == {}
    assert "/home/daytona" not in str(observed)


def test_raises_stable_safe_errors_without_exception_details() -> None:
    workspace, tools = _tools()

    from fleet_rlm.files.workspace_tools import WorkspaceToolError

    with pytest.raises(WorkspaceToolError, match="not found") as missing:
        tools["stat_workspace_file"](path="missing.txt")
    assert missing.value.code == "not_found"
    with pytest.raises(WorkspaceToolError, match="not found"):
        tools["read_workspace_text"](path="missing.txt", max_chars=10)

    workspace.files["large.txt"] = "x" * 20
    with pytest.raises(WorkspaceToolError) as large:
        tools["read_workspace_text"](path="large.txt", max_chars=10)
    assert large.value.code == "too_large"
    with pytest.raises(WorkspaceToolError) as conflict:
        tools["write_workspace_text"](path="large.txt", content="new", overwrite=False)
    assert conflict.value.code == "conflict"


def test_workspace_storage_failure_has_structured_host_error() -> None:
    workspace, tools = _tools()
    from fleet_rlm.daytona.workspace_fs import WorkspaceStorageError
    from fleet_rlm.files.workspace_tools import WorkspaceToolError

    def unavailable(*_args: object, **_kwargs: object) -> WorkspaceEntry:
        raise WorkspaceStorageError(1)

    workspace.write_text = unavailable  # type: ignore[method-assign]

    with pytest.raises(WorkspaceToolError) as failure:
        tools["write_workspace_text"](path="date.txt", content="2026-07-20", overwrite=False)

    assert failure.value.code == "unsupported_storage"
    assert "errno" not in failure.value.public_message


def test_entry_serialization_does_not_mutate_domain_value() -> None:
    entry = WorkspaceEntry("notes", "directory", None, None)
    workspace, tools = _tools()
    workspace.list_entries = lambda _path, limit=100: WorkspaceListResult((entry,), truncated=False)  # type: ignore[method-assign]

    result = tools["list_workspace_files"](path=".", limit=1)

    assert result["entries"] == [{"path": "notes", "kind": "directory", "byte_size": None, "modified_at": None}]
    assert entry == replace(entry)
