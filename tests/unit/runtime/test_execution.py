from __future__ import annotations

import json
from types import SimpleNamespace


def test_storage_paths_normalize_mount_layouts() -> None:
    from fleet_rlm.runtime.execution.storage_paths import mounted_storage_roots, runtime_storage_roots

    data_roots = mounted_storage_roots("/data/memory")
    current = mounted_storage_roots("/home/daytona/memory")
    interpreter_roots = runtime_storage_roots(SimpleNamespace(volume_mount_path="/srv/runtime/memory"))  # ty: ignore[invalid-argument-type]

    assert data_roots.mounted_root == "/data"
    assert data_roots.memory_root == "/data/memory"
    assert data_roots.artifacts_root == "/data/artifacts"
    assert data_roots.memories_root == "/data/memories"
    assert data_roots.knowledge_root == "/data/knowledge"
    assert data_roots.sessions_root == "/data/sessions"
    assert data_roots.allowed_root == "/data"

    assert current.mounted_root == "/home/daytona/memory"
    assert current.buffers_root == "/home/daytona/memory/buffers"
    assert current.meta_root == "/home/daytona/memory/meta"

    assert interpreter_roots.mounted_root == "/srv/runtime"
    assert interpreter_roots.memory_root == "/srv/runtime/memory"


def test_streaming_event_helpers_parse_tool_status_and_results() -> None:
    from fleet_rlm.runtime.execution.streaming_events import (
        classify_tool_event_kind,
        is_terminal_stream_event_kind,
        parse_tool_call_payload,
        parse_tool_call_status,
        parse_tool_result_payload,
        parse_tool_result_status,
    )

    assert is_terminal_stream_event_kind("done") is True
    assert is_terminal_stream_event_kind("status") is False
    assert classify_tool_event_kind("list_files") == "tool_call"

    assert parse_tool_call_status("Calling tool: list_files(path='src')") == "tool call: list_files(path='src')"
    assert parse_tool_call_payload("Calling tool: list_files(path='src')") == {
        "raw_status": "Calling tool: list_files(path='src')",
        "raw_call": "list_files(path='src')",
        "tool_name": "list_files",
        "tool_args": "path='src'",
        "tool_input": "path='src'",
    }

    assert parse_tool_result_status("Tool finished.") == "tool result: finished"
    assert parse_tool_result_status("Tool result: wrote file") == "tool result: completed"
    assert parse_tool_result_payload("Tool result: wrote file", tool_name="write_file") == {
        "raw_status": "Tool result: wrote file",
        "tool_name": "write_file",
        "tool_output": "wrote file",
    }


def test_try_parse_hitl_request_builds_status_event() -> None:
    from fleet_rlm.runtime.execution.streaming_events import try_parse_hitl_request

    clarification = try_parse_hitl_request(
        "clarification_questions",
        {"tool_output": json.dumps({"questions": ["Which repo?", "Which branch?"]})},
    )
    memory_review = try_parse_hitl_request(
        "memory_action_intent",
        {"tool_output": json.dumps({"intent": "delete", "requires_confirmation": True})},
    )

    assert clarification is not None
    assert clarification.kind == "status"
    assert clarification.payload["options"] == ["Which repo?", "Which branch?"]
    assert clarification.payload["requires_response"] is True

    assert memory_review is not None
    assert memory_review.text == "This memory action requires confirmation."
    assert memory_review.payload["action"] == "delete"


def test_normalize_trajectory_truncates_long_output_and_drops_terminal_thought() -> None:
    from fleet_rlm.runtime.execution.streaming_events import _normalize_trajectory

    long_output = "x" * 5_200
    trajectory = _normalize_trajectory(
        {
            "thought_1": "Inspect files",
            "tool_name_1": "list_files",
            "input_1": "src",
            "output_1": long_output,
            "thought_2": "Final hidden reasoning",
            "tool_name_2": "finish",
            "output_2": "done",
        }
    )

    assert trajectory[0]["tool_name"] == "list_files"
    assert trajectory[0]["input"] == "src"
    assert trajectory[0]["output_truncated"] is True
    assert trajectory[0]["output_length"] == len(long_output)
    assert "characters omitted" in trajectory[0]["output"]
    assert "thought" not in trajectory[1]
    assert trajectory[1]["tool_name"] == "finish"


def test_normalize_trajectory_accepts_structured_step_list() -> None:
    from fleet_rlm.runtime.execution.streaming_events import _normalize_trajectory

    trajectory = _normalize_trajectory(
        [
            {
                "thought": "Run code",
                "tool_name": "repl_execute",
                "input": {"code_preview": "print(1)"},
                "observation": {"success": True},
            },
            {
                "thought": "Final hidden reasoning",
                "tool_name": "finish",
                "output": "done",
            },
        ]
    )

    assert trajectory[0]["tool_name"] == "repl_execute"
    assert trajectory[0]["observation"] == {"success": True}
    assert "thought" not in trajectory[1]
    assert trajectory[1]["tool_name"] == "finish"


def test_build_final_payload_collects_sources_citations_and_human_review() -> None:
    from fleet_rlm.runtime.execution.streaming_events import _build_final_payload, _normalize_trajectory

    citation = {
        "title": "Design doc",
        "url": "https://example.com/spec",
        "source_id": "doc-1",
        "anchor_id": "anchor-1",
        "quote": "Important evidence",
    }
    trajectory = {
        "steps": [
            {"tool_name": "search", "output": json.dumps({"citations": [citation, citation]})},
            {"tool_name": "finish", "output": "done", "thought": "hidden"},
        ]
    }
    final_prediction = SimpleNamespace(
        recursive_repair={
            "repair_mode": "needs_human_review",
            "repair_target": "runtime/tools",
            "repair_steps": ["Confirm the file selection."],
        },
        final_reasoning="Need a human confirmation before continuing.",
    )

    payload = _build_final_payload(
        final_prediction=final_prediction,  # ty: ignore[invalid-argument-type]
        trajectory={"steps": _normalize_trajectory(trajectory)},
        history_turns=4,
        guardrail_warnings=["watch output size"],
        turn_metrics={"token_count": 7},
        fallback=False,
        effective_max_iters=8,
    )

    assert payload["schema_version"] == 2
    assert payload["history_turns"] == 4
    assert payload["guardrail_warnings"] == ["watch output size"]
    assert payload["token_count"] == 7
    assert len(payload["citations"]) == 1
    assert payload["citations"][0]["title"] == "Design doc"
    assert payload["sources"] == [
        {
            "source_id": "doc-1",
            "kind": "web",
            "title": "Design doc",
            "canonical_url": "https://example.com/spec",
            "display_url": "https://example.com/spec",
            "description": None,
            "quote": "Important evidence",
        }
    ]
    assert payload["citation_anchors"] == [
        {
            "anchor_id": "anchor-1",
            "source_id": "doc-1",
            "number": "1",
            "start_char": None,
            "end_char": None,
        }
    ]
    assert payload["human_review"] == {
        "required": True,
        "reason": "Need a human confirmation before continuing.",
        "repair_mode": "needs_human_review",
        "repair_target": "runtime/tools",
        "repair_steps": ["Confirm the file selection."],
    }
    assert payload["effective_max_iters"] == 8
