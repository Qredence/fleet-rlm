"""Pure event-to-step mapping helpers for execution step building."""

from __future__ import annotations

from typing import Any

from .step_builder_extractors import (
    ExecutionStepType,
    _extract_tool_name,
    _tool_step_type,
)


def build_output_like_spec(
    *, kind: str, text: str, payload_obj: dict[str, Any]
) -> tuple[ExecutionStepType, str, Any, Any]:
    label = "assistant_output" if kind == "done" else kind
    return (
        "output",
        label,
        {"event_kind": kind},
        {"text": text, "payload": payload_obj},
    )


def build_simple_event_spec(
    *, kind: str, text: str, payload_obj: dict[str, Any]
) -> tuple[ExecutionStepType, str, Any, Any]:
    step_type: ExecutionStepType = "llm"
    label = text or "reasoning"

    input_payload = payload_obj
    output_payload: dict[str, Any] = {"text": text}

    return (step_type, label, input_payload, output_payload)


def build_status_spec(text: str) -> tuple[ExecutionStepType, str, Any, Any] | None:
    if not text:
        return None
    if text.startswith("Calling tool:") or text == "Tool finished.":
        return None
    return ("llm", text, {"event_kind": "status"}, {"text": text})


def build_tool_call_spec(
    *, text: str, payload_obj: dict[str, Any]
) -> tuple[ExecutionStepType, str, Any, Any, str | None]:
    tool_name = _extract_tool_name(text, payload_obj)
    return (
        _tool_step_type(tool_name),
        tool_name or text or "tool_call",
        payload_obj,
        None,
        tool_name,
    )


def build_tool_result_spec(
    *, text: str, payload_obj: dict[str, Any]
) -> tuple[ExecutionStepType, str, Any, Any, str | None]:
    tool_name = _extract_tool_name(text, payload_obj)
    return (
        _tool_step_type(tool_name),
        tool_name or text or "tool_result",
        {"event_kind": "tool_result", "tool_name": tool_name},
        payload_obj,
        tool_name,
    )
