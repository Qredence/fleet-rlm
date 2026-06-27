"""Unit tests for RuntimeEventKind.TURN_INPUTS and RuntimeEvent.turn_inputs factory.

Covers VAL-B-001 and VAL-B-002 assertions.
"""

import pytest

from fleet_rlm.runtime.events import (
    RuntimeEvent,
    RuntimeEventKind,
    TurnInputRow,
)


class TestRuntimeEventKindTurnInputs:
    """VAL-B-001: RuntimeEventKind.TURN_INPUTS exists and is unique."""

    def test_turn_inputs_exists(self) -> None:
        """RuntimeEventKind.TURN_INPUTS member exists."""
        assert hasattr(RuntimeEventKind, "TURN_INPUTS")

    def test_turn_inputs_string_value(self) -> None:
        """RuntimeEventKind.TURN_INPUTS has string value 'turn_inputs'."""
        assert RuntimeEventKind.TURN_INPUTS == "turn_inputs"
        assert RuntimeEventKind.TURN_INPUTS.value == "turn_inputs"

    def test_turn_inputs_round_trip(self) -> None:
        """RuntimeEventKind('turn_inputs') round-trip succeeds."""
        kind = RuntimeEventKind("turn_inputs")
        assert kind == RuntimeEventKind.TURN_INPUTS

    def test_turn_inputs_unique_among_existing_members(self) -> None:
        """String value 'turn_inputs' is unique across all enum members."""
        all_values = [member.value for member in RuntimeEventKind]
        assert all_values.count("turn_inputs") == 1

    def test_turn_inputs_not_collide_with_terminal_kinds(self) -> None:
        """TURN_INPUTS is not a terminal kind."""
        assert not RuntimeEventKind.TURN_INPUTS.is_terminal()
        assert RuntimeEventKind.TURN_INPUTS not in RuntimeEventKind.terminal_kinds()


class TestTurnInputRow:
    """TurnInputRow dataclass carries label, kind, value, preview fields."""

    def test_turn_input_row_construction(self) -> None:
        """TurnInputRow can be constructed with all required fields."""
        row = TurnInputRow(
            label="Request",
            kind="request",
            value="What is the meaning of life?",
            preview="What is the meaning...",
        )
        assert row.label == "Request"
        assert row.kind == "request"
        assert row.value == "What is the meaning of life?"
        assert row.preview == "What is the meaning..."

    def test_turn_input_row_preview_defaults_to_empty(self) -> None:
        """TurnInputRow.preview defaults to empty string."""
        row = TurnInputRow(label="History", kind="history", value=[])
        assert row.preview == ""

    def test_turn_input_row_all_kinds(self) -> None:
        """TurnInputRow accepts all valid kind values."""
        valid_kinds = ["request", "skills", "history", "core_memory", "context"]
        for kind in valid_kinds:
            row = TurnInputRow(label=kind.title(), kind=kind, value="test")
            assert row.kind == kind

    def test_turn_input_row_invalid_kind_rejected(self) -> None:
        """TurnInputRow rejects invalid kind values."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            TurnInputRow(label="Invalid", kind="invalid_kind", value="test")

    def test_turn_input_row_value_can_be_any_type(self) -> None:
        """TurnInputRow.value accepts str, list, and dict."""
        # String value
        row_str = TurnInputRow(label="Request", kind="request", value="text")
        assert row_str.value == "text"

        # List value
        row_list = TurnInputRow(label="Skills", kind="skills", value=["skill1", "skill2"])
        assert row_list.value == ["skill1", "skill2"]

        # Dict value
        row_dict = TurnInputRow(
            label="Context",
            kind="context",
            value={"document": "content", "path": "/path"},
        )
        assert row_dict.value == {"document": "content", "path": "/path"}

    def test_turn_input_row_model_dump(self) -> None:
        """TurnInputRow.model_dump() produces expected dict structure."""
        row = TurnInputRow(
            label="Core Memory",
            kind="core_memory",
            value={"key": "value"},
            preview="Core memory preview",
        )
        dumped = row.model_dump()
        assert dumped == {
            "label": "Core Memory",
            "kind": "core_memory",
            "value": {"key": "value"},
            "preview": "Core memory preview",
        }


class TestRuntimeEventTurnInputsFactory:
    """VAL-B-002: RuntimeEvent.turn_inputs(rows) factory is callable and well-formed."""

    def test_turn_inputs_factory_callable(self) -> None:
        """RuntimeEvent.turn_inputs is a callable classmethod."""
        assert callable(RuntimeEvent.turn_inputs)

    def test_turn_inputs_factory_empty_rows(self) -> None:
        """RuntimeEvent.turn_inputs([]) does not raise."""
        event = RuntimeEvent.turn_inputs([])
        assert event.kind == RuntimeEventKind.TURN_INPUTS
        assert event.payload == {"rows": []}

    def test_turn_inputs_factory_single_row(self) -> None:
        """RuntimeEvent.turn_inputs with single row creates well-formed event."""
        row = TurnInputRow(
            label="Request",
            kind="request",
            value="User query",
            preview="User query",
        )
        event = RuntimeEvent.turn_inputs([row])

        assert event.kind == RuntimeEventKind.TURN_INPUTS
        assert event.text == "Turn inputs"
        assert "rows" in event.payload
        assert len(event.payload["rows"]) == 1
        assert event.payload["rows"][0] == row.model_dump()

    def test_turn_inputs_factory_multiple_rows(self) -> None:
        """RuntimeEvent.turn_inputs with multiple rows preserves order."""
        rows = [
            TurnInputRow(label="Request", kind="request", value="query", preview="query"),
            TurnInputRow(label="History", kind="history", value=[], preview="0 turns"),
            TurnInputRow(label="Core Memory", kind="core_memory", value="memory", preview="memory"),
        ]
        event = RuntimeEvent.turn_inputs(rows)

        assert event.kind == RuntimeEventKind.TURN_INPUTS
        assert len(event.payload["rows"]) == 3
        assert event.payload["rows"][0]["kind"] == "request"
        assert event.payload["rows"][1]["kind"] == "history"
        assert event.payload["rows"][2]["kind"] == "core_memory"

    def test_turn_inputs_factory_rlm_route_five_rows(self) -> None:
        """RLM route emits 5 rows: request, skills, history, core_memory, context."""
        rows = [
            TurnInputRow(label="Request", kind="request", value="user request", preview="user request"),
            TurnInputRow(label="Active Skills", kind="skills", value=["skill1"], preview="1 skill"),
            TurnInputRow(label="History", kind="history", value={"messages": []}, preview="0 turns"),
            TurnInputRow(label="Core Memory", kind="core_memory", value="memory text", preview="memory"),
            TurnInputRow(label="Context", kind="context", value={"doc": "text"}, preview="context"),
        ]
        event = RuntimeEvent.turn_inputs(rows)

        assert event.kind == RuntimeEventKind.TURN_INPUTS
        assert len(event.payload["rows"]) == 5
        kinds = [row["kind"] for row in event.payload["rows"]]
        assert kinds == ["request", "skills", "history", "core_memory", "context"]

    def test_turn_inputs_factory_react_cot_route_three_rows(self) -> None:
        """ReAct/CoT route emits 3 rows: request, history, core_memory."""
        rows = [
            TurnInputRow(label="Request", kind="request", value="query", preview="query"),
            TurnInputRow(label="History", kind="history", value=[], preview="0 turns"),
            TurnInputRow(label="Core Memory", kind="core_memory", value="", preview="(empty)"),
        ]
        event = RuntimeEvent.turn_inputs(rows)

        assert event.kind == RuntimeEventKind.TURN_INPUTS
        assert len(event.payload["rows"]) == 3
        kinds = [row["kind"] for row in event.payload["rows"]]
        assert kinds == ["request", "history", "core_memory"]

    def test_turn_inputs_factory_with_actor_and_context(self) -> None:
        """RuntimeEvent.turn_inputs accepts optional actor and context."""
        from fleet_rlm.runtime.events import RuntimeActorContext, RuntimeEventContext

        actor = RuntimeActorContext(actor_kind="root", actor_id="agent-1")
        context = RuntimeEventContext(sandbox_id="sandbox-123")
        rows = [TurnInputRow(label="Request", kind="request", value="test", preview="test")]

        event = RuntimeEvent.turn_inputs(rows, actor=actor, context=context)

        assert event.actor == actor
        assert event.context == context
        assert event.kind == RuntimeEventKind.TURN_INPUTS

    def test_turn_inputs_payload_rows_are_serialized_dicts(self) -> None:
        """Payload rows are serialized as dicts via model_dump()."""
        row = TurnInputRow(
            label="Request",
            kind="request",
            value={"complex": "value"},
            preview="preview",
        )
        event = RuntimeEvent.turn_inputs([row])

        # Rows in payload should be dicts, not TurnInputRow objects
        assert isinstance(event.payload["rows"][0], dict)
        assert event.payload["rows"][0] == row.model_dump()


class TestTurnInputRowExports:
    """__all__ exports both RuntimeEvent.turn_inputs and TurnInputRow."""

    def test_turn_input_row_in_all(self) -> None:
        """TurnInputRow is exported in __all__."""
        from fleet_rlm.runtime import events

        assert "TurnInputRow" in events.__all__

    def test_turn_input_row_importable(self) -> None:
        """TurnInputRow can be imported from fleet_rlm.runtime.events."""
        from fleet_rlm.runtime.events import TurnInputRow

        assert TurnInputRow is not None
