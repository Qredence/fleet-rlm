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
    label = "assistant_output" if kind == "final" else kind
    return (
        "output",
        label,
        {"event_kind": kind},
        {"text": text, "payload": payload_obj},
    )


def build_simple_event_spec(
    *, kind: str, text: str, payload_obj: dict[str, Any]
) -> tuple[ExecutionStepType, str, Any, Any]:
    step_type: ExecutionStepType
    label: str
    if kind == "reasoning_step":
        step_type = "llm"
        label = text or "reasoning"
    elif kind == "plan_update":
        step_type = "llm"
        label = "plan_update"
    elif kind == "rlm_executing":
        step_type = "repl"
        label = "rlm_executing"
    else:
        step_type = "memory"
        label = "memory_update"

    input_payload = payload_obj if kind == "reasoning_step" else {"event_kind": kind}
    output_payload: dict[str, Any] = {"text": text}
    if kind != "reasoning_step":
        output_payload["payload"] = payload_obj

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


def build_trajectory_spec(
    *, text: str, payload_obj: dict[str, Any]
) -> tuple[ExecutionStepType, str, Any, Any]:
    step_data = payload_obj.get("step_data")
    step_dict = step_data if isinstance(step_data, dict) else {}
    tool_name = _extract_tool_name(text, step_dict)
    label = (
        tool_name
        or str(step_dict.get("thought", "")).strip()
        or str(step_dict.get("action", "")).strip()
        or text
        or "trajectory_step"
    )
    return (
        _tool_step_type(tool_name) if tool_name else "llm",
        label,
        step_dict.get("input", step_dict),
        step_dict.get("output", step_dict.get("observation")),
    )
