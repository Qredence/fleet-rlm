"""Committed Turn domain contract."""

from __future__ import annotations

from uuid import uuid4

import pytest


def test_committed_turn_codec_round_trips_the_closed_v1_aggregate() -> None:
    from fleet_rlm.sessions.committed_turn import (
        ArtifactPart,
        CommittedTurn,
        CommittedTurnCodec,
        ReasoningPart,
        StructuredResultPart,
        TextPart,
        UsagePart,
    )

    artifact_id = uuid4()
    committed = CommittedTurn(
        schema_version=1,
        parts=(
            ReasoningPart(text="Inspected the bounded corpus", step=1),
            ArtifactPart(
                artifact_id=artifact_id,
                kind="json",
                title="result",
                media_type="application/json",
                byte_size=12,
                checksum_sha256="a" * 64,
            ),
            UsagePart(value={"iterations": 1, "llm_calls": 2}),
            StructuredResultPart(schema_id="analysis", schema_version="1", value={"total": 42}),
            TextPart(text="42"),
        ),
    )

    encoded = CommittedTurnCodec.encode(committed)
    decoded = CommittedTurnCodec.decode(encoded)

    assert decoded == committed
    assert decoded.text == "42"
    assert decoded.structured_result == {"total": 42}
    assert encoded == {
        "schema_version": 1,
        "parts": [
            {"type": "reasoning", "text": "Inspected the bounded corpus", "step": 1},
            {
                "type": "artifact",
                "artifact_id": str(artifact_id),
                "kind": "json",
                "title": "result",
                "media_type": "application/json",
                "byte_size": 12,
                "checksum_sha256": "a" * 64,
            },
            {"type": "usage", "value": {"iterations": 1, "llm_calls": 2}},
            {
                "type": "structured_result",
                "schema_id": "analysis",
                "schema_version": "1",
                "value": {"total": 42},
            },
            {"type": "text", "text": "42"},
        ],
    }


def test_committed_turn_codec_handles_every_execution_part_variant() -> None:
    from fleet_rlm.sessions.committed_turn import (
        AttachmentPart,
        CodePart,
        CommittedTurn,
        CommittedTurnCodec,
        OutputPart,
        SkillPart,
        StepPart,
        TextPart,
        ToolCallPart,
        UsagePart,
        WarningPart,
    )

    committed = CommittedTurn(
        schema_version=1,
        parts=(
            StepPart(state="started", step=1),
            CodePart(code="print(42)", step=1),
            OutputPart(output="42", step=1),
            ToolCallPart(
                tool_call_id="call-1",
                tool_name="read_attachment",
                state="completed",
                input={"attachment_id": "bounded"},
                output={"ok": True},
            ),
            ToolCallPart(
                tool_call_id="call-2",
                tool_name="lookup",
                state="failed",
                input={},
                error="Tool failed",
            ),
            SkillPart(
                skill_id="skill-1",
                name="analysis",
                phase="activated",
                version="1",
                trust="trusted",
                affordances=("search",),
            ),
            AttachmentPart(attachment_id=uuid4(), phase="read", filename="data.txt", byte_size=2),
            WarningPart(message="Some details were omitted", code="detail_overflow"),
            StepPart(state="finished", step=1, duration_ms=8),
            UsagePart(value={}),
            TextPart(text="42"),
        ),
    )

    assert CommittedTurnCodec.decode(CommittedTurnCodec.encode(committed)) == committed


@pytest.mark.parametrize(
    "payload",
    [
        {"schema_version": 2, "parts": []},
        {"schema_version": 1, "parts": [{"type": "unknown"}]},
        {"schema_version": 1, "parts": [{"type": "text", "text": "missing usage"}]},
    ],
)
def test_committed_turn_codec_rejects_unknown_or_noncanonical_values(payload: object) -> None:
    from fleet_rlm.sessions.committed_turn import CommittedTurnCodec, CommittedTurnValidationError

    with pytest.raises(CommittedTurnValidationError):
        CommittedTurnCodec.decode(payload)
