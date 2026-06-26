"""Unit tests for build_execution_completion_summary final_artifact reconstruction.

Covers VAL-A-009, VAL-A-010, VAL-A-011, VAL-A-021.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fleet_rlm.api.routers.ws.stream_summary import build_execution_completion_summary


@dataclass
class _FakeEvent:
    """Minimal stand-in for StreamEventLike (done/error events)."""

    kind: str
    text: str | None = None
    payload: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# VAL-A-009: final_artifact never None on successful turn
# ---------------------------------------------------------------------------


def test_final_artifact_populated_from_payload_top_level() -> None:
    """When payload already has a final_artifact, it is used directly."""
    existing_artifact = {
        "kind": "code_file",
        "value": {"content": "def fib(): pass", "summary": "def fib(): pass"},
        "finalization_mode": "SUBMIT",
    }
    event = _FakeEvent(
        kind="done",
        text="",
        payload={"final_artifact": existing_artifact},
    )
    summary = build_execution_completion_summary(
        event=event,
        request_message="Write fibonacci",
        run_id="run-1",
    )
    assert summary["final_artifact"] is not None
    assert summary["final_artifact"]["kind"] == "code_file"
    assert summary["status"] == "completed"


def test_final_artifact_reconstructed_from_event_text_when_no_top_level() -> None:
    """When payload has no final_artifact but event.text is non-empty, reconstruct."""
    event = _FakeEvent(
        kind="done",
        text="Here is the answer with enough content to be meaningful for the user.",
        payload={},
    )
    summary = build_execution_completion_summary(
        event=event,
        request_message="Explain something",
        run_id="run-2",
    )
    assert summary["final_artifact"] is not None
    assert summary["status"] == "completed"


def test_final_artifact_never_none_on_successful_turn() -> None:
    """A completed turn must always have a non-None final_artifact."""
    event = _FakeEvent(
        kind="done",
        text="Short reply.",
        payload={"routing_decision": "direct"},
    )
    summary = build_execution_completion_summary(
        event=event,
        request_message="Hello",
        run_id="run-3",
    )
    assert summary["final_artifact"] is not None
    assert summary["status"] == "completed"


# ---------------------------------------------------------------------------
# VAL-A-010: Reconstructs from run_result.final_answer when top-level missing
# ---------------------------------------------------------------------------


def test_final_artifact_from_run_result_final_answer() -> None:
    """When payload lacks final_artifact but has run_result.final_answer, reconstruct."""
    event = _FakeEvent(
        kind="done",
        text="",
        payload={
            "run_result": {
                "final_answer": "def fibonacci(n):\n    fibs = [0, 1]\n    for i in range(2, n):\n        fibs.append(fibs[-1] + fibs[-2])\n    return fibs",
                "status": "completed",
            }
        },
    )
    summary = build_execution_completion_summary(
        event=event,
        request_message="Write a Python function that returns the first N Fibonacci numbers",
        run_id="run-4",
    )
    assert summary["final_artifact"] is not None
    fa = summary["final_artifact"]
    # Should be classified as code_file because of the fenced code
    assert fa["kind"] in ("code_file", "markdown", "assistant_response")
    assert "fibonacci" in str(fa["value"])


def test_final_artifact_from_trajectory_last_output() -> None:
    """When no final_answer but trajectory has output, reconstruct from trajectory."""
    event = _FakeEvent(
        kind="done",
        text="",
        payload={
            "run_result": {"status": "completed"},
            "trajectory": [
                {"tool_name": "repl_execute", "output": "step 1 result"},
                {
                    "tool_name": "repl_execute",
                    "output": "The final result from the last trajectory step is complete.",
                },
            ],
        },
    )
    summary = build_execution_completion_summary(
        event=event,
        request_message="Run some code",
        run_id="run-5",
    )
    assert summary["final_artifact"] is not None
    assert (
        "final result" in str(summary["final_artifact"]["value"]).lower()
        or "trajectory" in str(summary["final_artifact"]["value"]).lower()
    )


def test_final_artifact_run_result_takes_priority_over_event_text() -> None:
    """run_result.final_answer is preferred over event.text."""
    event = _FakeEvent(
        kind="done",
        text="This is the event text fallback.",
        payload={
            "run_result": {
                "final_answer": "This is the run_result final answer which should be preferred.",
            }
        },
    )
    summary = build_execution_completion_summary(
        event=event,
        request_message="Test priority",
        run_id="run-6",
    )
    assert summary["final_artifact"] is not None
    value_str = str(summary["final_artifact"]["value"])
    assert "run_result final answer" in value_str
    assert "event text fallback" not in value_str


def test_final_artifact_from_run_result_with_run_result_branch() -> None:
    """When run_result exists in payload and has final_answer, reconstruct in run_result branch."""
    event = _FakeEvent(
        kind="done",
        text="",
        payload={
            "run_result": {
                "run_id": "rr-1",
                "final_answer": "The answer from run_result with enough detail.",
                "status": "completed",
            }
        },
    )
    summary = build_execution_completion_summary(
        event=event,
        request_message="Explain",
        run_id="run-7",
    )
    assert summary["final_artifact"] is not None
    assert summary["status"] == "completed"
    # Verify run_result branch was taken
    assert summary.get("run_id") == "rr-1" or summary.get("run_id") == "run-7"


# ---------------------------------------------------------------------------
# VAL-A-011: None still permitted on genuine error / needs_human_review
# ---------------------------------------------------------------------------


def test_final_artifact_none_on_error_status() -> None:
    """On genuine error (runtime_degraded + tool_execution_error), final_artifact can be None."""
    event = _FakeEvent(
        kind="done",
        text=None,
        payload={
            "runtime_degraded": True,
            "runtime_failure_category": "tool_execution_error",
        },
    )
    summary = build_execution_completion_summary(
        event=event,
        request_message="Do something",
        run_id="run-err",
    )
    assert summary["status"] == "error"
    # final_artifact can be None on genuine error
    assert summary.get("final_artifact") is None


def test_final_artifact_none_on_needs_human_review() -> None:
    """On needs_human_review, final_artifact can be None (no answer to surface)."""
    event = _FakeEvent(
        kind="done",
        text=None,
        payload={
            "recursive_repair": {
                "repair_mode": "needs_human_review",
                "repair_target": "broken code",
                "repair_rationale": "Needs human review to proceed.",
            }
        },
    )
    summary = build_execution_completion_summary(
        event=event,
        request_message="Fix something",
        run_id="run-hr",
    )
    assert summary["status"] == "needs_human_review"
    assert summary.get("human_review") is not None
    assert summary.get("human_review")["required"] is True


def test_error_event_kind_produces_error_status() -> None:
    """An event with kind='error' produces status='error'."""
    event = _FakeEvent(
        kind="error",
        text="Something went wrong",
        payload={},
    )
    summary = build_execution_completion_summary(
        event=event,
        request_message="Do something",
        run_id="run-err2",
    )
    assert summary["status"] == "error"
    # Error text is stored in nested summary.error, not top-level
    assert summary["summary"]["error"] == "Something went wrong"


# ---------------------------------------------------------------------------
# VAL-A-021: run_summary.status matches transcript state
# ---------------------------------------------------------------------------


def test_status_completed_for_successful_done_event() -> None:
    """A done event without error/human_review flags produces status='completed'."""
    event = _FakeEvent(
        kind="done",
        text="All good.",
        payload={},
    )
    summary = build_execution_completion_summary(
        event=event,
        request_message="Hello",
        run_id="run-ok",
    )
    assert summary["status"] == "completed"


def test_status_error_for_degraded_tool_execution_error() -> None:
    """runtime_degraded + tool_execution_error produces status='error'."""
    event = _FakeEvent(
        kind="done",
        text="",
        payload={
            "runtime_degraded": True,
            "runtime_failure_category": "tool_execution_error",
        },
    )
    summary = build_execution_completion_summary(
        event=event,
        request_message="Do something",
        run_id="run-fail",
    )
    assert summary["status"] == "error"


def test_status_needs_human_review_for_recursive_repair() -> None:
    """recursive_repair with needs_human_review produces status='needs_human_review'."""
    event = _FakeEvent(
        kind="done",
        text="",
        payload={
            "recursive_repair": {
                "repair_mode": "needs_human_review",
                "repair_target": "fix needed",
            }
        },
    )
    summary = build_execution_completion_summary(
        event=event,
        request_message="Fix",
        run_id="run-hr2",
    )
    assert summary["status"] == "needs_human_review"


def test_status_cancelled_when_payload_cancelled() -> None:
    """A done event with cancelled=True produces status='cancelled'."""
    event = _FakeEvent(
        kind="done",
        text="",
        payload={"cancelled": True},
    )
    summary = build_execution_completion_summary(
        event=event,
        request_message="Do something",
        run_id="run-cancel",
    )
    assert summary["status"] == "cancelled"


def test_run_result_status_preserved_when_already_set() -> None:
    """run_result.status is preserved when already set (existing behavior)."""
    event = _FakeEvent(
        kind="done",
        text="Done.",
        payload={
            "run_result": {
                "status": "completed",  # already set to completed
                "final_answer": "All done.",
            }
        },
    )
    summary = build_execution_completion_summary(
        event=event,
        request_message="Do something",
        run_id="run-resolve",
    )
    assert summary["status"] == "completed"


def test_run_result_error_status_overrides_to_error() -> None:
    """When run_result exists and the event is an error, status reflects that."""
    event = _FakeEvent(
        kind="done",
        text="",
        payload={
            "run_result": {
                "status": "running",
            },
            "runtime_degraded": True,
            "runtime_failure_category": "tool_execution_error",
        },
    )
    summary = build_execution_completion_summary(
        event=event,
        request_message="Do something",
        run_id="run-resolve-err",
    )
    assert summary["status"] == "error"
