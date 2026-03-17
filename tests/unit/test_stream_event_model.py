"""Unit tests for StreamEvent and TurnState models.

Covers fleet_rlm.core.models.streaming — all event kinds and
TurnState.apply state transitions introduced in the new implementation.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from fleet_rlm.core.models.streaming import (
    StreamEvent,
    TurnState,
)


# ---------------------------------------------------------------------------
# StreamEvent dataclass
# ---------------------------------------------------------------------------


def test_stream_event_default_construction():
    event = StreamEvent(kind="status")
    assert event.kind == "status"
    assert event.text == ""
    assert event.payload == {}
    assert event.flush_tokens is False
    assert isinstance(event.timestamp, datetime)
    assert event.timestamp.tzinfo is not None


def test_stream_event_custom_fields():
    ts = datetime(2025, 1, 1, tzinfo=timezone.utc)
    event = StreamEvent(
        kind="tool_call",
        text="load_document",
        payload={"tool_name": "load_document"},
        timestamp=ts,
        flush_tokens=True,
    )
    assert event.kind == "tool_call"
    assert event.text == "load_document"
    assert event.payload["tool_name"] == "load_document"
    assert event.timestamp == ts
    assert event.flush_tokens is True


# ---------------------------------------------------------------------------
# StreamEventKind coverage — new HITL + command events
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kind",
    [
        "assistant_token",
        "status",
        "reasoning_step",
        "tool_call",
        "tool_result",
        "trajectory_step",
        "plan_update",
        "rlm_executing",
        "memory_update",
        "hitl_request",
        "hitl_resolved",
        "command_ack",
        "command_reject",
        "final",
        "error",
        "cancelled",
    ],
)
def test_stream_event_kind_is_valid(kind: str):
    """All expected event kinds should be constructable without errors."""
    event = StreamEvent(kind=kind)  # type: ignore[arg-type]
    assert event.kind == kind


# ---------------------------------------------------------------------------
# TurnState.apply — state transitions
# ---------------------------------------------------------------------------


def test_turn_state_apply_assistant_token():
    state = TurnState()
    state.apply(StreamEvent(kind="assistant_token", text="Hello"))
    state.apply(StreamEvent(kind="assistant_token", text=" world"))

    assert state.assistant_tokens == ["Hello", " world"]
    assert state.transcript_text == "Hello world"
    assert state.token_count == 2
    assert state.stream_chunks == ["Hello", " world"]
    assert state.done is False


def test_turn_state_apply_status():
    state = TurnState()
    state.apply(StreamEvent(kind="status", text="Calling tool: load_document"))

    assert "Calling tool: load_document" in state.status_lines
    assert "Calling tool: load_document" in state.status_messages
    assert "Calling tool: load_document" in state.reasoning_lines


def test_turn_state_apply_reasoning_step():
    state = TurnState()
    state.apply(StreamEvent(kind="reasoning_step", text="I need to analyze this file."))

    assert "I need to analyze this file." in state.reasoning_lines
    assert "I need to analyze this file." in state.thought_chunks


def test_turn_state_apply_tool_call():
    state = TurnState()
    state.apply(StreamEvent(kind="tool_call", text="load_document(path='x.txt')"))

    assert "load_document(path='x.txt')" in state.tool_timeline


def test_turn_state_apply_tool_result():
    state = TurnState()
    state.apply(StreamEvent(kind="tool_result", text="file content loaded"))

    assert "file content loaded" in state.tool_timeline


def test_turn_state_apply_trajectory_step():
    state = TurnState()
    step_data = {"tool": "load_document", "result": "ok"}
    state.apply(StreamEvent(kind="trajectory_step", payload={"step_data": step_data}))

    assert state.trajectory == {"steps": [step_data]}

    # Second step appends correctly
    step2 = {"tool": "finish", "result": "done"}
    state.apply(StreamEvent(kind="trajectory_step", payload={"step_data": step2}))
    assert len(state.trajectory["steps"]) == 2


def test_turn_state_apply_final():
    state = TurnState()
    state.apply(StreamEvent(kind="assistant_token", text="partial"))
    state.apply(
        StreamEvent(
            kind="final",
            text="The complete answer.",
            payload={
                "trajectory": {"steps": [{"t": "finish"}]},
                "final_reasoning": "Some reasoning.",
                "history_turns": 3,
            },
        )
    )

    assert state.done is True
    assert state.final_text == "The complete answer."
    assert state.transcript_text == "The complete answer."
    assert state.history_turns == 3
    assert state.final_reasoning == "Some reasoning."
    assert state.trajectory["steps"][0]["t"] == "finish"


def test_turn_state_apply_final_uses_tokens_when_no_text():
    """final event with empty text should fall back to accumulated tokens."""
    state = TurnState()
    state.apply(StreamEvent(kind="assistant_token", text="fallback answer"))
    state.apply(StreamEvent(kind="final", text="", payload={}))

    assert state.final_text == "fallback answer"
    assert state.done is True


def test_turn_state_apply_cancelled():
    state = TurnState()
    state.apply(StreamEvent(kind="assistant_token", text="partial response"))
    state.apply(StreamEvent(kind="cancelled", text="", payload={"history_turns": 1}))

    assert state.cancelled is True
    assert state.done is True
    # TurnState.apply for cancelled falls back to transcript_text when event.text is empty;
    # the [cancelled] annotation is added by the streaming layer, not the model.
    assert state.final_text == "partial response"
    assert state.history_turns == 1


def test_turn_state_apply_error():
    state = TurnState()
    state.apply(
        StreamEvent(kind="error", text="LLM timeout", payload={"history_turns": 2})
    )

    assert state.errored is True
    assert state.done is True
    assert state.error_message == "LLM timeout"
    assert state.history_turns == 2


def test_turn_state_plan_update_rlm_executing_memory_update_add_to_timelines():
    """plan_update, rlm_executing, memory_update should append to both timelines."""
    state = TurnState()
    for kind in ("plan_update", "rlm_executing", "memory_update"):
        state.apply(StreamEvent(kind=kind, text=f"{kind} msg"))  # type: ignore[arg-type]

    assert len(state.tool_timeline) == 3
    assert len(state.status_lines) == 3


def test_turn_state_empty_text_events_dont_append():
    """Events with empty text should not pollute the lists."""
    state = TurnState()
    state.apply(StreamEvent(kind="status", text=""))
    state.apply(StreamEvent(kind="reasoning_step", text=""))
    state.apply(StreamEvent(kind="tool_call", text=""))

    assert state.status_lines == []
    assert state.reasoning_lines == []
    assert state.tool_timeline == []
