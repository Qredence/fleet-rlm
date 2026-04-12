from __future__ import annotations

from fleet_rlm.api.routers.ws.completion import build_execution_completion_summary
from fleet_rlm.worker import WorkspaceEvent
from tests.ui.fixtures_ui import ts


def test_build_execution_completion_summary_preserves_run_result_and_guarantees_minimum_fields() -> (
    None
):
    event = WorkspaceEvent(
        kind="final",
        text="done",
        payload={
            "runtime": {"run_id": "runtime-run", "runtime_mode": "daytona_pilot"},
            "run_result": {"task": "indexed", "status": "completed"},
            "summary": {"duration_ms": 12, "warnings": ["slow"]},
        },
        timestamp=ts(),
        terminal=True,
    )

    summary = build_execution_completion_summary(
        event=event,
        request_message="hello",
        run_id="fallback-run",
    )

    assert summary["run_id"] == "runtime-run"
    assert summary["runtime_mode"] == "daytona_pilot"
    assert summary["task"] == "indexed"
    assert summary["status"] == "completed"
    assert summary["warnings"] == ["slow"]
    assert summary["summary"]["duration_ms"] == 12
    assert summary["summary"]["termination_reason"] == "final"
    assert summary["final_artifact"]["value"]["summary"] == "done"


def test_build_execution_completion_summary_builds_fallback_final_artifact() -> None:
    event = WorkspaceEvent(
        kind="final",
        text="answer text",
        payload={"runtime_mode": "daytona_pilot", "sources": [{"id": "src-1"}]},
        timestamp=ts(),
        terminal=True,
    )

    summary = build_execution_completion_summary(
        event=event,
        request_message="what happened?",
        run_id="run-123",
    )

    assert summary["run_id"] == "run-123"
    assert summary["status"] == "completed"
    assert summary["final_artifact"]["value"]["text"] == "answer text"
    assert summary["sources"] == [{"id": "src-1"}]
    assert summary["summary"]["error"] is None


def test_build_execution_completion_summary_prefers_top_level_final_artifact_when_present() -> (
    None
):
    event = WorkspaceEvent(
        kind="final",
        text="raw fallback text",
        payload={
            "runtime_mode": "daytona_pilot",
            "final_artifact": {
                "kind": "markdown",
                "value": {"summary": "Structured final summary"},
                "finalization_mode": "SUBMIT",
            },
        },
        timestamp=ts(),
        terminal=True,
    )

    summary = build_execution_completion_summary(
        event=event,
        request_message="summarize",
        run_id="run-top-level-artifact",
    )

    assert summary["runtime_mode"] == "daytona_pilot"
    assert summary["final_artifact"]["finalization_mode"] == "SUBMIT"
    assert summary["final_artifact"]["value"]["summary"] == "Structured final summary"


def test_build_execution_completion_summary_builds_error_summary() -> None:
    event = WorkspaceEvent(
        kind="error",
        text="boom",
        payload={"summary": {"duration_ms": 55}},
        timestamp=ts(),
        terminal=True,
    )

    summary = build_execution_completion_summary(
        event=event,
        request_message="please run",
        run_id="run-err",
    )

    assert summary["status"] == "error"
    assert summary["termination_reason"] == "error"
    assert summary["final_artifact"] is None
    assert summary["summary"]["duration_ms"] == 55
    assert summary["summary"]["error"] == "boom"


def test_build_execution_completion_summary_marks_tool_error_final_as_error() -> None:
    event = WorkspaceEvent(
        kind="final",
        text="claimed success",
        payload={
            "runtime_degraded": True,
            "runtime_failure_category": "tool_execution_error",
        },
        timestamp=ts(),
        terminal=True,
    )

    summary = build_execution_completion_summary(
        event=event,
        request_message="please run",
        run_id="run-tool-error",
    )

    assert summary["status"] == "error"
    assert summary["termination_reason"] == "final"
    assert summary["final_artifact"]["value"]["summary"] == "claimed success"


def test_build_execution_completion_summary_surfaces_human_review_terminal_state() -> (
    None
):
    event = WorkspaceEvent(
        kind="final",
        text="Need a human to review the risky repair.",
        payload={
            "recursive_repair": {
                "repair_mode": "needs_human_review",
                "repair_target": "Review the risky filesystem mutation.",
                "repair_steps": ["Confirm the proposed workspace mutation."],
                "repair_rationale": "The remaining repair path is too risky.",
            },
            "final_reasoning": "Recursive repair requested a human review checkpoint.",
        },
        timestamp=ts(),
        terminal=True,
    )

    summary = build_execution_completion_summary(
        event=event,
        request_message="repair the workspace",
        run_id="run-human-review",
    )

    assert summary["status"] == "needs_human_review"
    assert summary["termination_reason"] == "needs_human_review"
    assert summary["summary"]["termination_reason"] == "needs_human_review"
    assert summary["human_review"]["required"] is True
    assert (
        summary["summary"]["human_review"]["reason"]
        == "Recursive repair requested a human review checkpoint."
    )
    assert (
        summary["human_review"]["repair_target"]
        == "Review the risky filesystem mutation."
    )


def test_build_execution_completion_summary_normalizes_human_review_payload() -> None:
    event = WorkspaceEvent(
        kind="final",
        text="Need review",
        payload={
            "human_review": {
                "required": True,
                "repair_mode": "needs_human_review",
                "repair_target": "Review target",
                "repair_steps": [" first ", "", 42, {"ignored": True}],
            }
        },
        timestamp=ts(),
        terminal=True,
    )

    summary = build_execution_completion_summary(
        event=event,
        request_message="review this",
        run_id="run-human-review-normalized",
    )

    assert summary["human_review"]["reason"] == (
        "Recursive repair requested human review before continuing."
    )
    assert summary["human_review"]["repair_steps"] == ["first"]


def test_build_execution_completion_summary_omits_human_review_when_absent() -> None:
    event = WorkspaceEvent(
        kind="final",
        text="done",
        payload={"summary": {"duration_ms": 1}},
        timestamp=ts(),
        terminal=True,
    )

    summary = build_execution_completion_summary(
        event=event,
        request_message="finish",
        run_id="run-no-human-review",
    )

    assert "human_review" not in summary
    assert "human_review" not in summary["summary"]


def test_build_execution_completion_summary_normalizes_recursive_repair_human_review() -> (
    None
):
    event = WorkspaceEvent(
        kind="final",
        text="Need review",
        payload={
            "recursive_repair": {
                "repair_mode": "needs_human_review",
                "repair_steps": "not-a-list",
            }
        },
        timestamp=ts(),
        terminal=True,
    )

    summary = build_execution_completion_summary(
        event=event,
        request_message="review this",
        run_id="run-recursive-human-review",
    )

    assert summary["human_review"]["reason"] == (
        "Recursive repair requested human review before continuing."
    )
    assert summary["human_review"]["repair_steps"] == []
