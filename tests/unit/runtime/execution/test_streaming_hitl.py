"""Unit tests for HITL event wiring in the streaming status helpers."""

from __future__ import annotations

import json

from fleet_rlm.runtime.execution.streaming import (
    try_parse_hitl_request as _try_parse_hitl_request,
)
from fleet_rlm.runtime.models.streaming import StreamEvent


# ---------------------------------------------------------------------------
# clarification_questions tool
# ---------------------------------------------------------------------------


def test_hitl_request_from_clarification_questions_with_questions():
    """clarification_questions with non-empty questions list emits hitl_request."""
    payload = {
        "tool_output": json.dumps({"questions": ["What scope?", "Which environment?"]})
    }
    event = _try_parse_hitl_request("clarification_questions", payload)

    assert event is not None
    assert isinstance(event, StreamEvent)
    assert event.kind == "hitl_request"
    assert event.payload["source"] == "clarification_questions"
    assert event.payload["requires_response"] is True
    assert event.payload["options"] == ["What scope?", "Which environment?"]


def test_hitl_request_from_clarification_questions_empty_list():
    """clarification_questions with empty questions list returns None."""
    payload = {"tool_output": json.dumps({"questions": []})}
    event = _try_parse_hitl_request("clarification_questions", payload)
    assert event is None


def test_hitl_request_from_clarification_questions_no_questions_key():
    """clarification_questions with JSON missing 'questions' key returns None."""
    payload = {"tool_output": json.dumps({"answer": "something"})}
    event = _try_parse_hitl_request("clarification_questions", payload)
    assert event is None


def test_hitl_request_from_clarification_questions_plain_text():
    """clarification_questions with non-JSON tool_output returns None (can't parse)."""
    payload = {"tool_output": "This is a plain text response."}
    event = _try_parse_hitl_request("clarification_questions", payload)
    assert event is None


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
    assert event.kind == "hitl_request"
    assert event.payload["source"] == "memory_action_intent"
    assert event.payload["action"] == "delete_memory_path"
    assert event.payload["requires_response"] is True


def test_hitl_request_from_memory_action_intent_no_confirmation():
    """memory_action_intent without requires_confirmation=True returns None."""
    payload = {
        "tool_output": json.dumps(
            {
                "intent": "read_memory_path",
                "requires_confirmation": False,
            }
        )
    }
    event = _try_parse_hitl_request("memory_action_intent", payload)
    assert event is None


def test_hitl_request_from_memory_action_intent_confirmation_missing():
    """memory_action_intent with no confirmation key returns None."""
    payload = {"tool_output": json.dumps({"intent": "read_memory_path"})}
    event = _try_parse_hitl_request("memory_action_intent", payload)
    assert event is None


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_hitl_request_returns_none_for_unknown_tool():
    """Unknown tool name never triggers an HITL request."""
    payload = {"tool_output": json.dumps({"questions": ["Should I proceed?"]})}
    event = _try_parse_hitl_request("load_document", payload)
    assert event is None


def test_hitl_request_returns_none_when_tool_name_is_none():
    """None tool name returns None without error."""
    payload = {"tool_output": json.dumps({"questions": ["Yes?"]})}
    event = _try_parse_hitl_request(None, payload)
    assert event is None


def test_hitl_request_returns_none_when_tool_output_missing():
    """Missing tool_output key in payload returns None."""
    event = _try_parse_hitl_request("clarification_questions", {})
    assert event is None


def test_hitl_request_returns_none_when_tool_output_not_string():
    """Non-string tool_output value returns None."""
    payload = {"tool_output": 12345}
    event = _try_parse_hitl_request("clarification_questions", payload)
    assert event is None


def test_hitl_request_payload_has_required_fields():
    """Verify that both HITL request payload shapes contain expected keys."""
    cq_payload = {"tool_output": json.dumps({"questions": ["Q1"]})}
    cq_event = _try_parse_hitl_request("clarification_questions", cq_payload)
    assert cq_event is not None
    for key in ("options", "source", "requires_response"):
        assert key in cq_event.payload, (
            f"Missing key in clarification_questions payload: {key}"
        )

    ma_payload = {
        "tool_output": json.dumps({"intent": "purge", "requires_confirmation": True})
    }
    ma_event = _try_parse_hitl_request("memory_action_intent", ma_payload)
    assert ma_event is not None
    for key in ("action", "source", "requires_response"):
        assert key in ma_event.payload, (
            f"Missing key in memory_action_intent payload: {key}"
        )
