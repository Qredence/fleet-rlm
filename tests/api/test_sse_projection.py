"""SSE projection contracts for RuntimeEvent v1 and live streaming."""

from __future__ import annotations

import ast
from pathlib import Path
from uuid import uuid4

from fleet_rlm.api.sse import AISDKUIProjector
from fleet_rlm.rlm.events import (
    PROVIDER_ENDPOINT_NOT_FOUND_MESSAGE,
    TERMINAL_DETAIL_TYPES,
    AttachmentRead,
    ChildProgress,
    EventRecorder,
    RLMCode,
    RLMOutput,
    RLMReasoning,
    RunCancelled,
    RunCompleted,
    RunFailed,
    RunStarted,
    RuntimeEvent,
    SkillActivated,
    SkillLoaded,
    Status,
    StepFinished,
    StepStarted,
    StructuredResult,
    TextCompleted,
    TextDelta,
    ToolCompleted,
    ToolStarted,
    Usage,
)


def _projected_types(events: list[RuntimeEvent]) -> list[str]:
    projector = AISDKUIProjector()
    chunks = [chunk for event in events for chunk in projector.project(event)]
    return [chunk["type"] for chunk in chunks]


# --- Architectural Boundary Checks ---


def test_rlm_events_module_does_not_import_fastapi() -> None:
    events_path = Path(__file__).resolve().parents[2] / "src" / "fleet_rlm" / "rlm" / "events.py"
    tree = ast.parse(events_path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".", maxsplit=1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module.split(".", maxsplit=1)[0])
    assert "fastapi" not in imported
    assert "starlette" not in imported


# --- Event Projection Contracts ---


def test_ai_sdk_projector_emits_typed_ui_message_chunks() -> None:
    recorder = EventRecorder(run_id=uuid4(), session_id=uuid4())
    events = [
        recorder.record(RunStarted("live")),
        recorder.record(TextDelta("hello")),
        recorder.record(RunCompleted(0, "live", 1)),
    ]
    projector = AISDKUIProjector()
    payloads = [chunk for event in events for chunk in projector.project(event)]
    assert [item["type"] for item in payloads] == [
        "turn_start",
        "text",
        "turn_finish",
    ]
    assert payloads[-1]["finishReason"] == "stop"


def test_projector_maps_detailed_runtime_events_to_ui_message_chunks() -> None:
    recorder = EventRecorder(run_id=uuid4(), session_id=uuid4())
    projector = AISDKUIProjector()
    events = [
        recorder.record(RunStarted("live")),
        recorder.record(SkillActivated("s1", "long-context", "1", "system")),
        recorder.record(SkillLoaded("s1", "long-context", "1")),
        recorder.record(StepStarted(1)),
        recorder.record(RLMReasoning("Inspect the corpus", 1)),
        recorder.record(RLMCode("print(len(context))", 1)),
        recorder.record(ToolStarted("call-1", "lookup", {"key": "x"})),
        recorder.record(ToolCompleted("call-1", "lookup", {"value": 1})),
        recorder.record(RLMOutput("42", 1)),
        recorder.record(StepFinished(1)),
        recorder.record(
            Usage(
                {
                    "iterations": 1,
                    "observed_lm_usage": {"root": {"total_tokens": 12}},
                    "duration_ms": 3,
                }
            )
        ),
        recorder.record(StructuredResult("report", "1", {"score": 1})),
        recorder.record(TextDelta("answer")),
        recorder.record(TextCompleted("answer")),
        recorder.record(RunCompleted(1, "live")),
    ]

    chunks = [chunk for event in events for chunk in projector.project(event)]
    types = [chunk["type"] for chunk in chunks]

    assert types[0] == "turn_start"
    assert chunks[0]["runId"] == str(recorder.run_id)
    assert "skill" in types
    skill_chunks = [chunk for chunk in chunks if chunk["type"] == "skill"]
    assert [chunk["phase"] for chunk in skill_chunks] == ["activated", "loaded"]
    assert types[types.index("step_start") + 1 : types.index("code")] == ["reasoning"]
    assert "tool_call" in types
    assert "tool_result" in types
    assert types.index("usage") < types.index("structured_result") < types.index("text")
    assert types[-1] == "turn_finish"
    assert chunks[-1]["finishReason"] == "stop"


def test_child_progress_projects_structurally_and_preserves_legacy_status() -> None:
    run_id = uuid4()
    parent_run_id = str(run_id)
    recorder = EventRecorder(run_id=run_id, session_id=uuid4())
    before = recorder.record(RunStarted("live"))
    child = recorder.record(
        ChildProgress(
            "child-1",
            "Review the API contract",
            "completed",
            2,
            "Found a schema mismatch",
            "complete",
            parent_run_id,
            evidence=("src/api.py:42",),
            gaps=("Caller not checked",),
            result_file_count=2,
        )
    )
    legacy_status = recorder.record(
        Status(
            "recursive",
            "child_completed",
            "call_index=1 recursive_depth=1 duration_ms=2 cleanup_status=completed",
        )
    )

    assert before.sequence == 1
    assert child.sequence == 2
    assert legacy_status.sequence == 3
    assert AISDKUIProjector().project(child) == [
        {
            "type": "child_progress",
            "childId": "child-1",
            "taskLabel": "Review the API contract",
            "state": "completed",
            "elapsedMs": 2,
            "outcome": "Found a schema mismatch",
            "evidence": ["src/api.py:42"],
            "gaps": ["Caller not checked"],
            "resultFileCount": 2,
            "cleanupState": "complete",
            "parentRunId": parent_run_id,
        }
    ]
    assert AISDKUIProjector().project(legacy_status) == [
        {
            "type": "turn_status",
            "phase": "recursive",
            "status": "child_completed",
            "message": "call_index=1 recursive_depth=1 duration_ms=2 cleanup_status=completed",
        }
    ]


def test_projector_maps_failure_and_cancel_to_ai_sdk_terminal_parts() -> None:
    failed = EventRecorder(run_id=uuid4(), session_id=uuid4()).record(RunFailed("execution_failed", "Turn failed"))
    cancelled = EventRecorder(run_id=uuid4(), session_id=uuid4()).record(RunCancelled())

    assert AISDKUIProjector().project(failed) == [
        {"type": "turn_error", "message": "Turn failed", "code": "execution_failed"},
        {"type": "turn_finish", "finishReason": "error", "status": "error"},
    ]
    provider_failed = EventRecorder(run_id=uuid4(), session_id=uuid4()).record(
        RunFailed("execution_failed", PROVIDER_ENDPOINT_NOT_FOUND_MESSAGE)
    )
    assert AISDKUIProjector().project(provider_failed) == [
        {"type": "turn_error", "message": PROVIDER_ENDPOINT_NOT_FOUND_MESSAGE, "code": "execution_failed"},
        {"type": "turn_finish", "finishReason": "error", "status": "error"},
    ]
    assert AISDKUIProjector().project(cancelled) == [
        {"type": "turn_cancelled", "reason": "Turn cancelled"},
    ]


def test_projector_does_not_create_an_empty_reasoning_panel() -> None:
    event = EventRecorder(run_id=uuid4(), session_id=uuid4()).record(RLMReasoning("   ", 1))
    assert AISDKUIProjector().project(event) == []


def test_projector_projects_incremental_output_with_stable_stream_metadata() -> None:
    recorder = EventRecorder(run_id=uuid4(), session_id=uuid4())
    projector = AISDKUIProjector()
    chunks = [
        projector.project(recorder.record(RLMOutput("first", 1, "output-1", True, False)))[0],
        projector.project(recorder.record(RLMOutput("first second", 1, "output-1", False, True)))[0],
    ]

    assert [chunk["output"] for chunk in chunks] == ["first", "first second"]
    assert [chunk["isDelta"] for chunk in chunks] == [True, False]
    assert [chunk["final"] for chunk in chunks] == [False, True]
    assert [chunk["streamId"] for chunk in chunks] == ["output-1", "output-1"]


def test_projector_replaces_completed_live_reasoning_with_canonical_stream() -> None:
    recorder = EventRecorder(run_id=uuid4(), session_id=uuid4())
    projector = AISDKUIProjector()

    live = projector.project(recorder.record(RLMReasoning("Inspect", 1, "reasoning-1", True, True)))
    canonical = projector.project(recorder.record(RLMReasoning("Canonical inspect", 1)))

    assert live == [
        {
            "type": "reasoning",
            "streamId": "reasoning-1",
            "step": 1,
            "text": "Inspect",
            "delta": "Inspect",
            "final": True,
        },
    ]
    assert canonical == [
        {"type": "reasoning", "streamId": "1", "step": 1, "text": "Canonical inspect", "delta": "", "final": True},
    ]


def test_projector_closes_partial_live_reasoning_before_canonical_correction() -> None:
    recorder = EventRecorder(run_id=uuid4(), session_id=uuid4())
    projector = AISDKUIProjector()

    partial = projector.project(recorder.record(RLMReasoning("partial", 1, "reasoning-1", True, False)))
    canonical = projector.project(recorder.record(RLMReasoning("canonical", 1)))

    assert partial == [
        {
            "type": "reasoning",
            "streamId": "reasoning-1",
            "step": 1,
            "text": "partial",
            "delta": "partial",
            "final": False,
        },
    ]
    assert canonical == [
        {"type": "reasoning", "streamId": "1", "step": 1, "text": "canonical", "delta": "", "final": True},
    ]


def test_projector_reuses_same_step_ids_for_trajectory_corrections() -> None:
    recorder = EventRecorder(run_id=uuid4(), session_id=uuid4())
    projector = AISDKUIProjector()
    first_code = projector.project(recorder.record(RLMCode("stale", 1)))[0]
    corrected_code = projector.project(recorder.record(RLMCode("canonical", 1)))[0]
    first_output = projector.project(recorder.record(RLMOutput("stale", 1)))[0]
    corrected_output = projector.project(recorder.record(RLMOutput("canonical", 1)))[0]

    assert first_code["step"] == corrected_code["step"] == 1
    assert first_output["step"] == corrected_output["step"] == 1
    assert first_code["streamId"] == corrected_code["streamId"] == "1"


def test_projector_maps_attachment_reads_to_reload_compatible_ui_data() -> None:
    attachment_id = uuid4()
    recorder = EventRecorder(run_id=uuid4(), session_id=uuid4())
    chunks = AISDKUIProjector().project(recorder.record(AttachmentRead(attachment_id, "phase1.txt", 42)))

    assert chunks == [
        {
            "type": "attachment",
            "attachmentId": str(attachment_id),
            "phase": "read",
            "filename": "phase1.txt",
            "byteSize": 42,
        }
    ]


# --- Turn Lifecycle Projection Contracts ---


def test_projected_turn_lifecycle_is_start_chunks_one_terminal_done() -> None:
    recorder = EventRecorder(run_id=uuid4(), session_id=uuid4())
    events = [
        recorder.record(RunStarted("live")),
        recorder.record(Status("execution", "running")),
        recorder.record(Usage({"iterations": 1, "observed_lm_usage": {}, "duration_ms": 2})),
        recorder.record(TextDelta("ok")),
        recorder.record(TextCompleted("ok")),
        recorder.record(RunCompleted(1, "live")),
    ]

    terminal_events = [event for event in events if isinstance(event.detail, TERMINAL_DETAIL_TYPES)]
    assert len(terminal_events) == 1
    assert terminal_events[0] is events[-1]

    types = _projected_types(events)
    assert types[0] == "turn_start"
    assert types[-1] == "turn_finish"
    assert types.count("turn_finish") == 1
    assert not any(chunk_type in {"turn_finish", "turn_cancelled", "turn_error"} for chunk_type in types[1:-1])


def test_error_terminal_projects_error_then_finish_as_single_runtime_terminal() -> None:
    recorder = EventRecorder(run_id=uuid4(), session_id=uuid4())
    events = [recorder.record(RunStarted("live")), recorder.record(RunFailed("execution_failed", "Turn failed"))]

    assert len([event for event in events if isinstance(event.detail, TERMINAL_DETAIL_TYPES)]) == 1
    assert isinstance(events[-1].detail, TERMINAL_DETAIL_TYPES)
    types = _projected_types(events)
    assert types == ["turn_start", "turn_error", "turn_finish"]


def test_error_terminal_after_text_delta_does_not_invent_extra_terminal_chunks() -> None:
    recorder = EventRecorder(run_id=uuid4(), session_id=uuid4())
    events = [
        recorder.record(RunStarted("live")),
        recorder.record(TextDelta("partial")),
        recorder.record(RunFailed("execution_failed", "Turn failed")),
    ]

    assert len([event for event in events if isinstance(event.detail, TERMINAL_DETAIL_TYPES)]) == 1
    assert isinstance(events[-1].detail, TERMINAL_DETAIL_TYPES)
    types = _projected_types(events)
    assert types == ["turn_start", "text", "turn_error", "turn_finish"]


def test_abort_terminal_projects_abort_as_single_runtime_terminal() -> None:
    recorder = EventRecorder(run_id=uuid4(), session_id=uuid4())
    events = [recorder.record(RunStarted("live")), recorder.record(RunCancelled())]

    assert len([event for event in events if isinstance(event.detail, TERMINAL_DETAIL_TYPES)]) == 1
    assert isinstance(events[-1].detail, TERMINAL_DETAIL_TYPES)
    types = _projected_types(events)
    assert types == ["turn_start", "turn_cancelled"]


def test_openapi_declares_typed_render_data_payloads() -> None:
    from tests.support.testing_app import create_testing_app

    schema = create_testing_app().openapi()
    variants = schema["components"]["schemas"]["FleetUIMessageChunk"]["oneOf"]
    by_type = {variant["properties"]["type"]["const"]: variant for variant in variants}

    code_data = by_type["code"]["properties"]
    output_data = by_type["output"]["properties"]
    structured_data = by_type["structured_result"]["properties"]

    assert code_data["code"]["type"] == "string"
    assert output_data["output"]["type"] == "string"
    assert "type" not in structured_data["value"]
