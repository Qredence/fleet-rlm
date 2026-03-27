"""Status parsing and tool/HITL event helpers for streamed chat turns."""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from dspy.streaming.messages import StatusMessageProvider

from fleet_rlm.runtime.models.streaming import StreamEvent

ToolEventKind = Literal["tool_call", "plan_update", "rlm_executing", "memory_update"]


def parse_tool_call_status(message: str) -> str | None:
    match = re.match(r"^Calling tool:\s*(.+)$", message.strip())
    if not match:
        return None
    return f"tool call: {match.group(1).strip()}"


def parse_tool_call_payload(message: str) -> dict[str, Any] | None:
    match = re.match(r"^Calling tool:\s*(.+)$", message.strip())
    if not match:
        return None

    raw_call = match.group(1).strip()
    tool_name = raw_call.split("(", 1)[0].strip() if raw_call else ""
    args_snippet = ""
    if "(" in raw_call:
        args_snippet = raw_call.split("(", 1)[1].rsplit(")", 1)[0].strip()

    payload: dict[str, Any] = {"raw_status": message, "raw_call": raw_call}
    if tool_name:
        payload["tool_name"] = tool_name
    if args_snippet:
        payload["tool_args"] = args_snippet
        payload["tool_input"] = args_snippet
    return payload


def parse_tool_result_status(message: str) -> str | None:
    stripped = message.strip()
    if stripped == "Tool finished.":
        return "tool result: finished"
    if stripped.startswith("Tool result:"):
        return "tool result: completed"
    return None


def parse_tool_result_payload(
    message: str, *, tool_name: str | None
) -> dict[str, Any] | None:
    stripped = message.strip()
    if stripped != "Tool finished." and not stripped.startswith("Tool result:"):
        return None

    payload: dict[str, Any] = {"raw_status": message}
    if tool_name:
        payload["tool_name"] = tool_name
    if stripped.startswith("Tool result:"):
        result_text = stripped.removeprefix("Tool result:").strip()
        if result_text:
            payload["tool_output"] = result_text
    return payload


class ReActStatusProvider(StatusMessageProvider):
    """Concise status messaging for streamed ReAct sessions."""

    def tool_start_status_message(self, instance: Any, inputs: dict[str, Any]):
        return f"Calling tool: {instance.name}"

    def tool_end_status_message(self, outputs: Any):
        return "Tool finished."

    def module_start_status_message(self, instance: Any, inputs: dict[str, Any]):
        return f"Running module: {instance.__class__.__name__}"

    def module_end_status_message(self, outputs: Any):
        return None


def try_parse_hitl_request(
    tool_name: str | None,
    payload: dict[str, Any],
) -> StreamEvent | None:
    if not tool_name:
        return None

    output = payload.get("tool_output")
    if not isinstance(output, str):
        return None

    data = None
    if output.startswith("{") and output.endswith("}"):
        try:
            data = json.loads(output)
        except (json.JSONDecodeError, TypeError, ValueError):
            data = None

    if tool_name == "clarification_questions":
        questions = []
        if data and isinstance(data, dict):
            questions = data.get("questions", [])
        if questions:
            return StreamEvent(
                kind="hitl_request",
                text="The agent has some questions for you.",
                payload={
                    "options": questions,
                    "source": "clarification_questions",
                    "requires_response": True,
                },
            )

    if tool_name == "memory_action_intent":
        if data and isinstance(data, dict) and data.get("requires_confirmation"):
            return StreamEvent(
                kind="hitl_request",
                text="This memory action requires confirmation.",
                payload={
                    "action": data.get("intent"),
                    "source": "memory_action_intent",
                    "requires_response": True,
                },
            )

    return None


def classify_tool_event_kind(tool_name: str | None) -> ToolEventKind:
    if tool_name == "plan_code_change":
        return "plan_update"
    if tool_name in {
        "rlm_query",
        "rlm_query_batched",
        "analyze_long_document",
        "summarize_long_document",
        "extract_from_logs",
        "grounded_answer",
        "triage_incident_logs",
        "parallel_semantic_map",
    }:
        return "rlm_executing"
    if tool_name and (tool_name.startswith("core_memory") or "memory" in tool_name):
        return "memory_update"
    return "tool_call"


__all__ = [
    "ReActStatusProvider",
    "ToolEventKind",
    "classify_tool_event_kind",
    "parse_tool_call_payload",
    "parse_tool_call_status",
    "parse_tool_result_payload",
    "parse_tool_result_status",
    "try_parse_hitl_request",
]
