"""Behavioral seams for per-Turn host-tool safeguards."""

from __future__ import annotations

import pytest

from fleet_rlm.rlm.runtime import (
    RunToolGuards,
    workspace_obligations,
)


def test_workspace_failure_is_cleared_only_by_successful_same_workspace_write() -> None:
    guards = RunToolGuards()
    original = {"path": "notes/report.md", "content": "old", "overwrite": False}
    other = {"path": "notes/other.md", "content": "new", "overwrite": True}

    guards.failed("write_workspace_text", original)
    guards.completed("read_workspace_text", {"path": "notes/report.md"}, {"content": "old", "eof": True})
    guards.completed("write_workspace_text", other, {"ok": True})

    assert guards.integrity.unresolved == ("session_workspace:notes/report.md",)

    repaired = {**original, "content": "new", "overwrite": True}
    guards.completed("write_workspace_text", repaired, {"ok": True})
    guards.completed("read_workspace_text", {"path": "notes/report.md"}, {"content": "new", "eof": True})
    assert guards.integrity.unresolved == ()


def test_required_target_scope_ignores_unrelated_diagnostic_mutations() -> None:
    guards = RunToolGuards(required_targets=frozenset({"session_workspace:notes/report.md"}))

    guards.failed("write_workspace_text", {"path": "notes/diagnostic.md"})
    guards.completed("write_workspace_text", {"path": "notes/report.md", "content": "new"}, {"ok": True})
    guards.completed("read_workspace_text", {"path": "notes/report.md"}, {"content": "new", "eof": True})

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
    guards = RunToolGuards(required_targets=workspace_obligations(task_text))

    guards.failed("write_workspace_text", {"path": "README"})

    assert guards.integrity.unresolved == ("session_workspace:README",)


def test_required_failure_remains_unresolved_after_unrelated_success() -> None:
    guards = RunToolGuards(required_targets=frozenset({"session_workspace:date.txt"}))

    guards.failed("write_workspace_text", {"path": "date.txt"})
    guards.completed(
        "write_workspace_text",
        {"path": "diagnostic.txt", "content": "ok"},
        {"ok": True},
    )

    assert guards.integrity.unresolved == ("session_workspace:date.txt",)


def test_unrelated_failure_does_not_block_verified_required_target() -> None:
    guards = RunToolGuards(required_targets=frozenset({"session_workspace:date.txt"}))

    guards.failed("write_workspace_text", {"path": "diagnostic.txt"})
    guards.completed(
        "write_workspace_text",
        {"path": "date.txt", "content": "2026-07-20"},
        {"ok": True},
    )
    guards.completed("read_workspace_text", {"path": "date.txt"}, {"content": "2026-07-20", "eof": True})

    assert guards.integrity.unresolved == ()


def test_local_filesystem_success_does_not_satisfy_required_workspace_target() -> None:
    guards = RunToolGuards(required_targets=frozenset({"session_workspace:date.txt"}))

    guards.failed("write_workspace_text", {"path": "date.txt"})
    # A Python-local ``open`` never reaches this ledger and cannot clear it.

    assert guards.integrity.unresolved == ("session_workspace:date.txt",)


def test_cleanup_warning_does_not_invalidate_verified_primary_mutation() -> None:
    guards = RunToolGuards(required_targets=frozenset({"session_workspace:date.txt"}))

    guards.failed("write_workspace_text", {"path": "date.txt"})
    guards.completed(
        "write_workspace_text",
        {"path": "date.txt", "content": "2026-07-20"},
        {"ok": True, "warnings": [{"code": "cleanup_failed"}]},
    )
    guards.completed("read_workspace_text", {"path": "date.txt"}, {"content": "2026-07-20", "eof": True})


def test_successful_append_repairs_the_same_required_workspace_target() -> None:
    guards = RunToolGuards(required_targets=frozenset({"session_workspace:date.txt"}))
    guards.failed("append_workspace_text", {"path": "date.txt", "content": "new"})
    assert guards.integrity.unresolved == ("session_workspace:date.txt",)

    guards.completed("append_workspace_text", {"path": "date.txt", "content": "new"}, {"ok": True})
    assert guards.integrity.unresolved == ()


def test_identical_tool_results_warn_once_without_terminally_failing_a_turn() -> None:
    guards = RunToolGuards()
    arguments = {"offset": 22, "limit": 5}
    eof = {"next_offset": None, "done": True, "messages": []}

    assert guards.completed("read_session_history", arguments, eof) is None
    assert guards.completed("read_session_history", arguments, eof) == "repeated tool call produced no progress"
    assert guards.completed("read_session_history", arguments, eof) is None


def test_project_write_failure_is_cleared_only_by_verified_project_read_back() -> None:
    guards = RunToolGuards()
    original = {"path": "fleet-rlm/review.md", "content": "old", "overwrite": False}
    other = {"path": "fleet-rlm/other.md", "content": "new", "overwrite": True}

    guards.failed("write_project_text", original)
    guards.completed("read_project_text", {"path": "fleet-rlm/review.md"}, {"content": "old", "eof": True})
    guards.completed("write_project_text", other, {"ok": True})

    assert guards.integrity.unresolved == ("project_workspace:fleet-rlm/review.md",)

    repaired = {**original, "content": "new", "overwrite": True}
    guards.completed("write_project_text", repaired, {"ok": True})
    guards.completed("read_project_text", {"path": "fleet-rlm/review.md"}, {"content": "new", "eof": True})
    assert guards.integrity.unresolved == ()


def test_project_obligations_use_the_projects_prefix_and_coexist_with_session_targets() -> None:
    request = "deliver the final report to projects/fleet-rlm/review.md and keep the working draft at notes/scratch.md"
    assert workspace_obligations(request) == frozenset(
        {"project_workspace:fleet-rlm/review.md", "session_workspace:notes/scratch.md"}
    )
    # Existing session_workspace fingerprints stay stable.
    assert workspace_obligations("write notes/report.md") == frozenset({"session_workspace:notes/report.md"})
    assert workspace_obligations("update workspace/notes/report.md") == frozenset({"session_workspace:notes/report.md"})


def test_project_tools_join_obligations_through_the_ledger_and_progress_guard() -> None:
    guards = RunToolGuards(required_targets=workspace_obligations("deliver the report to projects/fleet-rlm/review.md"))

    guards.failed("write_project_text", {"path": "fleet-rlm/scratch.md"})
    assert guards.integrity.unresolved == ()  # unrelated diagnostic mutations stay out of scope

    arguments = {"path": "fleet-rlm/review.md", "content": "conflicted", "overwrite": False}
    conflict = {"ok": False, "error": "conflict"}
    guards.failed("write_project_text", arguments)
    assert guards.integrity.unresolved == ("project_workspace:fleet-rlm/review.md",)

    # Identical repeated failing project writes hit the no-progress guard once.
    assert guards.completed("write_project_text", arguments, conflict) is None
    assert guards.completed("write_project_text", arguments, conflict) == "repeated tool call produced no progress"
    assert guards.completed("write_project_text", arguments, conflict) is None

    repaired = {**arguments, "content": "final", "overwrite": True}
    guards.completed("write_project_text", repaired, {"ok": True})
    guards.completed("read_project_text", {"path": "fleet-rlm/review.md"}, {"content": "final", "eof": True})
    assert guards.integrity.unresolved == ()


def test_project_target_strips_a_redundant_projects_prefix() -> None:
    guards = RunToolGuards(required_targets=frozenset({"project_workspace:fleet-rlm/review.md"}))

    guards.failed("write_project_text", {"path": "projects/fleet-rlm/review.md"})
    assert guards.integrity.unresolved == ("project_workspace:fleet-rlm/review.md",)


def test_tool_namespace_is_authoritative_over_path_prefixes() -> None:
    # A session-workspace tool executes its path verbatim inside the session
    # workspace, so a ``projects/...`` path must never satisfy (or even join)
    # a ``project_workspace:`` obligation.
    guards = RunToolGuards(required_targets=frozenset({"project_workspace:fleet-rlm/review.md"}))
    guards.failed("write_project_text", {"path": "fleet-rlm/review.md"})
    assert guards.integrity.unresolved == ("project_workspace:fleet-rlm/review.md",)

    guards.completed(
        "write_workspace_text",
        {"path": "projects/fleet-rlm/review.md", "content": "final", "overwrite": True},
        {"ok": True},
    )
    guards.completed(
        "read_workspace_text",
        {"path": "projects/fleet-rlm/review.md"},
        {"content": "final", "eof": True},
    )
    assert guards.integrity.unresolved == ("project_workspace:fleet-rlm/review.md",)

    # The mirrored crossing (a project tool writing under a ``workspace``
    # slug) stays in the project namespace instead of flipping to session.
    session_guards = RunToolGuards(required_targets=frozenset({"session_workspace:notes/report.md"}))
    session_guards.failed("edit_project_text", {"path": "workspace/notes/report.md"})
    assert session_guards.integrity.unresolved == ()


def test_delete_and_edit_tools_join_guard_targets_in_both_namespaces() -> None:
    guards = RunToolGuards()

    # Failed delete/edit mutations are tracked against the same stable
    # namespaced targets as writes.
    guards.failed("delete_workspace_path", {"path": "notes/stale.md"})
    guards.failed("edit_project_text", {"path": "projects/fleet-rlm/review.md"})
    assert guards.integrity.unresolved == (
        "project_workspace:fleet-rlm/review.md",
        "session_workspace:notes/stale.md",
    )

    # A successful mutation settles its obligation immediately (a deleted path
    # cannot be read back; an edit's content is not derivable from fragments).
    guards.completed("delete_workspace_path", {"path": "notes/stale.md"}, {"ok": True})
    assert guards.integrity.unresolved == ("project_workspace:fleet-rlm/review.md",)
    guards.completed("edit_project_text", {"path": "fleet-rlm/review.md", "old": "a", "new": "b"}, {"ok": True})
    assert guards.integrity.unresolved == ()

    # Session-namespace edit and project-namespace delete share the semantics.
    guards.failed("edit_workspace_text", {"path": "notes/date.txt"})
    guards.failed("delete_project_path", {"path": "fleet-rlm/review.md"})
    assert guards.integrity.unresolved == (
        "project_workspace:fleet-rlm/review.md",
        "session_workspace:notes/date.txt",
    )
    guards.completed("edit_workspace_text", {"path": "notes/date.txt", "old": "a", "new": "b"}, {"ok": True})
    guards.completed("delete_project_path", {"path": "projects/fleet-rlm/review.md"}, {"ok": True})
    assert guards.integrity.unresolved == ()


def test_delete_edit_targets_respect_required_target_scoping() -> None:
    guards = RunToolGuards(required_targets=frozenset({"session_workspace:notes/report.md"}))

    guards.failed("delete_workspace_path", {"path": "notes/diagnostic.md"})
    assert guards.integrity.unresolved == ()  # unrelated deletes stay out of scope

    guards.failed("edit_workspace_text", {"path": "notes/report.md"})
    assert guards.integrity.unresolved == ("session_workspace:notes/report.md",)
    guards.completed(
        "edit_workspace_text",
        {"path": "notes/report.md", "old": "draft", "new": "final"},
        {"ok": True},
    )
    assert guards.integrity.unresolved == ()
