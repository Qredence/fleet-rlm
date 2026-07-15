"""Exhaustive AI SDK UI v1 projection for typed Fleet Runtime Events."""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Mapping
from datetime import datetime
from typing import Any
from uuid import UUID

from fleet_rlm.rlm.events import (
    ArtifactCreated,
    AttachmentRead,
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

AI_SDK_UI_STREAM_HEADERS = {
    "cache-control": "no-cache",
    "connection": "keep-alive",
    "x-vercel-ai-ui-message-stream": "v1",
    "x-accel-buffering": "no",
}

FLEET_UI_CHUNK_TYPES = (
    "start",
    "start-step",
    "finish-step",
    "reasoning-start",
    "reasoning-delta",
    "reasoning-end",
    "data-status",
    "data-skill",
    "data-rlm-code",
    "data-rlm-output",
    "tool-input-available",
    "tool-output-available",
    "tool-output-error",
    "data-attachment",
    "data-warning",
    "data-artifact",
    "data-usage",
    "data-structured-result",
    "text-start",
    "text-delta",
    "text-end",
    "finish",
    "abort",
    "error",
)


def _json_default(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _detail_data(detail: object) -> dict[str, Any]:
    fields = getattr(detail, "__dataclass_fields__", {})
    return {
        name: dict(value) if isinstance(value, Mapping) else value
        for name in fields
        if name != "kind"
        for value in (getattr(detail, name),)
    }


def _json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    return value


def _event_to_public_dict(event: RuntimeEvent) -> dict[str, Any]:
    return {
        "schema_version": event.schema_version,
        "event_id": event.event_id,
        "run_id": event.run_id,
        "session_id": event.session_id,
        "sequence": event.sequence,
        "timestamp": event.timestamp,
        "kind": event.kind,
        "payload": _detail_data(event.detail),
    }


class AISDKUIProjector:
    """Stateful typed Runtime Event to AI SDK UI message chunk projector."""

    def __init__(self) -> None:
        self._text_ids: dict[UUID, str] = {}
        self._text_started: set[UUID] = set()
        self._text_ended: set[UUID] = set()

    def project(self, event: RuntimeEvent) -> list[dict[str, Any]]:
        detail = event.detail
        data = _detail_data(detail)
        if isinstance(detail, RunStarted):
            return [
                {
                    "type": "start",
                    "messageId": str(event.run_id),
                    "messageMetadata": {
                        "schemaVersion": event.schema_version,
                        "runId": str(event.run_id),
                        "sessionId": str(event.session_id),
                        "createdAt": event.timestamp.isoformat(),
                        "delivery": detail.delivery,
                    },
                }
            ]
        if isinstance(detail, Status):
            return [self._data("status", data, transient=True)]
        if isinstance(detail, (SkillActivated, SkillLoaded)):
            return [self._data("skill", data, part_id=detail.skill_id)]
        if isinstance(detail, StepStarted):
            return [{"type": "start-step"}]
        if isinstance(detail, StepFinished):
            return [{"type": "finish-step"}]
        if isinstance(detail, RLMReasoning):
            if not detail.text.strip():
                return []
            part_id = self._step_id(event, detail.step, "reasoning")
            return [
                {"type": "reasoning-start", "id": part_id},
                {"type": "reasoning-delta", "id": part_id, "delta": detail.text},
                {"type": "reasoning-end", "id": part_id},
            ]
        if isinstance(detail, RLMCode):
            return [self._data("rlm-code", data, part_id=self._step_id(event, detail.step, "code"))]
        if isinstance(detail, RLMOutput):
            return [self._data("rlm-output", data, part_id=self._step_id(event, detail.step, "output"))]
        if isinstance(detail, ToolStarted):
            return [
                {
                    "type": "tool-input-available",
                    "toolCallId": detail.tool_call_id,
                    "toolName": detail.tool_name,
                    "input": detail.input,
                    "providerExecuted": True,
                    "dynamic": True,
                }
            ]
        if isinstance(detail, ToolCompleted):
            return [
                {
                    "type": "tool-output-available",
                    "toolCallId": detail.tool_call_id,
                    "output": detail.output,
                    "providerExecuted": True,
                    "dynamic": True,
                }
            ]
        if isinstance(detail, ToolFailed):
            return [
                {
                    "type": "tool-output-error",
                    "toolCallId": detail.tool_call_id,
                    "errorText": detail.error,
                    "providerExecuted": True,
                    "dynamic": True,
                }
            ]
        if isinstance(detail, AttachmentRead):
            return [self._data("attachment", data, part_id=str(detail.attachment_id))]
        if isinstance(detail, ArtifactCreated):
            return [self._data("artifact", data, part_id=str(detail.artifact_id))]
        if isinstance(detail, Usage):
            return [self._data("usage", {"usage": _json_value(detail.value)}, part_id=f"usage-{event.run_id}")]
        if isinstance(detail, StructuredResult):
            return [self._data("structured-result", data, part_id=f"result-{event.run_id}")]
        if isinstance(detail, WarningEvent):
            return [self._data("warning", data)]
        if isinstance(detail, TextDelta):
            text_id = self._text_ids.setdefault(event.run_id, f"text-{event.run_id}")
            chunks: list[dict[str, Any]] = []
            if event.run_id not in self._text_started:
                self._text_started.add(event.run_id)
                chunks.append({"type": "text-start", "id": text_id})
            chunks.append({"type": "text-delta", "id": text_id, "delta": detail.text})
            return chunks
        if isinstance(detail, TextCompleted):
            text_id = self._text_ids.setdefault(event.run_id, f"text-{event.run_id}")
            chunks = []
            if event.run_id not in self._text_started:
                self._text_started.add(event.run_id)
                chunks.append({"type": "text-start", "id": text_id})
            chunks.append({"type": "text-end", "id": text_id})
            self._text_ended.add(event.run_id)
            return chunks
        if isinstance(detail, (RunFailed, RunCancelled, RunTimedOut)):
            if isinstance(detail, RunCancelled):
                return [{"type": "abort", "reason": detail.message}]
            return [
                {"type": "error", "errorText": detail.message},
                {"type": "finish", "finishReason": "error"},
            ]
        if isinstance(detail, RunCompleted):
            chunks = []
            if event.run_id in self._text_started and event.run_id not in self._text_ended:
                chunks.append({"type": "text-end", "id": self._text_ids[event.run_id]})
                self._text_ended.add(event.run_id)
            chunks.append(
                {
                    "type": "finish",
                    "finishReason": "stop",
                    "messageMetadata": {
                        "schemaVersion": event.schema_version,
                        "runId": str(event.run_id),
                        "sessionId": str(event.session_id),
                        "checkpointVersion": detail.checkpoint_version,
                        "durationMs": detail.duration_ms,
                        "idempotentReplay": detail.delivery == "replay",
                    },
                }
            )
            return chunks
        raise AssertionError(f"unhandled Runtime Event detail: {type(detail).__name__}")

    @staticmethod
    def _data(
        name: str, data: dict[str, Any], *, part_id: str | None = None, transient: bool = False
    ) -> dict[str, Any]:
        chunk: dict[str, Any] = {"type": f"data-{name}", "data": data}
        if part_id is not None:
            chunk["id"] = part_id
        if transient:
            chunk["transient"] = True
        return chunk

    @staticmethod
    def _step_id(event: RuntimeEvent, step: int | None, name: str) -> str:
        return f"{name}-{event.run_id}-{step or event.sequence}"


class SSEProjector:
    def __init__(self) -> None:
        self._projector = AISDKUIProjector()

    def project(self, events: Iterable[RuntimeEvent]) -> Iterator[str]:
        for event in events:
            for chunk in self._projector.project(event):
                yield f"data: {json.dumps(chunk, default=_json_default)}\n\n"

    def done(self) -> str:
        return "data: [DONE]\n\n"

    def keepalive(self) -> str:
        return ": keepalive\n\n"
