"""Exhaustive AI SDK UI v1 projection for typed Fleet Runtime Events."""

# The live AI SDK UI transport contract is owned by typed models in
# src/fleet_rlm/api/ui_stream.py and consumed by these explicit adapters:
#   1. this module (RuntimeEvent -> typed chunks -> JSON)
#   2. src/fleet_rlm/api/ui_message.py       (durable assistant Result reload)
#   3. src/fleet_rlm/api/openapi.py          (derived OpenAPI chunk schemas)
#   4. tools/fleet-tui/src/generated/fleet-ui-chunk-validation.ts
#      (generated TUI validator tables via `make api-sync`)

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from fleet_rlm.api.json_util import to_plain_json
from fleet_rlm.api.ui_stream import fleet_ui_chunk_payload
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
_CANONICAL_REASONING_SUFFIX = ":canonical"


def _detail_data(detail: object) -> dict[str, Any]:
    """Extract all dataclass fields from a detail object as a plain dict, excluding 'kind'.

    ``kind`` is the internal discriminator field and is never forwarded to the transport.
    Mapping-typed values are converted to plain dicts so JSON serialization is safe.
    """
    fields = getattr(detail, "__dataclass_fields__", {})
    return {
        name: dict(value) if isinstance(value, Mapping) else value
        for name in fields
        if name != "kind"
        for value in (getattr(detail, name),)
    }


class AISDKUIProjector:
    """Stateful typed Runtime Event to AI SDK UI message chunk projector."""

    def __init__(self) -> None:
        self._text_ids: dict[UUID, str] = {}
        self._text_started: set[UUID] = set()
        self._text_ended: set[UUID] = set()
        self._stream_ids: dict[tuple[str, int | None], str] = {}
        self._reasoning_started: set[str] = set()
        self._reasoning_ended: set[str] = set()

    def project(self, event: RuntimeEvent) -> list[dict[str, Any]]:
        """Project one RuntimeEvent and validate every frame before SSE serialization."""
        return [fleet_ui_chunk_payload(chunk) for chunk in self._project(event)]

    def _project(self, event: RuntimeEvent) -> list[dict[str, Any]]:
        detail = event.detail
        data = _detail_data(detail)
        if isinstance(detail, RunStarted):
            return self._project_run_started(event, detail)
        if isinstance(detail, (Status, SkillActivated, SkillLoaded, StepStarted, StepFinished)):
            return self._project_progress_event(detail, data)
        if isinstance(detail, (RLMReasoning, RLMCode, RLMOutput)):
            return self._project_rlm_event(event, detail, data)
        if isinstance(detail, (ToolStarted, ToolCompleted, ToolFailed)):
            return self._project_tool_event(detail)
        if isinstance(detail, (AttachmentRead, ArtifactCreated, Usage, StructuredResult, WarningEvent)):
            return self._project_data_event(event, detail, data)
        if isinstance(detail, (TextDelta, TextCompleted)):
            return self._project_text_event(event, detail)
        if isinstance(detail, (RunFailed, RunCancelled, RunTimedOut)):
            return self._project_run_failure(detail)
        if isinstance(detail, RunCompleted):
            return self._project_run_completed(event, detail)
        raise AssertionError(f"unhandled Runtime Event detail: {type(detail).__name__}")

    @staticmethod
    def _project_run_started(event: RuntimeEvent, detail: RunStarted) -> list[dict[str, Any]]:
        metadata = {
            "schemaVersion": event.schema_version,
            "runId": str(event.run_id),
            "sessionId": str(event.session_id),
            "createdAt": event.timestamp.isoformat(),
            "delivery": detail.delivery,
        }
        if detail.trace_id:
            metadata["traceId"] = detail.trace_id
        return [
            {
                "type": "start",
                "messageId": str(event.run_id),
                "messageMetadata": metadata,
            }
        ]

    def _project_run_completed(self, event: RuntimeEvent, detail: RunCompleted) -> list[dict[str, Any]]:
        chunks: list[dict[str, Any]] = []
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
                    **({"traceId": detail.trace_id} if detail.trace_id else {}),
                },
            }
        )
        return chunks

    @staticmethod
    def _project_run_failure(detail: RunFailed | RunCancelled | RunTimedOut) -> list[dict[str, Any]]:
        if isinstance(detail, RunCancelled):
            return [{"type": "abort", "reason": detail.message}]
        return [
            {"type": "error", "errorText": detail.message},
            {"type": "finish", "finishReason": "error"},
        ]

    def _project_progress_event(
        self,
        detail: Status | SkillActivated | SkillLoaded | StepStarted | StepFinished,
        data: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if isinstance(detail, Status):
            return [self._data("status", data, transient=True)]
        if isinstance(detail, (SkillActivated, SkillLoaded)):
            phase = "activated" if isinstance(detail, SkillActivated) else "loaded"
            data["phase"] = phase
            return [self._data("skill", data, part_id=detail.skill_id)]
        if isinstance(detail, StepStarted):
            return [{"type": "start-step"}]
        return [{"type": "finish-step"}]

    def _project_rlm_event(
        self,
        event: RuntimeEvent,
        detail: RLMReasoning | RLMCode | RLMOutput,
        data: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if isinstance(detail, RLMReasoning):
            return self._project_reasoning(event, detail)
        if isinstance(detail, RLMCode):
            return [self._data("rlm-code", data, part_id=self._detail_part_id(event, detail, "code"))]
        return [self._data("rlm-output", data, part_id=self._detail_part_id(event, detail, "output"))]

    def _project_reasoning(self, event: RuntimeEvent, detail: RLMReasoning) -> list[dict[str, Any]]:
        """Project one RLMReasoning event, handling live-delta and canonical-trajectory cases.

        DSPy emits incremental ``is_delta=True`` events during execution; the completed
        trajectory correction arrives later as a single ``is_delta=False`` event.  When a
        canonical correction arrives after a live stream has already been closed, a distinct
        ``:<suffix>`` stream ID is used so strict AI SDK clients do not see a reopened part.
        Empty delta text is suppressed unless the stream is already open (``is_final`` flush).
        """
        part_id = self._detail_part_id(event, detail, "reasoning")
        chunks: list[dict[str, Any]] = []
        if detail.is_delta:
            if not detail.text and not detail.is_final:
                return []
            if not detail.text and part_id not in self._reasoning_started:
                return []
            if part_id not in self._reasoning_started:
                self._reasoning_started.add(part_id)
                chunks.append({"type": "reasoning-start", "id": part_id})
            if detail.text:
                chunks.append({"type": "reasoning-delta", "id": part_id, "delta": detail.text})
            if detail.is_final and part_id not in self._reasoning_ended:
                self._reasoning_ended.add(part_id)
                chunks.append({"type": "reasoning-end", "id": part_id})
            return chunks
        # A live DSPy callback can already have opened this stream before the
        # canonical trajectory correction arrives. Close the live stream first,
        # then use a distinct stream ID for the correction so strict clients do
        # not see a reopened AI SDK part.
        if part_id in self._reasoning_ended:
            return self._canonical_reasoning_correction(part_id, detail.text)
        if part_id in self._reasoning_started:
            self._reasoning_ended.add(part_id)
            correction = self._canonical_reasoning_correction(part_id, detail.text)
            return [{"type": "reasoning-end", "id": part_id}, *correction]
        if not detail.text.strip():
            return []
        chunks.extend(
            (
                {"type": "reasoning-start", "id": part_id},
                {"type": "reasoning-delta", "id": part_id, "delta": detail.text},
                {"type": "reasoning-end", "id": part_id},
            )
        )
        self._reasoning_started.add(part_id)
        self._reasoning_ended.add(part_id)
        return chunks

    def _canonical_reasoning_correction(self, part_id: str, text: str) -> list[dict[str, Any]]:
        if not text.strip():
            return []
        correction_id = f"{part_id}{_CANONICAL_REASONING_SUFFIX}"
        if correction_id in self._reasoning_ended:
            return []
        self._reasoning_started.add(correction_id)
        self._reasoning_ended.add(correction_id)
        return [
            {"type": "reasoning-start", "id": correction_id},
            {"type": "reasoning-delta", "id": correction_id, "delta": text},
            {"type": "reasoning-end", "id": correction_id},
        ]

    @staticmethod
    def _project_tool_event(detail: ToolStarted | ToolCompleted | ToolFailed) -> list[dict[str, Any]]:
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
        return [
            {
                "type": "tool-output-error",
                "toolCallId": detail.tool_call_id,
                "errorText": detail.error,
                "providerExecuted": True,
                "dynamic": True,
            }
        ]

    def _project_data_event(
        self,
        event: RuntimeEvent,
        detail: AttachmentRead | ArtifactCreated | Usage | StructuredResult | WarningEvent,
        data: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if isinstance(detail, AttachmentRead):
            # Keep the transport-neutral snake_case fields for existing clients while
            # matching the reload projection's public UIMessage shape.
            data.update(
                attachmentId=str(detail.attachment_id),
                phase="read",
                byteSize=detail.byte_size,
            )
            return [self._data("attachment", data, part_id=str(detail.attachment_id))]
        if isinstance(detail, ArtifactCreated):
            return [self._data("artifact", data, part_id=str(detail.artifact_id))]
        if isinstance(detail, Usage):
            return [self._data("usage", {"usage": to_plain_json(detail.value)}, part_id=f"usage-{event.run_id}")]
        if isinstance(detail, StructuredResult):
            return [self._data("structured-result", data, part_id=f"result-{event.run_id}")]
        return [self._data("warning", data)]

    def _project_text_event(self, event: RuntimeEvent, detail: TextDelta | TextCompleted) -> list[dict[str, Any]]:
        text_id = self._text_ids.setdefault(event.run_id, f"text-{event.run_id}")
        chunks: list[dict[str, Any]] = []
        if event.run_id not in self._text_started:
            self._text_started.add(event.run_id)
            chunks.append({"type": "text-start", "id": text_id})
        if isinstance(detail, TextDelta):
            chunks.append({"type": "text-delta", "id": text_id, "delta": detail.text})
            return chunks
        chunks.append({"type": "text-end", "id": text_id})
        self._text_ended.add(event.run_id)
        return chunks

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

    def _detail_part_id(self, event: RuntimeEvent, detail: RLMReasoning | RLMCode | RLMOutput, name: str) -> str:
        """Return or create a stable stream ID for a multi-step RLM event part.

        When the event carries its own ``stream_id`` (set by the DSPy callback), that ID
        is recorded and returned so subsequent delta events in the same step reuse it.
        Otherwise the ID is synthesised from the run ID and step number and cached the
        same way so all events in one step share one part ID.
        """
        key = (name, detail.step)
        if detail.stream_id:
            self._stream_ids[key] = detail.stream_id
            return detail.stream_id
        stream_id = self._stream_ids.get(key)
        if stream_id is None:
            stream_id = self._step_id(event, detail.step, name)
            self._stream_ids[key] = stream_id
        return stream_id
