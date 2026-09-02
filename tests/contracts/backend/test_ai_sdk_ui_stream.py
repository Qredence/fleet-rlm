"""AI SDK UI 7 v1 projection contract for the FastAPI chat stream."""

from __future__ import annotations

from uuid import uuid4


def test_projector_maps_detailed_runtime_events_to_ui_message_chunks() -> None:
    from fleet_rlm.api.sse import AISDKUIProjector
    from fleet_rlm.rlm.events import (
        EventRecorder,
        RLMCode,
        RLMOutput,
        RLMReasoning,
        RunCompleted,
        RunStarted,
        SkillActivated,
        SkillLoaded,
        StepFinished,
        StepStarted,
        StructuredResult,
        TextCompleted,
        TextDelta,
        ToolCompleted,
        ToolStarted,
        Usage,
    )

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

    assert types[0] == "start"
    assert chunks[0]["messageId"] == str(recorder.run_id)
    assert "data-skill" in types
    skill_chunks = [chunk for chunk in chunks if chunk["type"] == "data-skill"]
    assert [chunk["data"]["phase"] for chunk in skill_chunks] == ["activated", "loaded"]
    assert types[types.index("start-step") + 1 : types.index("data-rlm-code")] == [
        "reasoning-start",
        "reasoning-delta",
        "reasoning-end",
    ]
    assert "tool-input-available" in types
    assert "tool-output-available" in types
    assert types.index("data-usage") < types.index("data-structured-result") < types.index("text-start")
    assert types[-4:] == ["text-start", "text-delta", "text-end", "finish"]
    assert chunks[-1]["finishReason"] == "stop"


def test_projector_maps_failure_and_cancel_to_ai_sdk_terminal_parts() -> None:
    from fleet_rlm.api.sse import AISDKUIProjector
    from fleet_rlm.rlm.events import (
        PROVIDER_ENDPOINT_NOT_FOUND_MESSAGE,
        EventRecorder,
        RunCancelled,
        RunFailed,
    )

    failed = EventRecorder(run_id=uuid4(), session_id=uuid4()).record(RunFailed("execution_failed", "Turn failed"))
    cancelled = EventRecorder(run_id=uuid4(), session_id=uuid4()).record(RunCancelled())

    assert AISDKUIProjector().project(failed) == [
        {"type": "error", "errorText": "Turn failed"},
        {"type": "finish", "finishReason": "error"},
    ]
    provider_failed = EventRecorder(run_id=uuid4(), session_id=uuid4()).record(
        RunFailed("execution_failed", PROVIDER_ENDPOINT_NOT_FOUND_MESSAGE)
    )
    assert AISDKUIProjector().project(provider_failed) == [
        {"type": "error", "errorText": PROVIDER_ENDPOINT_NOT_FOUND_MESSAGE},
        {"type": "finish", "finishReason": "error"},
    ]
    assert AISDKUIProjector().project(cancelled) == [
        {"type": "abort", "reason": "Turn cancelled"},
    ]


def test_projector_does_not_create_an_empty_reasoning_panel() -> None:
    from fleet_rlm.api.sse import AISDKUIProjector
    from fleet_rlm.rlm.events import EventRecorder, RLMReasoning

    event = EventRecorder(run_id=uuid4(), session_id=uuid4()).record(RLMReasoning("   ", 1))

    assert AISDKUIProjector().project(event) == []


def test_projector_projects_incremental_output_with_stable_stream_metadata() -> None:
    from fleet_rlm.api.sse import AISDKUIProjector
    from fleet_rlm.rlm.events import EventRecorder, RLMOutput

    recorder = EventRecorder(run_id=uuid4(), session_id=uuid4())
    projector = AISDKUIProjector()
    chunks = [
        projector.project(recorder.record(RLMOutput("first", 1, "output-1", True, False)))[0],
        projector.project(recorder.record(RLMOutput("first second", 1, "output-1", False, True)))[0],
    ]

    assert [chunk["data"] for chunk in chunks] == [
        {"output": "first", "step": 1, "stream_id": "output-1", "is_delta": True, "is_final": False},
        {
            "output": "first second",
            "step": 1,
            "stream_id": "output-1",
            "is_delta": False,
            "is_final": True,
        },
    ]
    assert [chunk["id"] for chunk in chunks] == ["output-1", "output-1"]


def test_projector_replaces_completed_live_reasoning_with_canonical_stream() -> None:
    from fleet_rlm.api.sse import AISDKUIProjector
    from fleet_rlm.rlm.events import EventRecorder, RLMReasoning

    recorder = EventRecorder(run_id=uuid4(), session_id=uuid4())
    projector = AISDKUIProjector()

    live = projector.project(recorder.record(RLMReasoning("Inspect", 1, "reasoning-1", True, True)))
    canonical = projector.project(recorder.record(RLMReasoning("Canonical inspect", 1)))

    assert live == [
        {"type": "reasoning-start", "id": "reasoning-1"},
        {"type": "reasoning-delta", "id": "reasoning-1", "delta": "Inspect"},
        {"type": "reasoning-end", "id": "reasoning-1"},
    ]
    assert canonical == [
        {"type": "reasoning-start", "id": "reasoning-1:canonical"},
        {"type": "reasoning-delta", "id": "reasoning-1:canonical", "delta": "Canonical inspect"},
        {"type": "reasoning-end", "id": "reasoning-1:canonical"},
    ]


def test_projector_closes_partial_live_reasoning_before_canonical_correction() -> None:
    from fleet_rlm.api.sse import AISDKUIProjector
    from fleet_rlm.rlm.events import EventRecorder, RLMReasoning

    recorder = EventRecorder(run_id=uuid4(), session_id=uuid4())
    projector = AISDKUIProjector()

    projector.project(recorder.record(RLMReasoning("partial", 1, "reasoning-1", True, False)))
    canonical = projector.project(recorder.record(RLMReasoning("canonical", 1)))

    assert canonical == [
        {"type": "reasoning-end", "id": "reasoning-1"},
        {"type": "reasoning-start", "id": "reasoning-1:canonical"},
        {"type": "reasoning-delta", "id": "reasoning-1:canonical", "delta": "canonical"},
        {"type": "reasoning-end", "id": "reasoning-1:canonical"},
    ]


def test_projector_reuses_same_step_ids_for_trajectory_corrections() -> None:
    from fleet_rlm.api.sse import AISDKUIProjector
    from fleet_rlm.rlm.events import EventRecorder, RLMCode, RLMOutput

    recorder = EventRecorder(run_id=uuid4(), session_id=uuid4())
    projector = AISDKUIProjector()
    first_code = projector.project(recorder.record(RLMCode("stale", 1)))[0]
    corrected_code = projector.project(recorder.record(RLMCode("canonical", 1)))[0]
    first_output = projector.project(recorder.record(RLMOutput("stale", 1)))[0]
    corrected_output = projector.project(recorder.record(RLMOutput("canonical", 1)))[0]

    assert first_code["id"] == corrected_code["id"]
    assert first_output["id"] == corrected_output["id"]


def test_projector_maps_attachment_reads_to_reload_compatible_ui_data() -> None:
    from fleet_rlm.api.sse import AISDKUIProjector
    from fleet_rlm.rlm.events import AttachmentRead, EventRecorder

    attachment_id = uuid4()
    recorder = EventRecorder(run_id=uuid4(), session_id=uuid4())

    chunks = AISDKUIProjector().project(recorder.record(AttachmentRead(attachment_id, "phase1.txt", 42)))

    assert chunks == [
        {
            "type": "data-attachment",
            "id": str(attachment_id),
            "data": {
                "attachment_id": str(attachment_id),
                "attachmentId": str(attachment_id),
                "phase": "read",
                "filename": "phase1.txt",
                "byte_size": 42,
                "byteSize": 42,
            },
        }
    ]
