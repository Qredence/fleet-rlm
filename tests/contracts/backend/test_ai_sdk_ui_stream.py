"""AI SDK UI 7 v1 projection contract for the FastAPI chat stream."""

from __future__ import annotations

from uuid import uuid4


def test_projector_maps_detailed_runtime_events_to_ui_message_chunks() -> None:
    from fleet_rlm.api.sse import AISDKUIProjector
    from fleet_rlm.rlm.events import EventRecorder, RuntimeEventKind

    recorder = EventRecorder(run_id=uuid4(), session_id=uuid4())
    projector = AISDKUIProjector()
    events = [
        recorder.emit(RuntimeEventKind.RUN_STARTED, {}),
        recorder.emit(RuntimeEventKind.SKILL_ACTIVATED, {"skill_id": "s1", "name": "long-context"}),
        recorder.emit(RuntimeEventKind.STEP_STARTED, {"step": 1}),
        recorder.emit(RuntimeEventKind.RLM_REASONING, {"step": 1, "text": "Inspect the corpus"}),
        recorder.emit(RuntimeEventKind.RLM_CODE, {"step": 1, "code": "print(len(context))"}),
        recorder.emit(
            RuntimeEventKind.TOOL_STARTED,
            {"tool_call_id": "call-1", "tool_name": "lookup", "input": {"key": "x"}},
        ),
        recorder.emit(
            RuntimeEventKind.TOOL_COMPLETED,
            {"tool_call_id": "call-1", "tool_name": "lookup", "output": {"value": 1}},
        ),
        recorder.emit(RuntimeEventKind.RLM_OUTPUT, {"step": 1, "output": "42"}),
        recorder.emit(RuntimeEventKind.STEP_FINISHED, {"step": 1}),
        recorder.emit(RuntimeEventKind.USAGE, {"usage": {"totalTokens": 12}}),
        recorder.emit(
            RuntimeEventKind.STRUCTURED_RESULT,
            {"schemaId": "report", "schemaVersion": "1", "value": {"score": 1}},
        ),
        recorder.emit(RuntimeEventKind.TEXT_DELTA, {"text": "answer"}),
        recorder.emit(RuntimeEventKind.TEXT_COMPLETED, {"text": "answer"}),
        recorder.emit(
            RuntimeEventKind.RUN_COMPLETED,
            {"status": "completed", "checkpoint_version": 1},
        ),
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
    from fleet_rlm.rlm.events import EventRecorder, RuntimeEventKind

    failed = EventRecorder(run_id=uuid4(), session_id=uuid4()).emit(
        RuntimeEventKind.ERROR,
        {"status": "failed", "message": "Turn failed"},
    )
    cancelled = EventRecorder(run_id=uuid4(), session_id=uuid4()).emit(
        RuntimeEventKind.ERROR,
        {"status": "cancelled", "message": "Turn cancelled"},
    )

    assert AISDKUIProjector().project(failed) == [
        {"type": "error", "errorText": "Turn failed"},
        {"type": "finish", "finishReason": "error"},
    ]
    assert AISDKUIProjector().project(cancelled) == [
        {"type": "abort", "reason": "Turn cancelled"},
    ]
