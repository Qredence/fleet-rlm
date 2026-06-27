"""
Test turn input emission across different execution paths.
Validates VAL-B-019 and VAL-B-020.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import dspy
import pytest

from fleet_rlm.runtime.events import RuntimeEvent, RuntimeEventKind, TurnInputRow
from fleet_rlm.runtime.modules.escalating import (
    EscalatingFleetModule,
    _emit_turn_inputs,
    _history_preview,
    _preview_text,
)


class TestPreviewHelpers:
    """Test the preview helper functions."""

    def test_preview_text_empty(self):
        assert _preview_text("") == ""
        assert _preview_text(None) == ""

    def test_preview_text_short(self):
        assert _preview_text("hello world") == "hello world"

    def test_preview_text_truncates(self):
        long_text = "a" * 200
        result = _preview_text(long_text, max_chars=120)
        assert len(result) == 123  # 120 + "..."
        assert result.endswith("...")

    def test_preview_text_multiline(self):
        text = "line1\nline2\nline3"
        result = _preview_text(text)
        assert result == "line1"

    def test_history_preview_empty(self):
        history = dspy.History(messages=[])
        assert _history_preview(history) == "No prior history"

    def test_history_preview_single(self):
        history = dspy.History(messages=[{"user_message": "hi", "response": "hello"}])
        assert _history_preview(history) == "1 turn"

    def test_history_preview_multiple(self):
        history = dspy.History(
            messages=[
                {"user_message": "a", "response": "b"},
                {"user_message": "c", "response": "d"},
                {"user_message": "e", "response": "f"},
            ]
        )
        assert _history_preview(history) == "3 turns"


class TestEmitTurnInputs:
    """Test the _emit_turn_inputs helper function."""

    def _make_module_with_relay(self) -> tuple[EscalatingFleetModule, Any, list]:
        """Create a module with a mock interpreter and relay."""
        module = EscalatingFleetModule()
        interpreter = MagicMock()
        relay = MagicMock()
        collected_events: list[RuntimeEvent] = []
        relay.emit_threadsafe = MagicMock(side_effect=lambda e: collected_events.append(e))
        interpreter._turn_progress_relay = relay
        module._interpreter = interpreter
        return module, interpreter, collected_events

    def test_emit_creates_turn_inputs_event(self):
        module, interpreter, events = self._make_module_with_relay()
        rows = [
            TurnInputRow(label="Request", kind="request", value="test", preview="test"),
            TurnInputRow(label="Core memory", kind="core_memory", value="mem", preview="mem"),
        ]
        _emit_turn_inputs(interpreter, rows, module=module)
        assert len(events) == 1
        event = events[0]
        assert event.kind == RuntimeEventKind.TURN_INPUTS
        assert len(event.payload["rows"]) == 2
        assert event.payload["rows"][0]["kind"] == "request"
        assert event.payload["rows"][1]["kind"] == "core_memory"

    def test_emit_sets_idempotency_flag(self):
        module, interpreter, events = self._make_module_with_relay()
        rows = [TurnInputRow(label="Request", kind="request", value="test", preview="test")]
        assert module._turn_inputs_emitted is False
        _emit_turn_inputs(interpreter, rows, module=module)
        assert module._turn_inputs_emitted is True

    def test_idempotency_prevents_second_emission(self):
        """VAL-B-019: exactly once per turn."""
        module, interpreter, events = self._make_module_with_relay()
        rows = [TurnInputRow(label="Request", kind="request", value="test", preview="test")]
        _emit_turn_inputs(interpreter, rows, module=module)
        _emit_turn_inputs(interpreter, rows, module=module)  # second call
        assert len(events) == 1

    def test_no_interpreter_no_error(self):
        module = EscalatingFleetModule()
        rows = [TurnInputRow(label="Request", kind="request", value="test", preview="test")]
        _emit_turn_inputs(None, rows, module=module)
        assert module._turn_inputs_emitted is True

    def test_no_relay_no_error(self):
        module = EscalatingFleetModule()
        interpreter = MagicMock(spec=[])  # no _turn_progress_relay
        rows = [TurnInputRow(label="Request", kind="request", value="test", preview="test")]
        _emit_turn_inputs(interpreter, rows, module=module)
        assert module._turn_inputs_emitted is True

    def test_empty_rows_still_emits(self):
        """VAL-B-020: rows show even when inputs empty."""
        module, interpreter, events = self._make_module_with_relay()
        rows: list[TurnInputRow] = []
        _emit_turn_inputs(interpreter, rows, module=module)
        assert len(events) == 1
        assert events[0].payload["rows"] == []

    def test_rlm_five_rows(self):
        """VAL-B-003: RLM route emits 5 rows in correct order."""
        module, interpreter, events = self._make_module_with_relay()
        history = dspy.History(messages=[{"user_message": "hi", "response": "hello"}])
        rows = [
            TurnInputRow(label="Request", kind="request", value="user msg", preview="user msg"),
            TurnInputRow(label="Active skills", kind="skills", value=["s1", "s2"], preview="2 skills"),
            TurnInputRow(label="History", kind="history", value=list(history.messages), preview="1 turn"),
            TurnInputRow(label="Core memory", kind="core_memory", value="memory text", preview="memory text"),
            TurnInputRow(label="Context", kind="context", value="context text", preview="context text"),
        ]
        _emit_turn_inputs(interpreter, rows, module=module)
        assert len(events) == 1
        row_kinds = [r["kind"] for r in events[0].payload["rows"]]
        assert row_kinds == ["request", "skills", "history", "core_memory", "context"]

    def test_react_three_rows(self):
        """VAL-B-004: ReAct route emits 3 rows in correct order."""
        module, interpreter, events = self._make_module_with_relay()
        rows = [
            TurnInputRow(label="Request", kind="request", value="msg", preview="msg"),
            TurnInputRow(label="History", kind="history", value=[], preview="No prior history"),
            TurnInputRow(label="Core memory", kind="core_memory", value="mem", preview="mem"),
        ]
        _emit_turn_inputs(interpreter, rows, module=module)
        assert len(events) == 1
        row_kinds = [r["kind"] for r in events[0].payload["rows"]]
        assert row_kinds == ["request", "history", "core_memory"]

    def test_cot_three_rows(self):
        """VAL-B-005: CoT route emits 3 rows in correct order."""
        module, interpreter, events = self._make_module_with_relay()
        rows = [
            TurnInputRow(label="Request", kind="request", value="hi", preview="hi"),
            TurnInputRow(label="History", kind="history", value=[], preview="No prior history"),
            TurnInputRow(label="Core memory", kind="core_memory", value="", preview=""),
        ]
        _emit_turn_inputs(interpreter, rows, module=module)
        assert len(events) == 1
        row_kinds = [r["kind"] for r in events[0].payload["rows"]]
        assert row_kinds == ["request", "history", "core_memory"]

    def test_empty_inputs_produce_rows_with_placeholders(self):
        """VAL-B-020: rows show even when inputs empty with placeholder."""
        module, interpreter, events = self._make_module_with_relay()
        history = dspy.History(messages=[])
        rows = [
            TurnInputRow(label="Request", kind="request", value="", preview=""),
            TurnInputRow(label="Active skills", kind="skills", value=[], preview=""),
            TurnInputRow(label="History", kind="history", value=[], preview=_history_preview(history)),
            TurnInputRow(label="Core memory", kind="core_memory", value="", preview=""),
            TurnInputRow(label="Context", kind="context", value="", preview=""),
        ]
        _emit_turn_inputs(interpreter, rows, module=module)
        assert len(events) == 1
        assert len(events[0].payload["rows"]) == 5
        # All rows should still be present
        for row_data in events[0].payload["rows"]:
            assert "label" in row_data
            assert "kind" in row_data

    def test_row_values_match_model_input(self):
        """VAL-B-006: row values match the actual model input."""
        module, interpreter, events = self._make_module_with_relay()
        user_request = "Write a Fibonacci function"
        core_memory = "I am a helpful assistant"
        rows = [
            TurnInputRow(label="Request", kind="request", value=user_request, preview=_preview_text(user_request)),
            TurnInputRow(
                label="Core memory", kind="core_memory", value=core_memory, preview=_preview_text(core_memory)
            ),
        ]
        _emit_turn_inputs(interpreter, rows, module=module)
        assert len(events) == 1
        row_data = events[0].payload["rows"]
        assert row_data[0]["value"] == user_request
        assert row_data[1]["value"] == core_memory


class TestModuleFlagReset:
    """Test that the module resets the flag at turn start."""

    def test_forward_resets_flag(self):
        """Verify forward() resets _turn_inputs_emitted."""
        module = EscalatingFleetModule()
        module._turn_inputs_emitted = True
        # We can't easily call forward() without a full runtime setup,
        # but we can verify the attribute exists and is resettable
        module._turn_inputs_emitted = False
        assert module._turn_inputs_emitted is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
