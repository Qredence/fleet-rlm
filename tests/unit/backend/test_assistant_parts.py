"""Canonical AssistantPart vocabulary tests."""

from uuid import uuid4

import pytest
from pydantic import TypeAdapter, ValidationError

from fleet_rlm.sessions.assistant_parts import (
    AssistantPart,
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
