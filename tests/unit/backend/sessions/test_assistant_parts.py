"""Canonical AssistantPart vocabulary tests."""

from uuid import uuid4

import pytest
from pydantic import TypeAdapter, ValidationError

from fleet_rlm.sessions.assistant_parts import (
    AssistantPart,
    assistant_part_from_model,
)
from fleet_rlm.sessions.committed_turn import (
    ArtifactPart,
    AttachmentPart,
    ChildProgressPart,
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


def _canonical_turn(parent_run_id: str | None = None) -> CommittedTurn:
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
            ChildProgressPart(
                child_id="child-1",
                task_label="Verify the selected evidence",
                state="completed",
                elapsed_ms=24,
                outcome="Evidence cross-check complete",
                code_excerpt="print('checked')",
                output_excerpt="checked",
                cleanup_state="complete",
                parent_run_id=parent_run_id,
            ),
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


def test_artifact_checksum_rejects_non_string_values_as_validation_errors() -> None:
    payload = {
        "type": "artifact",
        "artifact_id": str(uuid4()),
        "kind": "json",
        "title": None,
        "media_type": "application/json",
        "byte_size": 12,
        "checksum_sha256": 0,
    }
    with pytest.raises(ValidationError, match="checksum_sha256 must be a string"):
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
