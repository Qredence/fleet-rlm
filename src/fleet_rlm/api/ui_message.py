"""Exhaustive canonical Turn to deterministic AI SDK UIMessage projection."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, assert_never

from fleet_rlm.sessions.committed_turn import (
    ArtifactPart,
    AttachmentPart,
    CodePart,
    CommittedPart,
    OutputPart,
    ReasoningPart,
    SkillPart,
    StepPart,
    StructuredResultPart,
    TextPart,
    ToolCallPart,
    UsagePart,
    WarningPart,
)
from fleet_rlm.sessions.models import AssistantTurnRecord, UserTurnRecord


def _json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_value(item) for item in value]
    return value


def _assistant_part(part: CommittedPart) -> dict[str, Any]:
    if isinstance(part, StepPart):
        return {
            "type": "data-step",
            "data": {"state": part.state, "step": part.step, "durationMs": part.duration_ms},
        }
    if isinstance(part, ReasoningPart):
        return {"type": "reasoning", "text": part.text, "state": "done"}
    if isinstance(part, CodePart):
        return {"type": "data-rlm-code", "data": {"code": part.code, "step": part.step}}
    if isinstance(part, OutputPart):
        return {"type": "data-rlm-output", "data": {"output": part.output, "step": part.step}}
    if isinstance(part, ToolCallPart):
        value: dict[str, Any] = {
            "type": "dynamic-tool",
            "toolName": part.tool_name,
            "toolCallId": part.tool_call_id,
            "input": _json_value(part.input),
            "providerExecuted": True,
        }
        if part.state == "completed":
            value.update(state="output-available", output=_json_value(part.output))
        else:
            value.update(state="output-error", errorText=part.error)
        return value
    if isinstance(part, SkillPart):
        return {
            "type": "data-skill",
            "id": part.skill_id,
            "data": {
                "skillId": part.skill_id,
                "name": part.name,
                "phase": part.phase,
                "version": part.version,
                "trust": part.trust,
                "affordances": list(part.affordances),
            },
        }
    if isinstance(part, AttachmentPart):
        return {
            "type": "data-attachment",
            "data": {
                "attachmentId": str(part.attachment_id),
                "phase": part.phase,
                "filename": part.filename,
                "byteSize": part.byte_size,
            },
        }
    if isinstance(part, WarningPart):
        return {"type": "data-warning", "data": {"message": part.message, "code": part.code}}
    if isinstance(part, ArtifactPart):
        return {
            "type": "data-artifact",
            "data": {
                "artifactId": str(part.artifact_id),
                "kind": part.kind,
                "title": part.title,
                "mediaType": part.media_type,
                "byteSize": part.byte_size,
                "checksumSha256": part.checksum_sha256,
            },
        }
    if isinstance(part, UsagePart):
        return {"type": "data-usage", "data": _json_value(part.value)}
    if isinstance(part, StructuredResultPart):
        return {
            "type": "data-structured-result",
            "data": {
                "schemaId": part.schema_id,
                "schemaVersion": part.schema_version,
                "value": _json_value(part.value),
            },
        }
    if isinstance(part, TextPart):
        return {"type": "text", "text": part.text, "state": "done"}
    assert_never(part)


def user_turn_to_ui_message(record: UserTurnRecord) -> dict[str, Any]:
    parts: list[dict[str, Any]] = [{"type": "text", "text": record.input.text, "state": "done"}]
    parts.extend(
        {
            "type": "data-attachment",
            "data": {"attachmentId": str(attachment_id), "phase": "selected"},
        }
        for attachment_id in record.input.attachment_ids
    )
    return {
        "id": str(record.id),
        "role": "user",
        "parts": parts,
        "metadata": {
            "schemaVersion": 1,
            "sessionId": str(record.session_id),
            "sequence": record.sequence,
            "attachmentIds": [str(value) for value in record.input.attachment_ids],
        },
    }


def assistant_turn_to_ui_message(record: AssistantTurnRecord) -> dict[str, Any]:
    return {
        "id": str(record.run_id),
        "role": "assistant",
        "parts": [_assistant_part(part) for part in record.committed.parts],
        "metadata": {
            "schemaVersion": 1,
            "runId": str(record.run_id),
            "sessionId": str(record.session_id),
            "sequence": record.sequence,
        },
    }
