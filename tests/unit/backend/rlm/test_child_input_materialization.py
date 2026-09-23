"""Selected child inputs are copied under existing host tool authority."""

from __future__ import annotations

import time

import dspy
import pytest

from fleet_rlm.rlm.execution import materialize_child_inputs
from fleet_rlm.rlm.recursion import ChildRequest, ChildRuntimeAuthorizationError


def _tools(source: str, *, change_on_second_read: bool = False) -> dict[str, dspy.Tool]:
    reads = 0

    def stat_workspace_file(path: str) -> dict[str, object]:
        if path != "selected/evidence.txt":
            return {"ok": False}
        return {"ok": True, "entry": {"path": path, "kind": "file", "byte_size": len(source)}}

    def read_workspace_text(path: str, cursor: str | None = None, max_chars: int = 10_000) -> dict[str, object]:
        nonlocal reads
        assert path == "selected/evidence.txt"
        offset = int(cursor or 0)
        if offset == 0:
            reads += 1
        current = source
        if change_on_second_read and reads >= 2:
            current = source[:-1] + "X"
        end = min(len(current), offset + max_chars)
        return {
            "ok": True,
            "content": current[offset:end],
            "next_cursor": str(end) if end < len(current) else None,
            "eof": end >= len(current),
            "byte_size": len(current),
        }

    return {
        "stat_workspace_file": dspy.Tool(stat_workspace_file),
        "read_workspace_text": dspy.Tool(read_workspace_text),
    }


def test_child_materialization_copies_large_authorized_source() -> None:
    source = "record\n" * 20_000
    request = ChildRequest(task="Inspect all records", inputs=("selected/evidence.txt",))
    staged = materialize_child_inputs(
        request,
        tools=_tools(source),
        check_authority=lambda: None,
        turn_deadline=time.monotonic() + 10,
    )
    assert staged == {"selected/evidence.txt": source.encode("utf-8")}
    assert len(staged["selected/evidence.txt"]) > 50_000


def test_child_materialization_rejects_source_change() -> None:
    request = ChildRequest(task="Inspect selected source", inputs=("selected/evidence.txt",))
    with pytest.raises(ValueError, match="changed during child staging"):
        materialize_child_inputs(
            request,
            tools=_tools("same-size source", change_on_second_read=True),
            check_authority=lambda: None,
            turn_deadline=time.monotonic() + 10,
        )


def test_child_materialization_detects_changes_across_a_multi_file_snapshot() -> None:
    source = {"selected/a.txt": "before", "selected/b.txt": "trigger"}
    second_file_reads = 0

    def stat_workspace_file(path: str) -> dict[str, object]:
        if path == "selected":
            return {"ok": True, "entry": {"path": path, "kind": "directory"}}
        content = source.get(path)
        if content is None:
            return {"ok": False}
        return {"ok": True, "entry": {"path": path, "kind": "file", "byte_size": len(content)}}

    def list_workspace_files(
        path: str,
        limit: int = 100,
        after: str | None = None,
    ) -> dict[str, object]:
        assert path == "selected"
        assert limit == 100
        assert after is None
        return {
            "ok": True,
            "entries": [{"path": name, "kind": "file"} for name in sorted(source)],
            "truncated": False,
            "next_cursor": None,
        }

    def read_workspace_text(
        path: str,
        cursor: str | None = None,
        max_chars: int = 10_000,
    ) -> dict[str, object]:
        nonlocal second_file_reads
        current = source[path]
        if path == "selected/b.txt" and cursor is None:
            second_file_reads += 1
            if second_file_reads == 1:
                source["selected/a.txt"] = "changed"
        return {
            "ok": True,
            "content": current[:max_chars],
            "next_cursor": None,
            "eof": True,
            "byte_size": len(current),
        }

    tools = {
        "stat_workspace_file": dspy.Tool(stat_workspace_file),
        "list_workspace_files": dspy.Tool(list_workspace_files),
        "read_workspace_text": dspy.Tool(read_workspace_text),
    }
    with pytest.raises(ValueError, match="changed during child staging"):
        materialize_child_inputs(
            ChildRequest(task="Inspect both files", inputs=("selected",)),
            tools=tools,
            check_authority=lambda: None,
            turn_deadline=time.monotonic() + 10,
        )


def test_child_materialization_rejects_unauthorized_reference() -> None:
    request = ChildRequest(task="Inspect selected source", inputs=("unrelated/secret.txt",))
    with pytest.raises(ChildRuntimeAuthorizationError):
        materialize_child_inputs(
            request,
            tools=_tools("source"),
            check_authority=lambda: None,
            turn_deadline=time.monotonic() + 10,
        )
