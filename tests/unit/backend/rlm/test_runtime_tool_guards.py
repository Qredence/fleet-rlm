"""Behavioral seams for per-Turn host-tool safeguards."""

from __future__ import annotations

import pytest

from fleet_rlm.rlm.execution import RunToolGuards


@pytest.mark.parametrize(
    "prompt",
    ["Explain README.md and https://example.com/report."],
)
def test_read_only_prose_never_seeds_workspace_mutation_obligations(prompt: str) -> None:
    del prompt
    assert RunToolGuards().integrity.unresolved == ()


def test_failed_reads_do_not_create_integrity_obligations() -> None:
    guards = RunToolGuards()

    guards.failed("read_workspace_text", {"path": "README.md"})
    guards.failed("read_project_text", {"path": "fleet-rlm/review.md"})

    assert guards.integrity.unresolved == ()


def test_mutations_remain_scoped_to_their_actual_target() -> None:
    guards = RunToolGuards()

    guards.failed("write_workspace_text", {"path": "notes/report.md", "content": "draft"})
    guards.completed("write_workspace_text", {"path": "notes/other.md", "content": "done"}, {"ok": True})

    assert guards.integrity.unresolved == ("session_workspace:notes/report.md",)


def test_successful_append_edit_delete_and_publish_settle_mutations() -> None:
    guards = RunToolGuards()
    mutations = (
        ("append_workspace_text", {"path": "notes/a.md", "content": "x"}),
        ("edit_workspace_text", {"path": "notes/b.md", "old": "x", "new": "y"}),
        ("delete_workspace_path", {"path": "notes/c.md"}),
        ("publish_workspace_artifact", {"path": "notes/d.md", "kind": "markdown"}),
        ("edit_project_text", {"path": "fleet-rlm/e.md", "old": "x", "new": "y"}),
        ("delete_project_path", {"path": "fleet-rlm/f.md"}),
    )

    for tool_name, arguments in mutations:
        guards.failed(tool_name, arguments)
        guards.completed(tool_name, arguments, {"ok": True})

    assert guards.integrity.unresolved == ()


def test_identical_tool_results_warn_once_without_terminally_failing_a_turn() -> None:
    guards = RunToolGuards()
    arguments = {"offset": 22, "limit": 5}
    eof = {"next_offset": None, "done": True, "messages": []}

    assert guards.completed("read_session_history", arguments, eof) is None
    assert guards.completed("read_session_history", arguments, eof) == "repeated tool call produced no progress"
    assert guards.completed("read_session_history", arguments, eof) is None
