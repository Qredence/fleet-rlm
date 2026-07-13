"""AI SDK UI 7 v1 projection for transport-neutral Fleet RuntimeEvents."""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from datetime import datetime
from typing import Any
from uuid import UUID

from fleet_rlm.rlm.events import RuntimeEvent, RuntimeEventKind

AI_SDK_UI_STREAM_HEADERS = {
    "cache-control": "no-cache",
    "connection": "keep-alive",
    "x-vercel-ai-ui-message-stream": "v1",
    "x-accel-buffering": "no",
}


def _json_default(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "value"):
        return value.value
    msg = f"Object of type {type(value).__name__} is not JSON serializable"
    raise TypeError(msg)


def _event_to_public_dict(event: RuntimeEvent) -> dict[str, Any]:
    """Retained for logs/tests; the public chat wire uses ``AISDKUIProjector``."""
    return {
        "schema_version": event.schema_version,
        "event_id": event.event_id,
        "run_id": event.run_id,
        "session_id": event.session_id,
        "sequence": event.sequence,
        "timestamp": event.timestamp,
        "kind": event.kind.value,
        "payload": dict(event.payload),
    }


class AISDKUIProjector:
    """Stateful RuntimeEvent -> AI SDK UI message chunk projector."""

    def __init__(self) -> None:
        self._text_ids: dict[UUID, str] = {}
        self._text_started: set[UUID] = set()
        self._text_ended: set[UUID] = set()

    def project(self, event: RuntimeEvent) -> list[dict[str, Any]]:
        payload = dict(event.payload)
        kind = event.kind
        if kind is RuntimeEventKind.RUN_STARTED:
            return [
                {
                    "type": "start",
                    "messageId": str(event.run_id),
                    "messageMetadata": {
                        "runId": str(event.run_id),
                        "sessionId": str(event.session_id),
                        "createdAt": event.timestamp.isoformat(),
                    },
                }
            ]
        if kind is RuntimeEventKind.STATUS:
            return [self._data("run", payload, transient=True)]
        if kind in {RuntimeEventKind.SKILL_ACTIVATED, RuntimeEventKind.SKILL_LOADED}:
            data = {**payload, "phase": "activated" if kind is RuntimeEventKind.SKILL_ACTIVATED else "loaded"}
            return [self._data("skill", data, part_id=str(payload.get("skill_id") or event.event_id))]
        if kind is RuntimeEventKind.STEP_STARTED:
            return [{"type": "start-step"}]
        if kind is RuntimeEventKind.STEP_FINISHED:
            return [{"type": "finish-step"}]
        if kind is RuntimeEventKind.RLM_REASONING:
            step = int(payload.get("step") or event.sequence)
            part_id = f"reasoning-{event.run_id}-{step}"
            return [
                {"type": "reasoning-start", "id": part_id},
                {"type": "reasoning-delta", "id": part_id, "delta": str(payload.get("text") or "")},
                {"type": "reasoning-end", "id": part_id},
            ]
        if kind is RuntimeEventKind.RLM_CODE:
            return [self._data("rlm-code", payload, part_id=self._step_id(event, payload, "code"))]
        if kind is RuntimeEventKind.RLM_OUTPUT:
            return [self._data("rlm-output", payload, part_id=self._step_id(event, payload, "output"))]
        if kind is RuntimeEventKind.TOOL_STARTED:
            return [
                {
                    "type": "tool-input-available",
                    "toolCallId": str(payload.get("tool_call_id") or event.event_id),
                    "toolName": str(payload.get("tool_name") or "tool"),
                    "input": payload.get("input"),
                    "providerExecuted": True,
                    "dynamic": True,
                }
            ]
        if kind is RuntimeEventKind.TOOL_COMPLETED:
            return [
                {
                    "type": "tool-output-available",
                    "toolCallId": str(payload.get("tool_call_id") or event.event_id),
                    "output": payload.get("output"),
                    "providerExecuted": True,
                    "dynamic": True,
                }
            ]
        if kind is RuntimeEventKind.TOOL_FAILED:
            return [
                {
                    "type": "tool-output-error",
                    "toolCallId": str(payload.get("tool_call_id") or event.event_id),
                    "errorText": str(payload.get("error") or "Tool failed"),
                    "providerExecuted": True,
                    "dynamic": True,
                }
            ]
        if kind is RuntimeEventKind.ATTACHMENT_READ:
            return [self._data("attachment", payload, part_id=str(payload.get("attachment_id") or event.event_id))]
        if kind is RuntimeEventKind.ARTIFACT_CREATED:
            return [self._data("artifact", payload, part_id=str(payload.get("artifact_id") or event.event_id))]
        if kind is RuntimeEventKind.USAGE:
            return [self._data("usage", payload, part_id=f"usage-{event.run_id}")]
        if kind is RuntimeEventKind.STRUCTURED_RESULT:
            return [self._data("structured-result", payload, part_id=f"result-{event.run_id}")]
        if kind is RuntimeEventKind.WARNING:
            return [self._data("run", {**payload, "level": "warning"}, transient=True)]
        if kind is RuntimeEventKind.TEXT_DELTA:
            text_id = self._text_ids.setdefault(event.run_id, f"text-{event.run_id}")
            chunks: list[dict[str, Any]] = []
            if event.run_id not in self._text_started:
                self._text_started.add(event.run_id)
                chunks.append({"type": "text-start", "id": text_id})
            chunks.append({"type": "text-delta", "id": text_id, "delta": str(payload.get("text") or "")})
            return chunks
        if kind is RuntimeEventKind.TEXT_COMPLETED:
            text_id = self._text_ids.setdefault(event.run_id, f"text-{event.run_id}")
            chunks = []
            if event.run_id not in self._text_started:
                self._text_started.add(event.run_id)
                chunks.append({"type": "text-start", "id": text_id})
            chunks.append({"type": "text-end", "id": text_id})
            self._text_ended.add(event.run_id)
            return chunks
        if kind is RuntimeEventKind.ERROR:
            message = str(payload.get("message") or "Turn failed")
            if str(payload.get("status") or "") == "cancelled":
                return [{"type": "abort", "reason": message}]
            return [
                {"type": "error", "errorText": message},
                {"type": "finish", "finishReason": "error"},
            ]
        if kind is RuntimeEventKind.RUN_COMPLETED:
            chunks = []
            if event.run_id in self._text_started and event.run_id not in self._text_ended:
                chunks.append({"type": "text-end", "id": self._text_ids[event.run_id]})
                self._text_ended.add(event.run_id)
            chunks.append(
                {
                    "type": "finish",
                    "finishReason": "stop",
                    "messageMetadata": {
                        "runId": str(event.run_id),
                        "sessionId": str(event.session_id),
                        "checkpointVersion": payload.get("checkpoint_version"),
                        "durationMs": payload.get("duration_ms"),
                        "idempotentReplay": bool(payload.get("idempotent_replay", False)),
                    },
                }
            )
            return chunks
        return []

    @staticmethod
    def _data(
        name: str,
        data: dict[str, Any],
        *,
        part_id: str | None = None,
        transient: bool = False,
    ) -> dict[str, Any]:
        chunk: dict[str, Any] = {"type": f"data-{name}", "data": data}
        if part_id is not None:
            chunk["id"] = part_id
        if transient:
            chunk["transient"] = True
        return chunk

    @staticmethod
    def _step_id(event: RuntimeEvent, payload: dict[str, Any], name: str) -> str:
        return f"{name}-{event.run_id}-{int(payload.get('step') or event.sequence)}"


class SSEProjector:
    """Project RuntimeEvents as AI SDK UI v1 SSE data lines."""

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
