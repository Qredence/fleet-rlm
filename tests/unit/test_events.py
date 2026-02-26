"""Tests for discriminated-union serialization of stream events.

Validates that:
1. ``StreamEvent`` can be constructed with each ``StreamEventKind``.
2. ``TurnState.apply`` correctly routes every event kind.
3. The multiplexed kinds (``plan_update``, ``rlm_executing``, ``memory_update``)
   degrade cleanly for backward-compatible CLI rendering.
4. ``ExecutionStepBuilder.from_stream_event`` produces the expected
   ``ExecutionStepType`` for every kind.
"""

from __future__ import annotations

import time

import pytest

from fleet_rlm.models import StreamEvent, StreamEventKind, TurnState
from fleet_rlm.server.execution.events import ExecutionStepBuilder


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

ALL_KINDS: list[StreamEventKind] = [
    "assistant_token",
    "status",
    "reasoning_step",
    "tool_call",
    "tool_result",
    "trajectory_step",
    "plan_update",
    "rlm_executing",
    "memory_update",
    "final",
    "error",
    "cancelled",
]


@pytest.fixture
def builder() -> ExecutionStepBuilder:
    return ExecutionStepBuilder(run_id="test-run")


@pytest.fixture
def fresh_state() -> TurnState:
    return TurnState()


# ---------------------------------------------------------------------------
# StreamEvent construction
# ---------------------------------------------------------------------------


class TestStreamEventConstruction:
    """Every ``StreamEventKind`` literal must produce a valid ``StreamEvent``."""

    @pytest.mark.parametrize("kind", ALL_KINDS)
    def test_create_event(self, kind: StreamEventKind) -> None:
        event = StreamEvent(kind=kind, text="test")
        assert event.kind == kind
        assert event.text == "test"

    def test_default_payload(self) -> None:
        event = StreamEvent(kind="status", text="hello")
        assert event.payload == {}

    def test_custom_payload(self) -> None:
        event = StreamEvent(kind="tool_call", text="x", payload={"tool_name": "foo"})
        assert event.payload["tool_name"] == "foo"


# ---------------------------------------------------------------------------
# TurnState.apply routing
# ---------------------------------------------------------------------------


class TestTurnStateApply:
    """``TurnState.apply`` must route every kind without raising."""

    @pytest.mark.parametrize("kind", ALL_KINDS)
    def test_apply_does_not_raise(
        self, fresh_state: TurnState, kind: StreamEventKind
    ) -> None:
        event = StreamEvent(kind=kind, text="hi", payload={"history_turns": 1})
        fresh_state.apply(event)

    def test_assistant_token_accumulates(self, fresh_state: TurnState) -> None:
        fresh_state.apply(StreamEvent(kind="assistant_token", text="Hello"))
        fresh_state.apply(StreamEvent(kind="assistant_token", text=" world"))
        assert fresh_state.transcript_text == "Hello world"
        assert fresh_state.token_count == 2

    def test_final_sets_done(self, fresh_state: TurnState) -> None:
        fresh_state.apply(StreamEvent(kind="final", text="Done."))
        assert fresh_state.done is True
        assert fresh_state.final_text == "Done."

    def test_error_sets_errored(self, fresh_state: TurnState) -> None:
        fresh_state.apply(StreamEvent(kind="error", text="Boom"))
        assert fresh_state.errored is True
        assert fresh_state.done is True
        assert fresh_state.error_message == "Boom"

    def test_cancelled_sets_cancelled(self, fresh_state: TurnState) -> None:
        fresh_state.apply(StreamEvent(kind="cancelled", text="User cancelled"))
        assert fresh_state.cancelled is True
        assert fresh_state.done is True


class TestMultiplexedBackwardCompatibility:
    """The multiplexed event kinds must degrade cleanly into CLI-renderable state."""

    @pytest.mark.parametrize("kind", ["plan_update", "rlm_executing", "memory_update"])
    def test_appends_to_status_and_timeline(
        self, fresh_state: TurnState, kind: StreamEventKind
    ) -> None:
        event = StreamEvent(kind=kind, text=f"Test {kind} event")
        fresh_state.apply(event)

        assert f"Test {kind} event" in fresh_state.status_lines
        assert f"Test {kind} event" in fresh_state.tool_timeline

    @pytest.mark.parametrize("kind", ["plan_update", "rlm_executing", "memory_update"])
    def test_does_not_set_done(
        self, fresh_state: TurnState, kind: StreamEventKind
    ) -> None:
        event = StreamEvent(kind=kind, text="test")
        fresh_state.apply(event)
        assert fresh_state.done is False


# ---------------------------------------------------------------------------
# ExecutionStepBuilder.from_stream_event
# ---------------------------------------------------------------------------


class TestExecutionStepBuilder:
    """``from_stream_event`` must produce correct ``ExecutionStepType`` values."""

    def test_plan_update_maps_to_llm(self, builder: ExecutionStepBuilder) -> None:
        step = builder.from_stream_event(
            kind="plan_update",
            text="Moving to step 2",
            payload={},
            timestamp=time.time(),
        )
        assert step is not None
        assert step.type == "llm"
        assert step.label == "plan_update"

    def test_rlm_executing_maps_to_repl(self, builder: ExecutionStepBuilder) -> None:
        step = builder.from_stream_event(
            kind="rlm_executing",
            text="Executing sub-agent",
            payload={},
            timestamp=time.time(),
        )
        assert step is not None
        assert step.type == "repl"
        assert step.label == "rlm_executing"

    def test_memory_update_maps_to_memory(self, builder: ExecutionStepBuilder) -> None:
        step = builder.from_stream_event(
            kind="memory_update",
            text="Saved fact",
            payload={},
            timestamp=time.time(),
        )
        assert step is not None
        assert step.type == "memory"
        assert step.label == "memory_update"

    def test_tool_call_maps_to_tool(self, builder: ExecutionStepBuilder) -> None:
        step = builder.from_stream_event(
            kind="tool_call",
            text="Running list_files",
            payload={"tool_name": "list_files"},
            timestamp=time.time(),
        )
        assert step is not None
        assert step.type == "tool"
        assert step.label == "list_files"

    def test_memory_tool_maps_to_memory_type(
        self, builder: ExecutionStepBuilder
    ) -> None:
        step = builder.from_stream_event(
            kind="tool_call",
            text="memory_write",
            payload={"tool_name": "memory_write"},
            timestamp=time.time(),
        )
        assert step is not None
        assert step.type == "memory"

    def test_final_maps_to_output(self, builder: ExecutionStepBuilder) -> None:
        step = builder.from_stream_event(
            kind="final",
            text="Complete.",
            payload={"trajectory": {}},
            timestamp=time.time(),
        )
        assert step is not None
        assert step.type == "output"

    def test_assistant_token_returns_none(self, builder: ExecutionStepBuilder) -> None:
        step = builder.from_stream_event(
            kind="assistant_token",
            text="Hi",
            payload={},
            timestamp=time.time(),
        )
        assert step is None

    def test_step_ids_are_sequential(self, builder: ExecutionStepBuilder) -> None:
        steps = []
        for kind in ["reasoning_step", "tool_call", "tool_result"]:
            step = builder.from_stream_event(
                kind=kind, text=f"test {kind}", payload={}, timestamp=time.time()
            )
            if step:
                steps.append(step)

        ids = [s.id for s in steps]
        assert len(ids) == len(set(ids)), "Step IDs must be unique"

    def test_parent_linking_for_tool_result(
        self, builder: ExecutionStepBuilder
    ) -> None:
        call_step = builder.from_stream_event(
            kind="tool_call",
            text="list_files",
            payload={"tool_name": "list_files"},
            timestamp=time.time(),
        )
        result_step = builder.from_stream_event(
            kind="tool_result",
            text="files found",
            payload={},
            timestamp=time.time(),
        )
        assert call_step is not None
        assert result_step is not None
        assert result_step.parent_id == call_step.id
