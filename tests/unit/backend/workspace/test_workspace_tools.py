"""Typed host tools for the Session Workspace."""

from __future__ import annotations

import hashlib
import time

import dspy
import pytest

from fleet_rlm.rlm.budget import BudgetDimension, BudgetLimits, TurnBudget, TurnBudgetExhausted
from fleet_rlm.rlm.events import ToolFailed, WarningEvent, observe_tool
from fleet_rlm.rlm.execution import RunToolGuards
from fleet_rlm.workspace.models import WorkspaceEntry, WorkspaceListResult, WorkspaceTextPage


class FakeWorkspace:
    def __init__(self) -> None:
        self.files: dict[str, str] = {}

    def list_entries(self, path: str, *, limit: int = 100, after: str | None = None) -> WorkspaceListResult:
        del path
        items = [(name, content) for name, content in sorted(self.files.items()) if after is None or name > after]
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
        if content is None:
            return None
        return WorkspaceEntry(path, "file", len(content.encode()), "2026-07-16T12:00:00Z")

    def read_text_page(
        self,
        path: str,
        *,
        cursor: str | None,
        max_chars: int,
        max_bytes: int,
    ) -> WorkspaceTextPage:
        if path not in self.files:
            raise FileNotFoundError(path)
        content = self.files[path]
        if len(content.encode()) > max_bytes:
            raise ValueError("workspace file exceeds read bound")
        if cursor is not None:
            raise ValueError("workspace cursor is invalid")
        return WorkspaceTextPage(content[:max_chars], None, len(content.encode()), len(content) <= max_chars)

    def write_text(self, path: str, content: str, *, overwrite: bool) -> WorkspaceEntry:
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
            actual = hashlib.sha256(self.files[path].encode()).hexdigest()
            if actual != expected_sha256:
                raise WorkspaceConflictError(path, detail="checksum_mismatch")
        if path == "conflicted.txt":
            raise WorkspaceConflictError(path, detail="checksum_mismatch")
        if path == "notes":
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

        if path not in self.files:
            raise FileNotFoundError(path)
        if expected_sha256 is not None:
            actual = hashlib.sha256(self.files[path].encode()).hexdigest()
            if actual != expected_sha256:
                raise WorkspaceConflictError(path, detail="checksum_mismatch")
        occurrences = self.files[path].count(old)
        if occurrences < 1:
            raise WorkspaceConflictError(path, detail="missing")
        if occurrences > 1:
            raise WorkspaceConflictError(path, detail="ambiguous")
        self.files[path] = self.files[path].replace(old, new, 1)
        # The fake reports a checksum like the real FS so the test locks the
        # tool's LLM-facing 4-key entry shape.
        return WorkspaceEntry(
            path,
            "file",
            len(self.files[path].encode()),
            "2026-07-16T12:00:00Z",
            checksum_sha256=hashlib.sha256(self.files[path].encode()).hexdigest(),
        )


def _tools(workspace: FakeWorkspace | None = None) -> tuple[FakeWorkspace, dict[str, dspy.Tool]]:
    from fleet_rlm.workspace.workspace import WorkspaceToolHost

    value = workspace or FakeWorkspace()
    tools = WorkspaceToolHost(value, max_file_bytes=32).as_tools()
    return value, {str(tool.name): tool for tool in tools}


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
    assert read["content"] == "durable decision"


def test_batch_reads_selected_pages_in_order_and_keeps_item_failures_local() -> None:
    workspace, tools = _tools()
    workspace.files.update({"notes/a.md": "alpha", "notes/b.md": "bravo"})

    result = tools["read_workspace_text_batch"](
        requests=[
            {"path": "notes/b.md", "max_chars": 3},
            {"path": "missing.md", "max_chars": 10},
            {"path": "notes/a.md", "max_chars": 10},
        ]
    )

    assert result["count"] == 3
    assert result["failed_count"] == 1
    assert [item["path"] for item in result["results"]] == ["notes/b.md", "missing.md", "notes/a.md"]
    assert result["results"][0]["content"] == "bra"
    assert result["results"][1] == {
        "ok": False,
        "namespace": "session_workspace",
        "path": "missing.md",
        "error": "not_found",
    }
    assert result["results"][2]["content"] == "alpha"


def test_workspace_event_views_expose_metadata_without_file_bodies_or_entries() -> None:
    from fleet_rlm.workspace.workspace import WorkspaceToolHost

    workspace = FakeWorkspace()
    host = WorkspaceToolHost(workspace, max_file_bytes=64)
    tools = {str(tool.name): tool for tool in host.as_tools()}
    views = host.event_views()
    assert "append_workspace_text" in views
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
    observe_tool(tools["read_workspace_text_batch"], observed.append, views["read_workspace_text_batch"])(
        requests=[{"path": "notes/private.md", "max_chars": 20}]
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
    assert observed[3].output == {
        "ok": True,
        "path": ".",
        "count": 1,
        "truncated": False,
        "next_cursor": None,
    }
    assert observed[5].output == {
        "ok": True,
        "namespace": "session_workspace",
        "path": "notes/private.md",
        "next_cursor": None,
        "byte_size": 22,
        "eof": True,
    }
    assert observed[6].input == {"request_count": 1, "requested_chars": 20}
    assert observed[7].output == {"ok": True, "namespace": "session_workspace", "count": 1, "failed_count": 0}
    assert "private workspace body" not in str(observed)
    assert "entries" not in str(observed)

    observed.clear()
    oversized_path = "x" * 2_000
    from fleet_rlm.workspace.workspace import WorkspaceToolError

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


def test_repeated_workspace_reads_are_idempotent_but_still_observed() -> None:
    workspace, tools = _tools()
    workspace.files["date.txt"] = "2026-07-21"
    from fleet_rlm.workspace.workspace import WorkspaceToolHost

    host = WorkspaceToolHost(workspace, max_file_bytes=32)
    view = host.event_views()["read_workspace_text"]
    observed: list[object] = []
    guards = RunToolGuards()
    read = observe_tool(tools["read_workspace_text"], observed.append, view, guards=guards)

    assert read(path="date.txt", max_chars=32)["content"] == "2026-07-21"
    assert read(path="date.txt", max_chars=32)["content"] == "2026-07-21"
    assert sum(isinstance(item, WarningEvent) for item in observed) == 1
    assert not any(isinstance(item, ToolFailed) for item in observed)


def test_batch_read_uses_one_turn_budget_reservation() -> None:
    from fleet_rlm.workspace.workspace import WorkspaceToolHost

    workspace, tools = _tools()
    workspace.files.update({f"notes/{index}.md": str(index) for index in range(32)})
    budget = TurnBudget(deadline=time.monotonic() + 60, limits=BudgetLimits(tool_calls=1))
    observed: list[object] = []
    read = observe_tool(
        tools["read_workspace_text_batch"],
        observed.append,
        WorkspaceToolHost(workspace).event_views()["read_workspace_text_batch"],
        guards=RunToolGuards(budget=budget),
    )

    result = read(requests=[{"path": f"notes/{index}.md", "max_chars": 10} for index in range(32)])

    assert result["count"] == 32
    assert budget.snapshot()[BudgetDimension.TOOL_CALLS.value] == 1
    with pytest.raises(TurnBudgetExhausted):
        read(requests=[{"path": "notes/0.md", "max_chars": 10}])


def test_raises_stable_safe_errors_without_exception_details() -> None:
    workspace, tools = _tools()

    from fleet_rlm.workspace.workspace import WorkspaceToolError

    with pytest.raises(WorkspaceToolError, match="not found") as missing:
        tools["stat_workspace_file"](path="missing.txt")
    assert missing.value.code == "not_found"
    with pytest.raises(WorkspaceToolError, match="not found"):
        tools["read_workspace_text"](path="missing.txt", max_chars=10)

    workspace.files["large.txt"] = "x" * 20
    page = tools["read_workspace_text"](path="large.txt", max_chars=10)
    assert page["content"] == "x" * 10
    assert page["eof"] is False
    with pytest.raises(WorkspaceToolError) as conflict:
        tools["write_workspace_text"](path="large.txt", content="new", overwrite=False)
    assert conflict.value.code == "conflict"


def test_paged_read_list_cursor_and_append_tool_contracts() -> None:
    _, tools = _tools()

    assert "after" in tools["list_workspace_files"].args
    assert "cursor" in tools["read_workspace_text"].args
    assert "append_workspace_text" in tools

    appended = tools["append_workspace_text"](path="notes.md", content="first")
    page = tools["read_workspace_text"](path="notes.md", max_chars=10)

    assert appended["ok"] is True
    assert page == {
        "ok": True,
        "namespace": "session_workspace",
        "path": "notes.md",
        "content": "first",
        "next_cursor": None,
        "byte_size": 5,
        "eof": True,
    }


def test_edit_workspace_text_conflict_and_scope_errors() -> None:
    workspace, tools = _tools()
    from fleet_rlm.workspace.workspace import WorkspaceToolError

    workspace.files["notes/report.md"] = "alpha alpha"
    with pytest.raises(WorkspaceToolError, match="more than once") as ambiguous:
        tools["edit_workspace_text"](path="notes/report.md", old="alpha", new="delta")
    assert ambiguous.value.code == "conflict"
    assert workspace.files["notes/report.md"] == "alpha alpha"

    with pytest.raises(WorkspaceToolError, match="was not found") as missing:
        tools["edit_workspace_text"](path="notes/report.md", old="omega", new="delta")
    assert missing.value.code == "conflict"

    with pytest.raises(WorkspaceToolError, match="checksum precondition") as checksum:
        tools["edit_workspace_text"](path="notes/report.md", old="alpha", new="delta", expected_sha256="f" * 64)
    assert checksum.value.code == "conflict"

    matched = tools["edit_workspace_text"](
        path="notes/report.md",
        old="alpha alpha",
        new="done",
        expected_sha256=hashlib.sha256(b"alpha alpha").hexdigest(),
    )
    assert matched["ok"] is True

    with pytest.raises(WorkspaceToolError) as missing_file:
        tools["edit_workspace_text"](path="missing.md", old="a", new="b")
    assert missing_file.value.code == "not_found"

    with pytest.raises(WorkspaceToolError) as escaped:
        tools["edit_workspace_text"](path="../../artifacts/x.md", old="a", new="b")
    assert escaped.value.code == "invalid_path"

    with pytest.raises(WorkspaceToolError) as empty_old:
        tools["edit_workspace_text"](path="notes/report.md", old="", new="b")
    assert empty_old.value.code == "invalid_path"

    with pytest.raises(WorkspaceToolError) as too_large:
        tools["edit_workspace_text"](path="notes/report.md", old="y" * 33, new="b")
    assert too_large.value.code == "too_large"


def test_delete_and_edit_event_views_expose_metadata_without_fragments() -> None:
    from fleet_rlm.rlm.events import observe_tool
    from fleet_rlm.workspace.workspace import WorkspaceToolHost

    workspace = FakeWorkspace()
    workspace.files["notes/private.md"] = "private fragment body"
    host = WorkspaceToolHost(workspace, max_file_bytes=64)
    tools = {str(tool.name): tool for tool in host.as_tools()}
    views = host.event_views()
    observed: list[object] = []

    observe_tool(tools["edit_workspace_text"], observed.append, views["edit_workspace_text"])(
        path="notes/private.md",
        old="private fragment",
        new="rewritten fragment",
        expected_sha256=None,
    )
    observe_tool(tools["delete_workspace_path"], observed.append, views["delete_workspace_path"])(
        path="notes/private.md",
        expected_sha256=None,
    )

    assert observed[0].input == {
        "path": "notes/private.md",
        "old_chars": 16,
        "new_chars": 18,
        "checksum_precondition": False,
    }
    assert observed[1].output == {
        "ok": True,
        "namespace": "session_workspace",
        "path": "notes/private.md",
        "byte_size": 23,
    }
    assert observed[2].input == {"path": "notes/private.md", "checksum_precondition": False}
    assert observed[3].output == {"ok": True, "namespace": "session_workspace", "path": "notes/private.md"}
    assert "private fragment" not in str(observed)


@pytest.mark.parametrize("directory", [".", "notes", "notes/reports"])
def test_daytona_workspace_storage_lists_direct_children(directory: str) -> None:
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from fleet_rlm.workspace.storage import DaytonaSandboxWorkspaceStorage

    root = "/workspace/sessions/session-a/workspace"
    prefix = "" if directory == "." else f"{directory}/"
    full_path = root if directory == "." else f"{root}/{directory}"
    fs = MagicMock()
    fs.get_file_info.return_value = {"mode": "040755", "is_symlink": False, "symlink": False}
    fs.list_files.return_value = [
        {"path": f"{full_path}/{name}", "is_dir": is_dir, "size": 7, "mod_time": "2026-09-20T00:00:00Z"}
        for name, is_dir in [
            ("z.md", False),
            ("a", True),
            ("a/deep.md", False),
            (".fleet-private", True),
            (".fleet-private/state", False),
        ]
    ] + [{"path": full_path}]
    storage = DaytonaSandboxWorkspaceStorage(SimpleNamespace(fs=fs), volume_root="/workspace", root=root)

    first = storage.list_entries(directory, limit=1)
    second = storage.list_entries(directory, limit=1, after=first.next_cursor)

    fs.list_files.assert_called_with(full_path, depth=1)
    assert [(entry.path, entry.kind) for entry in first.entries] == [(f"{prefix}a", "directory")]
    assert first.truncated is True
    assert first.next_cursor == f"{prefix}a"
    assert [(entry.path, entry.kind) for entry in second.entries] == [(f"{prefix}z.md", "file")]
    assert second.truncated is False
    assert second.next_cursor is None


@pytest.mark.parametrize(
    "item_path",
    ["/workspace/sessions/session-a/workspace/../outside.md", "/elsewhere/file.md"],
)
def test_daytona_workspace_storage_rejects_listing_paths_outside_trusted_root(item_path: str) -> None:
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from fleet_rlm.paths import UnsafePathError
    from fleet_rlm.workspace.storage import DaytonaSandboxWorkspaceStorage

    fs = MagicMock()
    fs.get_file_info.return_value = {"mode": "040755", "is_symlink": False, "symlink": False}
    fs.list_files.return_value = [{"path": item_path}]
    storage = DaytonaSandboxWorkspaceStorage(
        SimpleNamespace(fs=fs), volume_root="/workspace", root="/workspace/sessions/session-a/workspace"
    )

    with pytest.raises(UnsafePathError, match="workspace listing escapes"):
        storage.list_entries()
