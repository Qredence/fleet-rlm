from __future__ import annotations

from fleet_rlm.runtime.quality.transcript_exports import (
    build_transcript_dataset_rows,
)


def test_build_transcript_dataset_rows_reflection_defaults():
    rows, label = build_transcript_dataset_rows(
        module_slug="reflect-and-revise",
        turns=[("Investigate the bug", "I found the failing path.")],
    )

    assert label == "Reflect & Revise"
    assert rows == [
        {
            "user_request": "Investigate the bug",
            "working_memory_summary": "",
            "current_plan": "",
            "latest_sandbox_evidence": "",
            "latest_tool_or_code_result": "",
            "loop_state": "",
            "next_action": "finalize",
            "rationale": "I found the failing path.",
        }
    ]


def test_build_transcript_dataset_rows_context_selection_defaults():
    rows, _label = build_transcript_dataset_rows(
        module_slug="context-selection",
        turns=[("Summarize the repo state", "The latest change touched the router.")],
    )

    row = rows[0]
    assert row["user_request"] == "Summarize the repo state"
    assert row["current_plan"] == ""
    assert row["loop_state"] == ""
    assert row["working_memory_catalog"] == []
    assert row["recent_sandbox_evidence_catalog"] == []
    assert row["latest_tool_or_code_result"] == ""
    assert row["context_budget"] == 0
    assert row["selected_memory_handles"] == []
    assert row["selected_evidence_ids"] == []
    assert row["assembled_context_summary"] == "The latest change touched the router."
