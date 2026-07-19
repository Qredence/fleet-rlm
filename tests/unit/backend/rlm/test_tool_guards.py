"""Behavioral seams for per-Turn host-tool safeguards."""

from __future__ import annotations

from fleet_rlm.rlm.tool_guards import TurnToolGuards


def test_workspace_failure_is_cleared_only_by_successful_same_workspace_write() -> None:
    guards = TurnToolGuards()
    original = {"path": "notes/report.md", "content": "old", "overwrite": False}
    other = {"path": "notes/other.md", "content": "new", "overwrite": True}

    guards.failed("write_workspace_text", original)
    guards.completed("read_workspace_text", {"path": "notes/report.md"}, "old")
    guards.completed("write_workspace_text", other, {"ok": True})

    assert guards.integrity.unresolved == ("session_workspace:notes/report.md",)

    guards.completed("write_workspace_text", {**original, "overwrite": True}, {"ok": True})
    assert guards.integrity.unresolved == ()


def test_identical_tool_results_warn_once_without_terminally_failing_a_turn() -> None:
    guards = TurnToolGuards()
    arguments = {"offset": 22, "limit": 5}
    eof = {"next_offset": None, "done": True, "messages": []}

    assert guards.completed("read_session_history", arguments, eof) is None
    assert guards.completed("read_session_history", arguments, eof) == "repeated tool call produced no progress"
    assert guards.completed("read_session_history", arguments, eof) is None
