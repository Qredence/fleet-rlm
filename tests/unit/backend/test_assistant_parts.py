"""Canonical AssistantPart vocabulary tests."""

from uuid import uuid4

import pytest
from pydantic import TypeAdapter, ValidationError

from fleet_rlm.sessions.assistant_parts import (
    AssistantPart,
    assistant_part_from_model,
    assistant_part_from_payload,
    assistant_part_to_model,
)
from fleet_rlm.sessions.committed_turn import (
    ArtifactPart,
    AttachmentPart,
    CodePart,
    CommittedTurn,
    CommittedTurnCodec,
    OutputPart,
    ReasoningPart,
    SkillPart,
    StatusPart,
    StepPart,
    StructuredResultPart,
    TextPart,
    ToolCallPart,
    UsagePart,
    WarningPart,
)

_ADAPTER = TypeAdapter(AssistantPart)


def _canonical_turn() -> CommittedTurn:
    artifact_id = uuid4()
    attachment_id = uuid4()
    return CommittedTurn(
        schema_version=1,
        parts=(
            StepPart(state="started", step=1),
            ReasoningPart(text="inspect the file", step=1),
            CodePart(code="print(42)", step=1),
            OutputPart(output="42\n", step=1),
            ToolCallPart(
                tool_call_id="call-1",
                tool_name="read_project_text",
                state="completed",
                input={"path": "notes.md"},
                output={"content": "notes"},
            ),
            SkillPart(skill_id="dspy-rlm", name="DSPy RLM", phase="activated", trust="bundled"),
            AttachmentPart(attachment_id=attachment_id, phase="read", filename="notes.md", byte_size=5),
            WarningPart(message="some evidence omitted", code="detail_overflow"),
            StatusPart(phase="execution", status="degraded", message="cache unavailable"),
            StepPart(state="finished", step=1, duration_ms=8),
            ArtifactPart(
                artifact_id=artifact_id,
                kind="markdown",
                title="Report",
                media_type="text/markdown",
                byte_size=7,
                checksum_sha256="a" * 64,
            ),
            UsagePart(value={"iterations": 1, "observed_lm_usage": {}, "duration_ms": 8}),
            StructuredResultPart(schema_id="fleet.default", schema_version="1", value={"answer": "42"}),
            TextPart(text="done"),
        ),
    )


def test_assistant_part_is_a_closed_discriminated_union() -> None:
    payload = CommittedTurnCodec.encode(_canonical_turn())["parts"]
    parsed = [_ADAPTER.validate_python(part, strict=False) for part in payload]
    assert len(parsed) == len(payload)
    assert all(part.type == wire["type"] for part, wire in zip(parsed, payload, strict=True))

    with pytest.raises(ValidationError):
        _ADAPTER.validate_python({"type": "future-part", "value": {}})
    with pytest.raises(ValidationError):
        _ADAPTER.validate_python({"type": "text", "text": "ok", "unknown": True})


def test_assistant_part_models_round_trip_runtime_parts_without_loss() -> None:
    committed = _canonical_turn()
    assert tuple(assistant_part_from_payload(payload) for payload in CommittedTurnCodec.encode(committed)["parts"])
    assert (
        tuple(
            assistant_part_from_payload(assistant_part_to_model(part).model_dump(mode="json"))
            for part in committed.parts
        )
        == committed.parts
    )
    assert CommittedTurnCodec.decode(CommittedTurnCodec.encode(committed)) == committed


def test_reload_projection_consumes_canonical_part_vocabulary() -> None:
    from fleet_rlm.api.ui_message import assistant_turn_to_ui_message
    from fleet_rlm.sessions.models import AssistantTurnRecord

    committed = _canonical_turn()
    parsed_parts = tuple(
        assistant_part_from_payload(payload) for payload in CommittedTurnCodec.encode(committed)["parts"]
    )
    reparsed = CommittedTurn(schema_version=1, parts=parsed_parts, trace_id=None)
    record = AssistantTurnRecord(uuid4(), uuid4(), 2, reparsed, uuid4())

    assert (
        assistant_turn_to_ui_message(record)["parts"]
        == assistant_turn_to_ui_message(
            AssistantTurnRecord(record.id, record.session_id, record.sequence, committed, record.run_id)
        )["parts"]
    )


def test_tool_call_state_error_semantics_are_canonical() -> None:
    valid = {
        "type": "tool_call",
        "tool_call_id": "call-1",
        "tool_name": "read_project_text",
        "state": "failed",
        "input": {"path": "notes.md"},
        "output": None,
        "error": "sandbox unavailable",
    }
    parsed = _ADAPTER.validate_python(valid, strict=False)
    assert parsed.state == "failed"
    assert assistant_part_from_model(parsed) == ToolCallPart(
        tool_call_id=valid["tool_call_id"],
        tool_name=valid["tool_name"],
        state="failed",
        input=valid["input"],
        output=None,
        error=valid["error"],
    )

    invalid_cases = (
        (
            "completed tool calls cannot contain an error",
            {**valid, "state": "completed", "error": "must not appear"},
        ),
        ("failed tool calls require a non-blank error", {**valid, "error": None}),
        ("failed tool calls require a non-blank error", {**valid, "error": "  "}),
    )
    for message, payload in invalid_cases:
        with pytest.raises(ValidationError, match=message):
            _ADAPTER.validate_python(payload, strict=False)


def test_skill_phase_metadata_semantics_are_canonical() -> None:
    activated = {
        "type": "skill",
        "skill_id": "dspy-rlm",
        "name": "DSPy RLM",
        "phase": "activated",
        "version": "1.0.0",
        "trust": "bundled",
        "affordances": ["llm_query"],
    }
    parsed = _ADAPTER.validate_python(activated, strict=False)
    assert assistant_part_from_model(parsed) == SkillPart(
        skill_id=activated["skill_id"],
        name=activated["name"],
        phase="activated",
        version=activated["version"],
        trust=activated["trust"],
        affordances=("llm_query",),
    )

    for message, payload in (
        ("activated skills require non-blank trust metadata", {**activated, "trust": None}),
        ("loaded skills cannot contain activation metadata", {**activated, "phase": "loaded"}),
        ("loaded skills cannot contain activation metadata", {**activated, "phase": "loaded", "trust": None}),
        (
            "loaded skills cannot contain activation metadata",
            {**activated, "phase": "loaded", "trust": None, "affordances": ["unexpected"]},
        ),
    ):
        with pytest.raises(ValidationError, match=message):
            _ADAPTER.validate_python(payload, strict=False)
    loaded = {**activated, "phase": "loaded", "trust": None, "affordances": []}
    assert _ADAPTER.validate_python(loaded, strict=False).phase == "loaded"


@pytest.mark.parametrize(
    "payload, field_message",
    (
        ({"type": "text", "text": "  \n\t"}, "final text is required"),
        ({"type": "warning", "message": " "}, "warning message is required"),
        ({"type": "status", "phase": " ", "status": "running"}, "status semantics is required"),
        ({"type": "status", "phase": "execution", "status": " "}, "status semantics is required"),
        (
            {"type": "tool_call", "tool_call_id": " ", "tool_name": "tool", "state": "completed", "input": {}},
            "tool call identity is required",
        ),
        (
            {"type": "tool_call", "tool_call_id": "call", "tool_name": " ", "state": "completed", "input": {}},
            "tool call identity is required",
        ),
        ({"type": "skill", "skill_id": " ", "name": "skill", "phase": "loaded"}, "skill identity is required"),
        ({"type": "skill", "skill_id": "skill", "name": " ", "phase": "loaded"}, "skill identity is required"),
        (
            {
                "type": "artifact",
                "artifact_id": str(uuid4()),
                "kind": "markdown",
                "title": None,
                "media_type": " ",
                "byte_size": 0,
                "checksum_sha256": "a" * 64,
            },
            "artifact media_type is required",
        ),
        (
            {"type": "structured_result", "schema_id": " ", "schema_version": "1", "value": {}},
            "structured result schema identity is required",
        ),
        (
            {"type": "structured_result", "schema_id": "fleet.default", "schema_version": " ", "value": {}},
            "structured result schema identity is required",
        ),
    ),
)
def test_identity_and_message_fields_reject_whitespace_only_values(payload: dict, field_message: str) -> None:
    with pytest.raises(ValidationError, match=field_message):
        _ADAPTER.validate_python(payload, strict=False)


def test_artifact_checksum_is_normalized_at_the_canonical_boundary() -> None:
    checksum = "A1B2c3D4" * 8
    payload = {
        "type": "artifact",
        "artifact_id": str(uuid4()),
        "kind": "json",
        "title": None,
        "media_type": "application/json",
        "byte_size": 12,
        "checksum_sha256": checksum,
    }
    parsed = _ADAPTER.validate_python(payload, strict=False)
    assert parsed.checksum_sha256 == checksum.lower()
    assert assistant_part_from_model(parsed).checksum_sha256 == checksum.lower()


def test_usage_payload_uses_the_runtime_usage_contract() -> None:
    valid = {
        "type": "usage",
        "value": {
            "iterations": 2,
            "observed_lm_usage": {"root": {"prompt_tokens": 10, "completion_tokens": 4, "reasoning_tokens": 1}},
            "duration_ms": 750,
        },
    }
    parsed = _ADAPTER.validate_python(valid, strict=False)
    assert assistant_part_from_model(parsed).value["iterations"] == 2

    consumption_cases = (
        {"iterations": 1, "duration_ms": 1},
        {"iterations": "two", "observed_lm_usage": {}, "duration_ms": 1},
        {"iterations": 1, "observed_lm_usage": {"root": {"credentials": "sk-secret"}}, "duration_ms": 1},
    )
    for value in consumption_cases:
        with pytest.raises(ValidationError):
            _ADAPTER.validate_python({"type": "usage", "value": value}, strict=False)


def test_non_json_tool_and_result_values_are_rejected_before_runtime_conversion() -> None:
    tool_payload = {
        "type": "tool_call",
        "tool_call_id": "call-json",
        "tool_name": "read_project_text",
        "state": "completed",
        "input": {1: "one"},
    }
    with pytest.raises(ValidationError):
        _ADAPTER.validate_python(tool_payload, strict=False)

    structured_payload = {
        "type": "structured_result",
        "schema_id": "fleet.default",
        "schema_version": "1",
        "value": {1: "one"},
    }
    with pytest.raises(ValidationError):
        _ADAPTER.validate_python(structured_payload, strict=False)


def test_invalid_part_cannot_pass_adapter_and_fail_later_in_runtime_conversion() -> None:
    payload = {"type": "text", "text": " \n"}
    with pytest.raises(ValidationError):
        _ADAPTER.validate_python(payload, strict=False)
    with pytest.raises(ValidationError):
        assistant_part_from_payload(payload)

    envelope = {
        "schema_version": 1,
        "parts": [
            {
                "type": "usage",
                "value": {"iterations": 0, "observed_lm_usage": {}, "duration_ms": 0},
            },
            payload,
        ],
    }
    from fleet_rlm.sessions.committed_turn import CommittedTurnValidationError

    with pytest.raises(CommittedTurnValidationError):
        CommittedTurnCodec.decode(envelope)
