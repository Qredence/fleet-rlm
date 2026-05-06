"""Unit tests for StreamEvent and TurnState models.

Covers fleet_rlm.runtime.schemas — StreamEvent and TurnState models,
all event kinds, and TurnState.apply state transitions.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from fleet_rlm.runtime.schemas import (
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
        "status",
        "text",
        "reasoning",
        "tool_call",
        "tool_result",
        "warning",
        "error",
        "done",
    ],
)
def test_stream_event_kind_is_valid(kind: str):
    """All expected event kinds should be constructable without errors."""
    event = StreamEvent(kind=kind)  # type: ignore[arg-type]
    assert event.kind == kind


# ---------------------------------------------------------------------------
# TurnState.apply — state transitions
# ---------------------------------------------------------------------------


def test_turn_state_apply_text_kind():
    state = TurnState()
    state.apply(StreamEvent(kind="text", text="Hello"))
    state.apply(StreamEvent(kind="text", text=" world"))

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


def test_turn_state_apply_reasoning_kind():
    state = TurnState()
    state.apply(StreamEvent(kind="reasoning", text="I need to analyze this file."))

    assert "I need to analyze this file." in state.reasoning_lines
    assert "I need to analyze this file." in state.thought_chunks


def test_turn_state_apply_reasoning_kind_alias():
    """'reasoning' kind should accumulate reasoning lines and thought chunks."""
    state = TurnState()
    state.apply(StreamEvent(kind="reasoning", text="Thinking step."))

    assert "Thinking step." in state.reasoning_lines
    assert "Thinking step." in state.thought_chunks


def test_turn_state_apply_tool_call():
    state = TurnState()
    state.apply(StreamEvent(kind="tool_call", text="load_document(path='x.txt')"))

    assert "load_document(path='x.txt')" in state.tool_timeline


def test_turn_state_apply_tool_result():
    state = TurnState()
    state.apply(StreamEvent(kind="tool_result", text="file content loaded"))

    assert "file content loaded" in state.tool_timeline


def test_turn_state_apply_done():
    state = TurnState()
    state.apply(StreamEvent(kind="text", text="partial"))
    state.apply(
        StreamEvent(
            kind="done",
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


def test_turn_state_apply_done_kind_cancelled():
    """'done' with cancelled=True in payload marks cancelled turn."""
    state = TurnState()
    state.apply(StreamEvent(kind="text", text="partial response"))
    state.apply(
        StreamEvent(
            kind="done",
            text="partial response\n\n[cancelled]",
            payload={"cancelled": True, "history_turns": 1},
        )
    )

    assert state.cancelled is True
    assert state.done is True
    assert state.final_text == "partial response\n\n[cancelled]"
    assert state.history_turns == 1


def test_turn_state_apply_done_uses_tokens_when_no_text():
    """done event with empty text should fall back to accumulated tokens."""
    state = TurnState()
    state.apply(StreamEvent(kind="text", text="fallback answer"))
    state.apply(StreamEvent(kind="done", text="", payload={}))

    assert state.final_text == "fallback answer"
    assert state.done is True


def test_turn_state_apply_cancelled_via_done():
    state = TurnState()
    state.apply(StreamEvent(kind="text", text="partial response"))
    state.apply(StreamEvent(kind="done", text="", payload={"cancelled": True, "history_turns": 1}))

    assert state.cancelled is True
    assert state.done is True
    # done with cancelled=True falls back to transcript_text when event.text is empty
    assert state.final_text == "partial response"
    assert state.history_turns == 1


def test_turn_state_apply_error():
    state = TurnState()
    state.apply(StreamEvent(kind="error", text="LLM timeout", payload={"history_turns": 2}))

    assert state.errored is True
    assert state.done is True
    assert state.error_message == "LLM timeout"
    assert state.history_turns == 2


def test_turn_state_empty_text_events_dont_append():
    """Events with empty text should not pollute the lists."""
    state = TurnState()
    state.apply(StreamEvent(kind="status", text=""))
    state.apply(StreamEvent(kind="reasoning", text=""))
    state.apply(StreamEvent(kind="tool_call", text=""))

    assert state.status_lines == []
    assert state.reasoning_lines == []
    assert state.tool_timeline == []
