"""Unit tests for HITL event wiring in the streaming status helpers."""

from __future__ import annotations

import json

import pytest

from fleet_rlm.runtime.execution.streaming_events import (
    try_parse_hitl_request as _try_parse_hitl_request,
)
from fleet_rlm.runtime.schemas import StreamEvent

# ---------------------------------------------------------------------------
# clarification_questions tool
# ---------------------------------------------------------------------------


def test_hitl_request_from_clarification_questions_with_questions():
    """clarification_questions with non-empty questions list emits hitl_request."""
    payload = {"tool_output": json.dumps({"questions": ["What scope?", "Which environment?"]})}
    event = _try_parse_hitl_request("clarification_questions", payload)

    assert event is not None
    assert isinstance(event, StreamEvent)
    assert event.kind == "status"
    assert event.payload["source"] == "clarification_questions"
    assert event.payload["requires_response"] is True
    assert event.payload["options"] == ["What scope?", "Which environment?"]


@pytest.mark.parametrize(
    ("tool_name", "payload"),
    [
        ("clarification_questions", {"tool_output": json.dumps({"questions": []})}),
        ("clarification_questions", {"tool_output": json.dumps({"answer": "something"})}),
        ("clarification_questions", {"tool_output": "This is a plain text response."}),
        (
            "memory_action_intent",
            {"tool_output": json.dumps({"intent": "read_memory_path", "requires_confirmation": False})},
        ),
        ("memory_action_intent", {"tool_output": json.dumps({"intent": "read_memory_path"})}),
        ("load_document", {"tool_output": json.dumps({"questions": ["Should I proceed?"]})}),
        (None, {"tool_output": json.dumps({"questions": ["Yes?"]})}),
        ("clarification_questions", {}),
        ("clarification_questions", {"tool_output": 12345}),
    ],
)
def test_hitl_request_returns_none(tool_name, payload):
    """Various invalid inputs should all return None."""
    assert _try_parse_hitl_request(tool_name, payload) is None


# ---------------------------------------------------------------------------
# memory_action_intent tool
# ---------------------------------------------------------------------------


def test_hitl_request_from_memory_action_intent_requires_confirmation():
    """memory_action_intent with requires_confirmation=True emits hitl_request."""
    payload = {
        "tool_output": json.dumps(
            {
                "intent": "delete_memory_path",
                "requires_confirmation": True,
                "target": "memories/old-project",
            }
        )
    }
    event = _try_parse_hitl_request("memory_action_intent", payload)

    assert event is not None
    assert event.kind == "status"
    assert event.payload["source"] == "memory_action_intent"
    assert event.payload["action"] == "delete_memory_path"
    assert event.payload["requires_response"] is True


def test_hitl_request_payload_has_required_fields():
    """Verify that both HITL request payload shapes contain expected keys."""
    cq_payload = {"tool_output": json.dumps({"questions": ["Q1"]})}
    cq_event = _try_parse_hitl_request("clarification_questions", cq_payload)
    assert cq_event is not None
    for key in ("options", "source", "requires_response"):
        assert key in cq_event.payload, f"Missing key in clarification_questions payload: {key}"

    ma_payload = {"tool_output": json.dumps({"intent": "purge", "requires_confirmation": True})}
    ma_event = _try_parse_hitl_request("memory_action_intent", ma_payload)
    assert ma_event is not None
    for key in ("action", "source", "requires_response"):
        assert key in ma_event.payload, f"Missing key in memory_action_intent payload: {key}"
