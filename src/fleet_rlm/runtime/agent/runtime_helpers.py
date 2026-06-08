"""Pure helpers for AgentRuntime prediction, routing, and stream event shaping."""

from __future__ import annotations

import ast
import asyncio
import json
from typing import Any, cast

import dspy

from fleet_rlm.runtime.events import RuntimeEvent
from fleet_rlm.runtime.schemas import StreamEvent, StreamEventKind


def default_core_memory() -> dict[str, str]:
    return {
        "persona": (
            "I am a helpful AI assistant embedded in fleet-rlm, a recursive long-chain-of-thought "
            "agent system that delegates complex tasks to a fleet of specialised sub-agents via "
            "Daytona sandboxes. I am focused on helping developers understand and extend this system."
        ),
        "human": "The user is a developer working on the fleet-rlm project.",
        "scratchpad": "",
    }


def append_turn_to_history(
    history: dspy.History,
    *,
    user_message: str,
    response: str,
    history_max_turns: int | None,
) -> dspy.History:
    messages = list(getattr(history, "messages", []) or [])
    messages.append({"user_message": user_message, "response": response})
    if history_max_turns is not None and len(messages) > history_max_turns:
        messages = messages[-history_max_turns:]
    return dspy.History(messages=messages)


def prediction_value(result: Any, name: str) -> Any:
    if isinstance(result, dict) and name in result:
        return result.get(name)
    return getattr(result, name, None)


def prediction_response_text(result: Any) -> str:
    for field_name in ("response", "assistant_response", "answer"):
        value = prediction_value(result, field_name)
        if value not in (None, ""):
            return str(value)
    return ""


def prediction_reasoning_text(result: Any) -> str:
    """Return ChainOfThought / module reasoning when no ReAct trajectory is present."""
    for field_name in ("reasoning", "final_reasoning"):
        value = prediction_value(result, field_name)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def runtime_degradation_payload(result: Any) -> dict[str, Any]:
    runtime_degraded = bool(prediction_value(result, "runtime_degraded") or prediction_value(result, "degraded"))
    if not runtime_degraded:
        return {}

    payload: dict[str, Any] = {"runtime_degraded": True}
    for key in (
        "runtime_failure_category",
        "runtime_failure_phase",
        "runtime_fallback_used",
        "runtime_warning",
    ):
        value = prediction_value(result, key)
        if value not in (None, ""):
            payload[key] = value

    payload.setdefault("runtime_failure_category", "rlm_fallback")
    payload.setdefault("runtime_failure_phase", "escalating_rlm")
    payload.setdefault("runtime_fallback_used", True)
    payload.setdefault("runtime_warning", "RLM escalation fell back to the lightweight response path.")
    return payload


def runtime_routing_payload(result: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    selected_skills = prediction_value(result, "selected_skills")
    if isinstance(selected_skills, list):
        payload["selected_skills"] = [str(item) for item in selected_skills]
    routing_decision = prediction_value(result, "routing_decision")
    if routing_decision not in (None, ""):
        payload["routing_decision"] = str(routing_decision)
    source_url = prediction_value(result, "source_url")
    if source_url not in (None, ""):
        payload["source_url"] = str(source_url)
    return payload


def routing_status_text(payload: dict[str, Any]) -> str:
    selected = ", ".join(payload.get("selected_skills", []))
    route = payload.get("routing_decision", "auto")
    source = payload.get("source_url")
    text = f"Route: {route}"
    if selected:
        text += f" | skills: {selected}"
    if source:
        text += f" | source: {source}"
    return text


def get_streamable_react_program(program: Any) -> Any | None:
    for candidate in (program, getattr(program, "react", None)):
        if candidate is None:
            continue
        planner = getattr(candidate, "planner", None)
        extract = getattr(candidate, "extract", None)
        async_call = getattr(candidate, "async_planner_step", None)
        if planner is not None and extract is not None and callable(async_call):
            return candidate
    return None


def format_react_trajectory(program: Any, trajectory_raw: Any) -> str:
    """Format a ReAct trajectory for extract, with a safe fallback for custom programs."""
    formatter = getattr(program, "_format_trajectory", None)
    if callable(formatter):
        return formatter(trajectory_raw)
    return str(trajectory_raw)


def normalize_tool_args(tool_args: Any) -> dict[str, Any]:
    return dict(tool_args) if isinstance(tool_args, dict) else {}


def observation_record(observation: Any) -> dict[str, Any]:
    if isinstance(observation, dict):
        return observation
    if not isinstance(observation, str):
        return {}
    stripped = observation.strip()
    if not stripped:
        return {}
    for loader in (json.loads, ast.literal_eval):
        try:
            parsed = loader(stripped)
        except (SyntaxError, ValueError, TypeError, json.JSONDecodeError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


def recursive_child_review_payload(tool_name: str, observation: Any) -> dict[str, Any] | None:
    if tool_name not in {"delegate_to_rlm", "delegate_to_rlm_batched"}:
        return None

    record = observation_record(observation)
    if not record:
        return None

    status = str(record.get("status", "")).lower()
    degraded = bool(record.get("degraded"))
    reviews = record.get("reviews")
    if status != "needs_human_review" and not degraded and not reviews:
        return None

    reason = (
        str(record.get("reason") or record.get("degradation_reason") or "recursive_child_degraded")
        .strip()
        .replace("_", " ")
    )
    return {
        "required": True,
        "reason": f"Recursive child result needs review: {reason}.",
        "repair_mode": "needs_human_review",
        "repair_target": "Review degraded recursive child output before accepting the run.",
        "repair_steps": ["Inspect the preserved child answer and degradation metadata."],
    }


async def call_react_tool(tool: Any, tool_args: dict[str, Any]) -> Any:
    acall = getattr(tool, "acall", None)
    if callable(acall):
        return await acall(**tool_args)
    return await asyncio.to_thread(tool, **tool_args)


def stream_event_from_runtime_event(event: RuntimeEvent) -> StreamEvent:
    return StreamEvent(
        kind=cast(StreamEventKind, event.kind.value),
        text=event.text,
        payload=dict(event.payload),
        timestamp=event.timestamp,
    )


def build_tool_call_event(*, tool_name: str, tool_args: dict[str, Any], step_index: int) -> RuntimeEvent:
    return RuntimeEvent.tool_call(
        tool_name=tool_name,
        tool_args=tool_args,
        step_index=step_index,
    )


def build_tool_result_event(*, tool_name: str, observation: Any, step_index: int) -> RuntimeEvent:
    return RuntimeEvent.tool_result(
        tool_name=tool_name,
        observation=observation,
        step_index=step_index,
    )


def build_clarification_event(observation: Any) -> RuntimeEvent | None:
    if not isinstance(observation, dict) or observation.get("status") != "clarification_needed":
        return None

    return RuntimeEvent.clarification(
        message_id=str(observation.get("message_id") or f"clar-{uuid.uuid4().hex[:8]}"),
        question=observation.get("question"),
        step_label=observation.get("step_label", "Clarification needed"),
        options=observation.get("options", []),
    )
