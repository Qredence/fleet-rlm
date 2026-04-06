from __future__ import annotations

from fleet_rlm.api.routers.ws.completion import build_execution_completion_summary
from fleet_rlm.runtime.models import StreamEvent
from tests.ui.fixtures_ui import ts


def test_build_execution_completion_summary_preserves_run_result_and_guarantees_minimum_fields() -> (
    None
):
    event = StreamEvent(
        kind="final",
        text="done",
        payload={
            "runtime": {"run_id": "runtime-run", "runtime_mode": "daytona_pilot"},
            "run_result": {"task": "indexed", "status": "completed"},
            "summary": {"duration_ms": 12, "warnings": ["slow"]},
        },
        timestamp=ts(),
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
    event = StreamEvent(
        kind="final",
        text="answer text",
        payload={"runtime_mode": "modal_chat", "sources": [{"id": "src-1"}]},
        timestamp=ts(),
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
    event = StreamEvent(
        kind="final",
        text="raw fallback text",
        payload={
            "runtime_mode": "modal_chat",
            "final_artifact": {
                "kind": "markdown",
                "value": {"summary": "Structured final summary"},
                "finalization_mode": "SUBMIT",
            },
        },
        timestamp=ts(),
    )

    summary = build_execution_completion_summary(
        event=event,
        request_message="summarize",
        run_id="run-top-level-artifact",
    )

    assert summary["runtime_mode"] == "modal_chat"
    assert summary["final_artifact"]["finalization_mode"] == "SUBMIT"
    assert summary["final_artifact"]["value"]["summary"] == "Structured final summary"


def test_build_execution_completion_summary_builds_error_summary() -> None:
    event = StreamEvent(
        kind="error",
        text="boom",
        payload={"summary": {"duration_ms": 55}},
        timestamp=ts(),
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
    event = StreamEvent(
        kind="final",
        text="claimed success",
        payload={
            "runtime_degraded": True,
            "runtime_failure_category": "tool_execution_error",
        },
        timestamp=ts(),
    )

    summary = build_execution_completion_summary(
        event=event,
        request_message="please run",
        run_id="run-tool-error",
    )

    assert summary["status"] == "error"
    assert summary["termination_reason"] == "final"
    assert summary["final_artifact"]["value"]["summary"] == "claimed success"
