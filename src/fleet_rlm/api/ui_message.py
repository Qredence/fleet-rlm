"""Deterministic conversion of committed Turn details to AI SDK UIMessage parts."""

from __future__ import annotations

from typing import Any


def detail_parts_to_ui_parts(
    details: tuple[dict[str, Any], ...],
    *,
    answer_text: str,
    structured_result: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Build durable AI SDK UI 7 parts without replaying transport chunks."""
    parts: list[dict[str, Any]] = []
    tool_parts: dict[str, dict[str, Any]] = {}
    tool_indexes: dict[str, int] = {}
    saw_text = False
    saw_structured = False

    for detail in details:
        kind = str(detail.get("kind") or "")
        payload = detail.get("payload")
        data = dict(payload) if isinstance(payload, dict) else {}
        if kind == "step.started":
            parts.append({"type": "step-start"})
        elif kind == "rlm.reasoning":
            parts.append({"type": "reasoning", "text": str(data.get("text") or ""), "state": "done"})
        elif kind == "rlm.code":
            parts.append({"type": "data-rlm-code", "data": data})
        elif kind == "rlm.output":
            parts.append({"type": "data-rlm-output", "data": data})
        elif kind in {"skill.activated", "skill.loaded"}:
            parts.append({"type": "data-skill", "id": str(data.get("skill_id") or ""), "data": data})
        elif kind in {"attachment.read", "artifact.created", "usage", "warning"}:
            name = {
                "attachment.read": "attachment",
                "artifact.created": "artifact",
                "usage": "usage",
                "warning": "run",
            }[kind]
            parts.append({"type": f"data-{name}", "data": data})
        elif kind == "structured.result":
            saw_structured = True
            parts.append({"type": "data-structured-result", "data": data})
        elif kind == "tool.started":
            call_id = str(data.get("tool_call_id") or "")
            part = {
                "type": "dynamic-tool",
                "toolName": str(data.get("tool_name") or "tool"),
                "toolCallId": call_id,
                "state": "input-available",
                "input": data.get("input"),
                "providerExecuted": True,
            }
            tool_indexes[call_id] = len(parts)
            tool_parts[call_id] = part
            parts.append(part)
        elif kind in {"tool.completed", "tool.failed"}:
            call_id = str(data.get("tool_call_id") or "")
            part = tool_parts.get(call_id)
            if part is None:
                part = {
                    "type": "dynamic-tool",
                    "toolName": str(data.get("tool_name") or "tool"),
                    "toolCallId": call_id,
                    "input": None,
                    "providerExecuted": True,
                }
                tool_indexes[call_id] = len(parts)
                parts.append(part)
            if kind == "tool.completed":
                part.update(state="output-available", output=data.get("output"))
            else:
                part.update(state="output-error", errorText=str(data.get("error") or "Tool failed"))
            parts[tool_indexes[call_id]] = part
        elif kind == "text.delta":
            saw_text = True
            text = str(data.get("text") or "")
            if parts and parts[-1].get("type") == "text":
                parts[-1]["text"] = str(parts[-1].get("text") or "") + text
            else:
                parts.append({"type": "text", "text": text, "state": "done"})

    if structured_result is not None and not saw_structured:
        parts.append({"type": "data-structured-result", "data": structured_result})
    if answer_text and not saw_text:
        parts.append({"type": "text", "text": answer_text, "state": "done"})
    return parts
