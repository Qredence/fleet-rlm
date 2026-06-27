"""Unit tests for turn_inputs step building and projection.

Tests VAL-B-022: step_builder.from_runtime_event handles TURN_INPUTS for historical replay.
"""

from fleet_rlm.api.events import ExecutionStepBuilder
from fleet_rlm.api.events.project_graph import project_graph
from fleet_rlm.api.events.step_builder_extractors import ExecutionStepType
from fleet_rlm.runtime.events import (
    RuntimeEvent,
    TurnInputRow,
)


class TestStepBuilderTurnInputs:
    """VAL-B-022: ExecutionStepBuilder handles turn_inputs events."""

    def test_from_runtime_event_with_turn_inputs_creates_step(self):
        """turn_inputs events produce ExecutionStep with type='turn_inputs'."""
        builder = ExecutionStepBuilder(run_id="run-1")
        rows = [
            TurnInputRow(label="Request", kind="request", value="What is the weather?", preview="What is the weather?"),
            TurnInputRow(label="History", kind="history", value=[], preview="0 messages"),
            TurnInputRow(
                label="Core Memory",
                kind="core_memory",
                value="User prefers concise answers",
                preview="User prefers concise answers",
            ),
        ]
        event = RuntimeEvent.turn_inputs(rows)

        step = builder.from_runtime_event(event)

        assert step is not None
        assert step.type == "turn_inputs"
        assert step.label == "turn_inputs"
        assert step.id.startswith("run-1")

    def test_turn_inputs_step_preserves_rows_payload(self):
        """turn_inputs step input/output preserves the rows array."""
        builder = ExecutionStepBuilder(run_id="run-1")
        rows = [
            TurnInputRow(label="Request", kind="request", value="query", preview="query"),
            TurnInputRow(label="Skills", kind="skills", value=["skill-a", "skill-b"], preview="2 skills"),
        ]
        event = RuntimeEvent.turn_inputs(rows)

        step = builder.from_runtime_event(event)

        assert step is not None
        # Check input contains rows
        assert "rows" in step.input
        assert isinstance(step.input["rows"], list)
        assert len(step.input["rows"]) == 2
        assert step.input["rows"][0]["label"] == "Request"
        assert step.input["rows"][0]["kind"] == "request"
        assert step.input["rows"][1]["label"] == "Skills"
        assert step.input["rows"][1]["kind"] == "skills"

        # Check output contains rows
        assert "rows" in step.output
        assert len(step.output["rows"]) == 2

    def test_turn_inputs_step_with_empty_rows(self):
        """turn_inputs with empty rows array still produces valid step."""
        builder = ExecutionStepBuilder(run_id="run-1")
        event = RuntimeEvent.turn_inputs([])

        step = builder.from_runtime_event(event)

        assert step is not None
        assert step.type == "turn_inputs"
        assert step.input["rows"] == []
        assert step.output["rows"] == []

    def test_turn_inputs_step_type_in_literal(self):
        """'turn_inputs' is a valid ExecutionStepType."""
        from typing import get_args

        valid_types = get_args(ExecutionStepType)
        assert "turn_inputs" in valid_types

    def test_project_graph_turn_inputs(self):
        """project_graph correctly handles turn_inputs events."""
        builder = ExecutionStepBuilder(run_id="run-1")
        rows = [
            TurnInputRow(label="Request", kind="request", value="test", preview="test"),
        ]
        event = RuntimeEvent.turn_inputs(rows)

        step = project_graph(event, builder)

        assert step is not None
        assert step.type == "turn_inputs"
        assert step.label == "turn_inputs"
        assert "rows" in step.input
        assert "rows" in step.output

    def test_turn_inputs_step_timestamp(self):
        """turn_inputs step preserves event timestamp."""
        builder = ExecutionStepBuilder(run_id="run-1")
        event = RuntimeEvent.turn_inputs([])

        step = builder.from_runtime_event(event)

        assert step is not None
        assert step.timestamp == event.timestamp.timestamp()

    def test_turn_inputs_step_parent_id(self):
        """turn_inputs step uses root_id as parent."""
        builder = ExecutionStepBuilder(run_id="run-1")
        event = RuntimeEvent.turn_inputs([])

        step = builder.from_runtime_event(event)

        assert step is not None
        assert step.parent_id == "run-1:root"

    def test_turn_inputs_multiple_steps_increment_ids(self):
        """Multiple turn_inputs events get incrementing step IDs."""
        builder = ExecutionStepBuilder(run_id="run-1")
        event1 = RuntimeEvent.turn_inputs([])
        event2 = RuntimeEvent.turn_inputs([])

        step1 = builder.from_runtime_event(event1)
        step2 = builder.from_runtime_event(event2)

        assert step1 is not None
        assert step2 is not None
        assert step1.id != step2.id
        assert step1.id == "run-1:s1"
        assert step2.id == "run-1:s2"

    def test_turn_inputs_with_complex_row_values(self):
        """turn_inputs handles rows with complex nested values."""
        builder = ExecutionStepBuilder(run_id="run-1")
        rows = [
            TurnInputRow(
                label="History",
                kind="history",
                value={
                    "messages": [
                        {"role": "user", "content": "Hello"},
                        {"role": "assistant", "content": "Hi there"},
                    ]
                },
                preview="2 messages",
            ),
            TurnInputRow(
                label="Context",
                kind="context",
                value={
                    "document_text": "Long document content...",
                    "document_path": "/path/to/doc.txt",
                },
                preview="doc.txt",
            ),
        ]
        event = RuntimeEvent.turn_inputs(rows)

        step = builder.from_runtime_event(event)

        assert step is not None
        assert step.input["rows"][0]["value"]["messages"][0]["role"] == "user"
        assert step.input["rows"][1]["value"]["document_path"] == "/path/to/doc.txt"

    def test_turn_inputs_step_serialization(self):
        """turn_inputs step serializes to dict correctly."""
        builder = ExecutionStepBuilder(run_id="run-1")
        rows = [
            TurnInputRow(label="Request", kind="request", value="test", preview="test"),
        ]
        event = RuntimeEvent.turn_inputs(rows)

        step = builder.from_runtime_event(event)
        step_dict = step.model_dump()

        assert step_dict["type"] == "turn_inputs"
        assert step_dict["label"] == "turn_inputs"
        assert isinstance(step_dict["input"]["rows"], list)
        assert isinstance(step_dict["output"]["rows"], list)
        assert step_dict["id"].startswith("run-1")
