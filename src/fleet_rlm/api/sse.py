"""Exhaustive stream projection for typed Fleet Runtime Events into SSE frames."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi.sse import ServerSentEvent

from fleet_rlm.api.json_util import to_plain_json
from fleet_rlm.api.ui_stream import fleet_ui_chunk_payload
from fleet_rlm.rlm.event_types import (
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


Chunk = dict[str, Any]


def _duration(duration_ms: int | None) -> Chunk:
    return {"durationMs": duration_ms} if duration_ms is not None else {}


def _stream_id(stream_id: str | None, step: int | None) -> str:
    return stream_id or str(step or 1)


def _run_started(event: RuntimeEvent, detail: RunStarted) -> list[Chunk]:
    return [
        {
            "type": "turn_start",
            "runId": str(event.run_id),
            "sessionId": str(event.session_id),
            "delivery": detail.delivery,
            **({"traceId": detail.trace_id} if detail.trace_id else {}),
        }
    ]


def _status(_event: RuntimeEvent, detail: Status) -> list[Chunk]:
    return [{"type": "turn_status", "phase": detail.phase, "status": detail.status, "message": detail.message}]


def _step_started(_event: RuntimeEvent, detail: StepStarted) -> list[Chunk]:
    return [{"type": "step_start", "step": detail.step}]


def _step_finished(_event: RuntimeEvent, detail: StepFinished) -> list[Chunk]:
    return [{"type": "step_finish", "step": detail.step, **_duration(detail.duration_ms)}]


def _reasoning(_event: RuntimeEvent, detail: RLMReasoning) -> list[Chunk]:
    if not detail.text.strip():
        return []
    return [
        {
            "type": "reasoning",
            "streamId": _stream_id(detail.stream_id, detail.step),
            "step": detail.step or 0,
            "text": detail.text,
            "delta": detail.text if detail.is_delta else "",
            "final": detail.is_final,
        }
    ]


def _code(_event: RuntimeEvent, detail: RLMCode) -> list[Chunk]:
    return [
        {
            "type": "code",
            "streamId": _stream_id(detail.stream_id, detail.step),
            "step": detail.step or 0,
            "code": detail.code,
            "isDelta": detail.is_delta,
            "final": detail.is_final,
        }
    ]


def _output(_event: RuntimeEvent, detail: RLMOutput) -> list[Chunk]:
    return [
        {
            "type": "output",
            "streamId": _stream_id(detail.stream_id, detail.step),
            "step": detail.step or 0,
            "output": detail.output,
            "isDelta": detail.is_delta,
            "final": detail.is_final,
        }
    ]


def _tool_started(_event: RuntimeEvent, detail: ToolStarted) -> list[Chunk]:
    return [
        {
            "type": "tool_call",
            "toolCallId": detail.tool_call_id,
            "toolName": detail.tool_name,
            "input": to_plain_json(detail.input),
        }
    ]


def _tool_completed(_event: RuntimeEvent, detail: ToolCompleted) -> list[Chunk]:
    return [
        {
            "type": "tool_result",
            "toolCallId": detail.tool_call_id,
            "toolName": detail.tool_name,
            "output": to_plain_json(detail.output),
        }
    ]


def _tool_failed(_event: RuntimeEvent, detail: ToolFailed) -> list[Chunk]:
    return [
        {
            "type": "tool_result",
            "toolCallId": detail.tool_call_id,
            "toolName": detail.tool_name,
            "error": detail.error,
        }
    ]


def _text_delta(_event: RuntimeEvent, detail: TextDelta) -> list[Chunk]:
    return [{"type": "text", "streamId": "text", "delta": detail.text, "final": False, "role": "assistant"}]


def _text_completed(_event: RuntimeEvent, detail: TextCompleted) -> list[Chunk]:
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


def _skill_activated(_event: RuntimeEvent, detail: SkillActivated) -> list[Chunk]:
    return [
        {
            "type": "skill",
            "skillId": detail.skill_id,
            "name": detail.name,
            "phase": "activated",
            "version": detail.version,
            "trust": detail.trust,
            "affordances": list(detail.affordances),
        }
    ]


def _skill_loaded(_event: RuntimeEvent, detail: SkillLoaded) -> list[Chunk]:
    return [
        {
            "type": "skill",
            "skillId": detail.skill_id,
            "name": detail.name,
            "phase": "loaded",
            "version": detail.version,
        }
    ]


def _child_progress(_event: RuntimeEvent, detail: ChildProgress) -> list[Chunk]:
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


def _attachment_read(_event: RuntimeEvent, detail: AttachmentRead) -> list[Chunk]:
    return [
        {
            "type": "attachment",
            "attachmentId": str(detail.attachment_id),
            "phase": "read",
            "filename": detail.filename,
            "byteSize": detail.byte_size,
        }
    ]


def _artifact_created(_event: RuntimeEvent, detail: ArtifactCreated) -> list[Chunk]:
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


def _usage(_event: RuntimeEvent, detail: Usage) -> list[Chunk]:
    return [
        {
            "type": "usage",
            "iterations": detail.value.get("iterations", 0),
            **_duration(detail.value.get("duration_ms")),
            "usage": to_plain_json(detail.value),
        }
    ]


def _structured_result(_event: RuntimeEvent, detail: StructuredResult) -> list[Chunk]:
    return [
        {
            "type": "structured_result",
            "schemaId": detail.schema_id,
            "schemaVersion": detail.schema_version,
            "value": to_plain_json(detail.value),
        }
    ]


def _warning(_event: RuntimeEvent, detail: WarningEvent) -> list[Chunk]:
    return [{"type": "warning", "message": detail.message, **({"code": detail.code} if detail.code else {})}]


def _run_completed(_event: RuntimeEvent, detail: RunCompleted) -> list[Chunk]:
    return [
        {
            "type": "turn_finish",
            "finishReason": "stop",
            "status": "completed",
            "checkpointVersion": detail.checkpoint_version,
            **_duration(detail.duration_ms),
            **({"traceId": detail.trace_id} if detail.trace_id else {}),
        }
    ]


def _run_cancelled(_event: RuntimeEvent, detail: RunCancelled) -> list[Chunk]:
    return [{"type": "turn_cancelled", "reason": detail.message, **_duration(detail.duration_ms)}]


def _run_failed(_event: RuntimeEvent, detail: RunFailed) -> list[Chunk]:
    duration = _duration(detail.duration_ms)
    return [
        {"type": "turn_error", "message": detail.message, "code": detail.code, **duration},
        {"type": "turn_finish", "finishReason": "error", "status": "error", **duration},
    ]


def _run_timed_out(_event: RuntimeEvent, detail: RunTimedOut) -> list[Chunk]:
    duration = _duration(detail.duration_ms)
    return [
        {"type": "turn_error", "message": detail.message, "code": "timeout", **duration},
        {"type": "turn_finish", "finishReason": "timeout", "status": "error", **duration},
    ]


_PROJECTORS: dict[type, Callable[[RuntimeEvent, Any], list[Chunk]]] = {
    RunStarted: _run_started,
    Status: _status,
    StepStarted: _step_started,
    StepFinished: _step_finished,
    RLMReasoning: _reasoning,
    RLMCode: _code,
    RLMOutput: _output,
    ToolStarted: _tool_started,
    ToolCompleted: _tool_completed,
    ToolFailed: _tool_failed,
    TextDelta: _text_delta,
    TextCompleted: _text_completed,
    SkillActivated: _skill_activated,
    SkillLoaded: _skill_loaded,
    ChildProgress: _child_progress,
    AttachmentRead: _attachment_read,
    ArtifactCreated: _artifact_created,
    Usage: _usage,
    StructuredResult: _structured_result,
    WarningEvent: _warning,
    RunCompleted: _run_completed,
    RunCancelled: _run_cancelled,
    RunFailed: _run_failed,
    RunTimedOut: _run_timed_out,
}


def project_runtime_event(event: RuntimeEvent) -> list[Chunk]:
    """Project one domain RuntimeEvent into typed stream event payload dicts."""
    detail = event.detail
    projector = _PROJECTORS.get(type(detail))
    if projector is None:
        raise AssertionError(f"unhandled Runtime Event detail: {type(detail).__name__}")
    return projector(event, detail)


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
    "AISDKUIProjector",
    "project_runtime_event",
]
