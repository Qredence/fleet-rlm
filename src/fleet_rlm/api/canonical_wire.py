"""Thin adapters from each stable wire format into the canonical event schema (P24/QRE-168).

This module is the ONLY place wire-format compatibility (snake_case aliases,
camelCase aliases, wrapper shapes, synthesized stream ids) is resolved. The
canonical vocabulary downstream never re-reads a wire-shaped key.
"""

from __future__ import annotations

from typing import Any

from fleet_rlm.api.ui_stream import FleetUIMessageChunk
from fleet_rlm.events.canonical import (
    ArtifactEvent,
    AttachmentEvent,
    CanonicalEvent,
    CodePartEvent,
    OutputPartEvent,
    ReasoningPartEvent,
    SkillEvent,
    StepFinish,
    StepStart,
    StructuredResultEvent,
    TextPartEvent,
    ToolCallEvent,
    ToolResultEvent,
    TurnCancelled,
    TurnError,
    TurnFinish,
    TurnStart,
    TurnStatus,
    UsageEvent,
    WarningEvent,
)


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def canonical_from_live_chunk(chunk: FleetUIMessageChunk) -> tuple[CanonicalEvent, ...]:
    """Adapt one validated live SSE wire chunk to canonical events."""
    match chunk.type:
        case "start":
            metadata = _mapping(getattr(chunk, "message_metadata", None))
            delivery = metadata.get("delivery")
            trace_id = metadata.get("traceId") or metadata.get("trace_id")
            return (
                TurnStart(
                    run_id=str(chunk.message_id),
                    delivery=delivery if delivery in ("live", "replay") else None,
                    trace_id=trace_id if isinstance(trace_id, str) and trace_id else None,
                ),
            )
        case "start-step":
            return (StepStart(),)
        case "finish-step":
            return (StepFinish(),)
        case "reasoning-start":
            return (ReasoningPartEvent(stream_id=chunk.id, text="", final=False),)
        case "reasoning-delta":
            return (ReasoningPartEvent(stream_id=chunk.id, text=chunk.delta, final=False),)
        case "reasoning-end":
            return (ReasoningPartEvent(stream_id=chunk.id, text="", final=True),)
        case "text-start":
            return (TextPartEvent(stream_id=chunk.id, text_delta="", final=False),)
        case "text-delta":
            return (TextPartEvent(stream_id=chunk.id, text_delta=chunk.delta, final=False),)
        case "text-end":
            return (TextPartEvent(stream_id=chunk.id, text_delta="", final=True),)
        case "data-rlm-code":
            value = chunk.data
            stream_id = value.stream_id or chunk.id or "1"
            return (
                CodePartEvent(
                    stream_id=str(stream_id),
                    step=int(value.step) if value.step is not None else 0,
                    code_delta=value.code,
                    is_delta=value.is_delta is True,
                    final=value.is_final is not False,
                ),
            )
        case "data-rlm-output":
            value = chunk.data
            stream_id = value.stream_id or chunk.id or "1"
            return (
                OutputPartEvent(
                    stream_id=str(stream_id),
                    step=int(value.step) if value.step is not None else 0,
                    output_delta=value.output,
                    is_delta=value.is_delta is True,
                    final=value.is_final is not False,
                ),
            )
        case "tool-input-available":
            return (
                ToolCallEvent(
                    tool_call_id=chunk.tool_call_id,
                    tool_name=chunk.tool_name,
                    input=chunk.input,
                ),
            )
        case "tool-output-available":
            return (ToolResultEvent(tool_call_id=chunk.tool_call_id, output=chunk.output),)
        case "tool-output-error":
            return (ToolResultEvent(tool_call_id=chunk.tool_call_id, error=chunk.error_text),)
        case "data-status":
            value = chunk.data
            detail = value.message or value.status or value.detail
            return (TurnStatus(phase=value.phase, detail=detail),)
        case "data-skill":
            value = chunk.data
            return (
                SkillEvent(
                    skill_id=value.skill_id or value.skill_id_compat or chunk.id,
                    stream_id=chunk.id,
                    message_id=chunk.id,
                    phase=value.phase,
                    name=value.name,
                    version=value.version,
                    trust=value.trust,
                    affordances=value.affordances,
                ),
            )
        case "data-attachment":
            value = chunk.data
            attachment_id = value.attachment_id_compat or str(value.attachment_id)
            byte_size = value.byte_size if value.byte_size is not None else value.byte_size_compat
            return (
                AttachmentEvent(
                    attachment_id=attachment_id,
                    stream_id=chunk.id,
                    message_id=chunk.id,
                    phase=value.phase,
                    filename=value.filename,
                    byte_size=byte_size,
                ),
            )
        case "data-warning":
            value = chunk.data
            return (
                WarningEvent(
                    code=value.code or "warning", message=value.message, stream_id=chunk.id, message_id=chunk.id
                ),
            )
        case "data-artifact":
            value = chunk.data
            byte_size = value.byte_size if value.byte_size is not None else value.byte_size_compat
            return (
                ArtifactEvent(
                    artifact_id=str(value.artifact_id),
                    stream_id=chunk.id,
                    message_id=chunk.id,
                    artifact_kind=value.artifact_kind or value.kind,
                    title=value.title or value.name,
                    media_type=value.media_type or value.media_type_compat,
                    byte_size=byte_size,
                    checksum_sha256=value.checksum_sha256 or value.checksum_sha256_compat,
                ),
            )
        case "data-usage":
            value = _mapping(chunk.data.usage)
            iterations = value.get("iterations")
            duration_ms = value.get("duration_ms") or value.get("durationMs")
            return (
                UsageEvent(
                    iterations=iterations if isinstance(iterations, int) else 0,
                    duration_ms=duration_ms if isinstance(duration_ms, int) else None,
                    usage=value,
                    stream_id=chunk.id,
                    message_id=chunk.id,
                ),
            )
        case "data-structured-result":
            value = chunk.data
            return (
                StructuredResultEvent(
                    schema_id=value.schema_id or value.schema_id_compat,
                    schema_version=value.schema_version or value.schema_version_compat,
                    value=value.value,
                    stream_id=chunk.id,
                    message_id=chunk.id,
                ),
            )
        case "finish":
            metadata = _mapping(getattr(chunk, "message_metadata", None))
            duration_ms = metadata.get("durationMs", metadata.get("duration_ms"))
            checkpoint = metadata.get("checkpointVersion", metadata.get("checkpoint_version"))
            trace_id = metadata.get("traceId") or metadata.get("trace_id")
            return (
                TurnFinish(
                    finish_reason=chunk.finish_reason,
                    duration_ms=duration_ms if isinstance(duration_ms, int) else None,
                    checkpoint_version=checkpoint if isinstance(checkpoint, int) else None,
                    trace_id=trace_id if isinstance(trace_id, str) and trace_id else None,
                ),
            )
        case "abort":
            return (TurnCancelled(reason=chunk.reason),)
        case "error":
            return (TurnError(text=chunk.error_text),)
    raise AssertionError(f"unhandled live wire chunk type: {chunk.type}")


def canonical_from_reload_part(
    part: dict[str, Any],
    *,
    stream_id: str,
) -> tuple[CanonicalEvent, ...]:
    """Adapt one durable-reload part dict to canonical events.

    ``stream_id`` is synthesized by the caller (position-stable); every other
    field comes from the part itself. Wire compatibility (camelCase keys,
    usage wrappers, tool state merging) is resolved here only.
    """
    part_type = part.get("type")
    data = _mapping(part.get("data"))
    match part_type:
        case "step-start":
            return (StepStart(),)
        case "data-step":
            state = data.get("state")
            step = data.get("step")
            step_value = step if isinstance(step, int) else None
            if state == "started":
                return (StepStart(step=step_value),)
            duration = data.get("durationMs", data.get("duration_ms"))
            return (StepFinish(step=step_value, duration_ms=duration if isinstance(duration, int) else None),)
        case "reasoning":
            return (
                ReasoningPartEvent(stream_id=stream_id, message_id=stream_id, text=part.get("text") or "", final=True),
            )
        case "text":
            return (
                TextPartEvent(
                    stream_id=stream_id,
                    message_id=stream_id,
                    text_delta=part.get("text") or "",
                    final=True,
                    role="assistant",
                ),
            )
        case "data-rlm-code":
            step = data.get("step")
            return (
                CodePartEvent(
                    stream_id=stream_id,
                    step=step if isinstance(step, int) else 0,
                    code_delta=data.get("code") or "",
                    is_delta=False,
                    final=True,
                ),
            )
        case "data-rlm-output":
            step = data.get("step")
            return (
                OutputPartEvent(
                    stream_id=stream_id,
                    step=step if isinstance(step, int) else 0,
                    output_delta=data.get("output") or "",
                    is_delta=False,
                    final=True,
                ),
            )
        case "dynamic-tool":
            state = part.get("state")
            call = ToolCallEvent(
                tool_call_id=part.get("toolCallId") or part.get("tool_call_id") or stream_id,
                tool_name=part.get("toolName") or part.get("tool_name") or "tool",
                input=part.get("input"),
                message_id=stream_id,
            )
            if state == "output-available":
                return (
                    call,
                    ToolResultEvent(tool_call_id=call.tool_call_id, output=part.get("output"), message_id=stream_id),
                )
            return (
                call,
                ToolResultEvent(
                    tool_call_id=call.tool_call_id,
                    error=part.get("errorText") or part.get("error_text") or "Tool failed",
                    message_id=stream_id,
                ),
            )
        case "data-status":
            return ()
        case "data-skill":
            return (
                SkillEvent(
                    skill_id=data.get("skillId") or data.get("skill_id") or part.get("id"),
                    stream_id=stream_id,
                    message_id=stream_id,
                    phase=data.get("phase"),
                    name=data.get("name"),
                    version=data.get("version"),
                    trust=data.get("trust"),
                    affordances=list(data.get("affordances") or []) or None,
                ),
            )
        case "data-attachment":
            byte_size = data.get("byteSize", data.get("byte_size"))
            return (
                AttachmentEvent(
                    attachment_id=data.get("attachmentId") or data.get("attachment_id"),
                    stream_id=stream_id,
                    message_id=stream_id,
                    phase=data.get("phase"),
                    filename=data.get("filename"),
                    byte_size=byte_size if isinstance(byte_size, int) else None,
                ),
            )
        case "data-warning":
            return (
                WarningEvent(
                    code=data.get("code") or "warning",
                    message=data.get("message") or "",
                    stream_id=stream_id,
                    message_id=stream_id,
                ),
            )
        case "data-artifact":
            byte_size = data.get("byteSize", data.get("byte_size"))
            return (
                ArtifactEvent(
                    artifact_id=data.get("artifactId") or data.get("artifact_id"),
                    stream_id=stream_id,
                    message_id=stream_id,
                    artifact_kind=data.get("artifactKind") or data.get("artifact_kind") or data.get("kind"),
                    title=data.get("title") or data.get("name"),
                    media_type=data.get("mediaType") or data.get("media_type"),
                    byte_size=byte_size if isinstance(byte_size, int) else None,
                    checksum_sha256=data.get("checksum_sha256") or data.get("checksumSha256"),
                ),
            )
        case "data-usage":
            inner = data.get("usage")
            value = _mapping(inner) if isinstance(inner, dict) else data
            iterations = value.get("iterations")
            duration_ms = value.get("duration_ms") or value.get("durationMs")
            return (
                UsageEvent(
                    iterations=iterations if isinstance(iterations, int) else 0,
                    duration_ms=duration_ms if isinstance(duration_ms, int) else None,
                    usage=value,
                    stream_id=stream_id,
                    message_id=stream_id,
                ),
            )
        case "data-structured-result":
            return (
                StructuredResultEvent(
                    schema_id=data.get("schemaId") or data.get("schema_id"),
                    schema_version=data.get("schemaVersion") or data.get("schema_version"),
                    value=data.get("value"),
                    stream_id=stream_id,
                    message_id=stream_id,
                ),
            )
    raise AssertionError(f"unhandled reload part type: {part_type}")


__all__ = ["canonical_from_live_chunk", "canonical_from_reload_part"]
