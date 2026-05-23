from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from typing import get_args

import pytest
from pydantic import ValidationError


def test_stream_event_construction_and_serialization() -> None:
    from fleet_rlm.runtime.schemas import StreamEvent

    ts = datetime(2025, 1, 1, tzinfo=timezone.utc)
    event = StreamEvent(
        kind="tool_call",
        text="load_document(path='README.md')",
        payload={"tool_name": "load_document"},
        timestamp=ts,
        flush_tokens=True,
    )

    assert asdict(event) == {
        "kind": "tool_call",
        "text": "load_document(path='README.md')",
        "payload": {"tool_name": "load_document"},
        "timestamp": ts,
        "flush_tokens": True,
    }


def test_stream_event_defaults_are_runtime_friendly() -> None:
    from fleet_rlm.runtime.schemas import StreamEvent

    event = StreamEvent(kind="status")

    assert event.text == ""
    assert event.payload == {}
    assert event.flush_tokens is False
    assert event.timestamp.tzinfo is not None


def test_turn_state_apply_tracks_stream_lifecycle() -> None:
    from fleet_rlm.runtime.schemas import StreamEvent, TurnState

    state = TurnState()
    state.apply(StreamEvent(kind="text", text="Hello"))
    state.apply(StreamEvent(kind="text", text=" world"))
    state.apply(StreamEvent(kind="status", text="Calling tool: list_files"))
    state.apply(StreamEvent(kind="reasoning", text="Need to inspect the repo."))
    state.apply(StreamEvent(kind="warning", text="Using fallback search path"))
    state.apply(StreamEvent(kind="tool_call", text="list_files(path='src')"))
    state.apply(StreamEvent(kind="tool_result", text="2 files found"))
    state.apply(
        StreamEvent(
            kind="done",
            text="Final answer",
            payload={
                "trajectory": {"steps": [{"tool_name": "list_files"}]},
                "final_reasoning": "Inspected repo first.",
                "history_turns": 3,
            },
        )
    )

    assert state.transcript_text == "Final answer"
    assert state.assistant_tokens == ["Hello", " world"]
    assert state.token_count == 2
    assert state.reasoning_lines == [
        "Calling tool: list_files",
        "Need to inspect the repo.",
        "Using fallback search path",
    ]
    assert state.tool_timeline == ["Using fallback search path", "list_files(path='src')", "2 files found"]
    assert state.final_reasoning == "Inspected repo first."
    assert state.trajectory == {"steps": [{"tool_name": "list_files"}]}
    assert state.history_turns == 3
    assert state.done is True


def test_turn_state_apply_handles_cancelled_and_error_turns() -> None:
    from fleet_rlm.runtime.schemas import StreamEvent, TurnState

    cancelled = TurnState()
    cancelled.apply(StreamEvent(kind="text", text="partial"))
    cancelled.apply(StreamEvent(kind="done", text="", payload={"cancelled": True, "history_turns": 1}))

    assert cancelled.cancelled is True
    assert cancelled.final_text == "partial"
    assert cancelled.done is True
    assert cancelled.history_turns == 1

    errored = TurnState()
    errored.apply(StreamEvent(kind="error", text="LM timeout", payload={"history_turns": 2}))

    assert errored.errored is True
    assert errored.error_message == "LM timeout"
    assert errored.done is True
    assert errored.history_turns == 2


def test_profile_and_session_config_validation() -> None:
    from fleet_rlm.runtime.schemas import ProfileConfig, SessionConfig

    profile = ProfileConfig(name="workbench", timeout="120", react_max_iters="5")
    session = SessionConfig(profile_name="dev", trace_mode="verbose", stream_refresh_ms="25")

    assert profile.timeout == 120
    assert profile.react_max_iters == 5
    assert session.trace_mode == "verbose"
    assert session.stream_refresh_ms == 25

    with pytest.raises(ValidationError):
        SessionConfig(trace_mode="chatty")


def test_stream_event_kind_values_are_stable() -> None:
    from fleet_rlm.runtime.schemas import StreamEventKind

    assert get_args(StreamEventKind) == (
        "status",
        "text",
        "reasoning",
        "tool_call",
        "tool_result",
        "warning",
        "error",
        "done",
        "clarification",
    )
