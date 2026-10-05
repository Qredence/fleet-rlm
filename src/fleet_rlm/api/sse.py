"""Exhaustive stream projection for typed Fleet Runtime Events into SSE frames."""

from __future__ import annotations

from typing import Any

from fastapi.sse import ServerSentEvent

from fleet_rlm.api.json_util import to_plain_json
from fleet_rlm.api.ui_stream import fleet_ui_chunk_payload
from fleet_rlm.rlm.events import (
    ArtifactCreated,
    AttachmentRead,
    ChildProgress,
    RLMCode,
    RLMOutput,
    RLMReasoning,
    RunCancelled,
    RunCompleted,
    RunFailed,
    RunStarted,
    RunTimedOut,
    RuntimeEvent,
    SkillActivated,
    SkillLoaded,
    Status,
    StepFinished,
    StepStarted,
    StructuredResult,
    TextCompleted,
    TextDelta,
    ToolCompleted,
    ToolFailed,
    ToolStarted,
    Usage,
    WarningEvent,
)

FLEET_STREAM_EVENT_TYPES = (
    "turn_start",
    "turn_status",
    "step_start",
    "step_finish",
    "reasoning",
    "code",
    "output",
    "tool_call",
    "tool_result",
    "text",
    "skill",
    "child_progress",
    "attachment",
    "warning",
    "artifact",
    "usage",
    "structured_result",
    "turn_finish",
    "turn_cancelled",
    "turn_error",
)
FLEET_UI_CHUNK_TYPES = FLEET_STREAM_EVENT_TYPES


def project_runtime_event(event: RuntimeEvent) -> list[dict[str, Any]]:
    """Project one domain RuntimeEvent into typed stream event payload dicts."""
    detail = event.detail
    if isinstance(detail, RunStarted):
        return [
            {
                "type": "turn_start",
                "runId": str(event.run_id),
                "sessionId": str(event.session_id),
                "delivery": detail.delivery,
                **({"traceId": detail.trace_id} if detail.trace_id else {}),
            }
        ]
    if isinstance(detail, Status):
        return [
            {
                "type": "turn_status",
                "phase": detail.phase,
                "status": detail.status,
                "message": detail.message,
            }
        ]
    if isinstance(detail, StepStarted):
        return [{"type": "step_start", "step": detail.step}]
    if isinstance(detail, StepFinished):
        return [
            {
                "type": "step_finish",
                "step": detail.step,
                **({"durationMs": detail.duration_ms} if detail.duration_ms is not None else {}),
            }
        ]
    if isinstance(detail, RLMReasoning):
        if not detail.text.strip():
            return []
        return [
            {
                "type": "reasoning",
                "streamId": detail.stream_id or str(detail.step or 1),
                "step": detail.step or 0,
                "text": detail.text,
                "delta": detail.text if detail.is_delta else "",
                "final": detail.is_final,
            }
        ]
    if isinstance(detail, RLMCode):
        return [
            {
                "type": "code",
                "streamId": detail.stream_id or str(detail.step or 1),
                "step": detail.step or 0,
                "code": detail.code,
                "isDelta": detail.is_delta,
                "final": detail.is_final,
            }
        ]
    if isinstance(detail, RLMOutput):
        return [
            {
                "type": "output",
                "streamId": detail.stream_id or str(detail.step or 1),
                "step": detail.step or 0,
                "output": detail.output,
                "isDelta": detail.is_delta,
                "final": detail.is_final,
            }
        ]
    if isinstance(detail, ToolStarted):
        return [
            {
                "type": "tool_call",
                "toolCallId": detail.tool_call_id,
                "toolName": detail.tool_name,
                "input": to_plain_json(detail.input),
            }
        ]
    if isinstance(detail, ToolCompleted):
        return [
            {
                "type": "tool_result",
                "toolCallId": detail.tool_call_id,
                "toolName": detail.tool_name,
                "output": to_plain_json(detail.output),
            }
        ]
    if isinstance(detail, ToolFailed):
        return [
            {
                "type": "tool_result",
                "toolCallId": detail.tool_call_id,
                "toolName": detail.tool_name,
                "error": detail.error,
            }
        ]
    if isinstance(detail, TextDelta):
        return [
            {
                "type": "text",
                "streamId": "text",
                "delta": detail.text,
                "final": False,
                "role": "assistant",
            }
        ]
    if isinstance(detail, TextCompleted):
        return [
            {
                "type": "text",
                "streamId": "text",
                "delta": "",
                "text": detail.text,
                "final": True,
                "role": "assistant",
            }
        ]
    if isinstance(detail, (SkillActivated, SkillLoaded)):
        phase = "activated" if isinstance(detail, SkillActivated) else "loaded"
        payload: dict[str, Any] = {
            "type": "skill",
            "skillId": detail.skill_id,
            "name": detail.name,
            "phase": phase,
            "version": detail.version,
        }
        if isinstance(detail, SkillActivated):
            payload["trust"] = detail.trust
            payload["affordances"] = list(detail.affordances)
        return [payload]
    if isinstance(detail, ChildProgress):
        return [
            {
                "type": "child_progress",
                "childId": detail.child_id,
                "taskLabel": detail.task_label,
                "state": detail.state,
                "elapsedMs": detail.elapsed_ms,
                "outcome": detail.outcome,
                "cleanupState": detail.cleanup_state,
                "parentRunId": detail.parent_run_id,
                "evidence": list(detail.evidence),
                "gaps": list(detail.gaps),
                "resultFileCount": detail.result_file_count,
                "codeExcerpt": detail.code_excerpt,
                "outputExcerpt": detail.output_excerpt,
            }
        ]
    if isinstance(detail, AttachmentRead):
        return [
            {
                "type": "attachment",
                "attachmentId": str(detail.attachment_id),
                "phase": "read",
                "filename": detail.filename,
                "byteSize": detail.byte_size,
            }
        ]
    if isinstance(detail, ArtifactCreated):
        return [
            {
                "type": "artifact",
                "artifactId": str(detail.artifact_id),
                "artifactKind": detail.artifact_kind,
                "title": detail.title,
                "mediaType": detail.media_type,
                "byteSize": detail.byte_size,
                "checksumSha256": detail.checksum_sha256,
            }
        ]
    if isinstance(detail, Usage):
        return [
            {
                "type": "usage",
                "iterations": detail.value.get("iterations", 0),
                **(
                    {"durationMs": detail.value.get("duration_ms")}
                    if detail.value.get("duration_ms") is not None
                    else {}
                ),
                "usage": to_plain_json(detail.value),
            }
        ]
    if isinstance(detail, StructuredResult):
        return [
            {
                "type": "structured_result",
                "schemaId": detail.schema_id,
                "schemaVersion": detail.schema_version,
                "value": to_plain_json(detail.value),
            }
        ]
    if isinstance(detail, WarningEvent):
        return [
            {
                "type": "warning",
                "message": detail.message,
                **({"code": detail.code} if detail.code else {}),
            }
        ]
    if isinstance(detail, RunCompleted):
        return [
            {
                "type": "turn_finish",
                "finishReason": "stop",
                "status": "completed",
                "checkpointVersion": detail.checkpoint_version,
                **({"durationMs": detail.duration_ms} if detail.duration_ms is not None else {}),
                **({"traceId": detail.trace_id} if detail.trace_id else {}),
            }
        ]
    if isinstance(detail, RunCancelled):
        return [
            {
                "type": "turn_cancelled",
                "reason": detail.message,
                **({"durationMs": detail.duration_ms} if detail.duration_ms is not None else {}),
            }
        ]
    if isinstance(detail, RunFailed):
        return [
            {
                "type": "turn_error",
                "message": detail.message,
                "code": detail.code,
                **({"durationMs": detail.duration_ms} if detail.duration_ms is not None else {}),
            },
            {
                "type": "turn_finish",
                "finishReason": "error",
                "status": "error",
                **({"durationMs": detail.duration_ms} if detail.duration_ms is not None else {}),
            },
        ]
    if isinstance(detail, RunTimedOut):
        return [
            {
                "type": "turn_error",
                "message": detail.message,
                "code": "timeout",
                **({"durationMs": detail.duration_ms} if detail.duration_ms is not None else {}),
            },
            {
                "type": "turn_finish",
                "finishReason": "timeout",
                "status": "error",
                **({"durationMs": detail.duration_ms} if detail.duration_ms is not None else {}),
            },
        ]
    raise AssertionError(f"unhandled Runtime Event detail: {type(detail).__name__}")


class AISDKUIProjector:
    """Projector mapping RuntimeEvent to typed stream chunk payloads and SSE frames."""

    def project(self, event: RuntimeEvent) -> list[dict[str, Any]]:
        """Project one RuntimeEvent into validated chunk payload dictionaries."""
        return [fleet_ui_chunk_payload(chunk) for chunk in project_runtime_event(event)]

    def project_sse(self, event: RuntimeEvent) -> list[ServerSentEvent]:
        """Project one RuntimeEvent into native FastAPI ServerSentEvent instances."""
        chunks = self.project(event)
        return [ServerSentEvent(event=chunk["type"], data=chunk) for chunk in chunks]


__all__ = [
    "FLEET_STREAM_EVENT_TYPES",
    "FLEET_UI_CHUNK_TYPES",
    "AISDKUIProjector",
    "project_runtime_event",
]
