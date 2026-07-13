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
        recorder.record(StepStarted(1)),
        recorder.record(RLMReasoning("Inspect the corpus", 1)),
        recorder.record(RLMCode("print(len(context))", 1)),
        recorder.record(ToolStarted("call-1", "lookup", {"key": "x"})),
        recorder.record(ToolCompleted("call-1", "lookup", {"value": 1})),
        recorder.record(RLMOutput("42", 1)),
        recorder.record(StepFinished(1)),
        recorder.record(Usage({"totalTokens": 12})),
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
    from fleet_rlm.rlm.events import EventRecorder, RunCancelled, RunFailed

    failed = EventRecorder(run_id=uuid4(), session_id=uuid4()).record(RunFailed("execution_failed", "Turn failed"))
    cancelled = EventRecorder(run_id=uuid4(), session_id=uuid4()).record(RunCancelled())

    assert AISDKUIProjector().project(failed) == [
        {"type": "error", "errorText": "Turn failed"},
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
