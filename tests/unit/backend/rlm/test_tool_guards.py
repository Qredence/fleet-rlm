"""Behavioral seams for per-Turn host-tool safeguards."""

from __future__ import annotations

import pytest

from fleet_rlm.rlm.tool_guards import TurnToolGuards, workspace_obligations


def test_workspace_failure_is_cleared_only_by_successful_same_workspace_write() -> None:
    guards = TurnToolGuards()
    original = {"path": "notes/report.md", "content": "old", "overwrite": False}
    other = {"path": "notes/other.md", "content": "new", "overwrite": True}

    guards.failed("write_workspace_text", original)
    guards.completed("read_workspace_text", {"path": "notes/report.md"}, "old")
    guards.completed("write_workspace_text", other, {"ok": True})

    assert guards.integrity.unresolved == ("session_workspace:notes/report.md",)

    repaired = {**original, "content": "new", "overwrite": True}
    guards.completed("write_workspace_text", repaired, {"ok": True})
    guards.completed("read_workspace_text", {"path": "notes/report.md"}, "new")
    assert guards.integrity.unresolved == ()


def test_required_target_scope_ignores_unrelated_diagnostic_mutations() -> None:
    guards = TurnToolGuards(required_targets=frozenset({"session_workspace:notes/report.md"}))

    guards.failed("write_workspace_text", {"path": "notes/diagnostic.md"})
    guards.completed("write_workspace_text", {"path": "notes/report.md", "content": "new"}, {"ok": True})
    guards.completed("read_workspace_text", {"path": "notes/report.md"}, "new")

    assert guards.integrity.unresolved == ()


@pytest.mark.parametrize(
    "task_text",
    [
        "save this in the session workspace",
        "write README in the session workspace",
        "update .env in the session workspace",
    ],
)
def test_unrecognized_workspace_target_preserves_fail_closed_tracking(task_text: str) -> None:
    guards = TurnToolGuards(required_targets=workspace_obligations(task_text))

    guards.failed("write_workspace_text", {"path": "README"})

    assert guards.integrity.unresolved == ("session_workspace:README",)


def test_required_failure_remains_unresolved_after_unrelated_success() -> None:
    guards = TurnToolGuards(required_targets=frozenset({"session_workspace:date.txt"}))

    guards.failed("write_workspace_text", {"path": "date.txt"})
    guards.completed(
        "write_workspace_text",
        {"path": "diagnostic.txt", "content": "ok"},
        {"ok": True},
    )

    assert guards.integrity.unresolved == ("session_workspace:date.txt",)


def test_unrelated_failure_does_not_block_verified_required_target() -> None:
    guards = TurnToolGuards(required_targets=frozenset({"session_workspace:date.txt"}))

    guards.failed("write_workspace_text", {"path": "diagnostic.txt"})
    guards.completed(
        "write_workspace_text",
        {"path": "date.txt", "content": "2026-07-20"},
        {"ok": True},
    )
    guards.completed("read_workspace_text", {"path": "date.txt"}, "2026-07-20")

    assert guards.integrity.unresolved == ()


def test_local_filesystem_success_does_not_satisfy_required_workspace_target() -> None:
    guards = TurnToolGuards(required_targets=frozenset({"session_workspace:date.txt"}))

    guards.failed("write_workspace_text", {"path": "date.txt"})
    # A Python-local ``open`` never reaches this ledger and cannot clear it.

    assert guards.integrity.unresolved == ("session_workspace:date.txt",)


def test_cleanup_warning_does_not_invalidate_verified_primary_mutation() -> None:
    guards = TurnToolGuards(required_targets=frozenset({"session_workspace:date.txt"}))

    guards.failed("write_workspace_text", {"path": "date.txt"})
    guards.completed(
        "write_workspace_text",
        {"path": "date.txt", "content": "2026-07-20"},
        {"ok": True, "warnings": [{"code": "cleanup_failed"}]},
    )
    guards.completed("read_workspace_text", {"path": "date.txt"}, "2026-07-20")

    assert guards.integrity.unresolved == ()


def test_identical_tool_results_warn_once_without_terminally_failing_a_turn() -> None:
    guards = TurnToolGuards()
    arguments = {"offset": 22, "limit": 5}
    eof = {"next_offset": None, "done": True, "messages": []}

    assert guards.completed("read_session_history", arguments, eof) is None
    assert guards.completed("read_session_history", arguments, eof) == "repeated tool call produced no progress"
    assert guards.completed("read_session_history", arguments, eof) is None
