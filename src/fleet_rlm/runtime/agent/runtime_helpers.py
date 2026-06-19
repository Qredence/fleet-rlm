"""Pure helpers for AgentRuntime prediction, routing, and stream event shaping."""

from __future__ import annotations

import ast
import json
import uuid
from typing import Any

import dspy

from fleet_rlm.runtime.events import RuntimeEvent
from fleet_rlm.runtime.execution.final_artifact import build_final_artifact_from_answer


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
    value = prediction_value(result, "response")
    return str(value) if value not in (None, "") else ""


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


def attach_final_artifact(
    payload: dict[str, Any],
    *,
    answer: str,
    task: str | None = None,
) -> dict[str, Any]:
    """Add a structured ``final_artifact`` when the answer warrants markdown or code output."""
    if payload.get("final_artifact"):
        return payload
    routing_decision = payload.get("routing_decision")
    if not isinstance(routing_decision, str):
        routing_decision = None
    artifact = build_final_artifact_from_answer(
        answer,
        task=task,
        routing_decision=routing_decision,
    )
    if artifact is not None:
        payload["final_artifact"] = artifact
        payload["output_format"] = artifact.get("kind")
    return payload


def routing_status_text(payload: dict[str, Any]) -> str:
    selected = ", ".join(payload.get("selected_skills", []))
    route = payload.get("routing_decision", "auto")
    source = payload.get("source_url")
    estimated = payload.get("estimated_chars")
    text = f"Route: {route}"
    if route == "large_context_rlm" and estimated is not None:
        text = f"Route: large_context_rlm ({estimated} chars) — using dspy.RLM"
    if selected:
        text += f" | skills: {selected}"
    if source:
        text += f" | source: {source}"
    return text


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


def build_clarification_event(observation: Any) -> RuntimeEvent | None:
    if not isinstance(observation, dict) or observation.get("status") != "clarification_needed":
        return None

    return RuntimeEvent.clarification(
        message_id=str(observation.get("message_id") or f"clar-{uuid.uuid4().hex[:8]}"),
        question=observation.get("question"),
        step_label=observation.get("step_label", "Clarification needed"),
        options=observation.get("options", []),
    )


def relay_event_from_rlm_step(payload: dict[str, Any]) -> RuntimeEvent | None:
    """Convert an RLM iteration callback payload into a canonical RuntimeEvent."""
    if not isinstance(payload, dict):
        return None
    phase = str(payload.get("phase", "")).strip().lower()
    event_kind = str(payload.get("event_kind", "")).strip().lower()
    iteration = payload.get("iteration")
    step_index = int(iteration) if isinstance(iteration, int) else None

    if phase == "mlflow_span" or event_kind == "mlflow_span":
        span_id = str(payload.get("span_id") or "").strip()
        if not span_id:
            return None
        return RuntimeEvent.mlflow_span(
            span_id=span_id,
            name=str(payload.get("name") or payload.get("span_name") or "MLflow span"),
            status=str(payload.get("status") or "started"),
            parent_span_id=str(payload["parent_span_id"]) if payload.get("parent_span_id") else None,
            trace_id=str(payload["trace_id"]) if payload.get("trace_id") else None,
            duration_ms=payload.get("duration_ms") if isinstance(payload.get("duration_ms"), (int, float)) else None,
            started_at=str(payload["started_at"]) if payload.get("started_at") else None,
            ended_at=str(payload["ended_at"]) if payload.get("ended_at") else None,
            input=payload.get("input", payload.get("span_input")),
            output=payload.get("output", payload.get("span_output")),
            error=payload.get("error"),
            metadata=payload.get("metadata") if isinstance(payload.get("metadata"), dict) else None,
        )

    if phase == "rlm_reasoning":
        reasoning = str(payload.get("reasoning") or "").strip()
        if not reasoning:
            return None
        event = RuntimeEvent.reasoning(reasoning)
        if step_index is not None:
            event.payload["step_index"] = step_index
        return event

    if phase == "rlm_tool_call":
        code = str(payload.get("code") or payload.get("code_preview") or "")
        tool_name = str(payload.get("tool_name") or "repl_execute")
        tool_args = {"code": code} if code else {}
        event = RuntimeEvent.tool_call(
            tool_name=tool_name,
            tool_args=tool_args,
            step_index=step_index,
        )
        event.payload["trajectory_index"] = step_index
        return event

    if phase == "rlm_tool_result":
        observation = payload.get("output") or payload.get("observation") or ""
        tool_name = str(payload.get("tool_name") or "repl_execute")
        event = RuntimeEvent.tool_result(
            tool_name=tool_name,
            observation=observation,
            step_index=step_index,
        )
        event.payload["output"] = observation
        event.payload["trajectory_index"] = step_index
        return event

    if phase in {"document_fetch", "rlm_start", "large_context_prepare", "context_estimate", "rlm_progress"}:
        text = str(payload.get("text") or "").strip()
        if not text and phase == "rlm_progress":
            elapsed = payload.get("elapsed_s")
            text = (
                f"RLM execution in progress ({elapsed}s)..." if elapsed is not None else "RLM execution in progress..."
            )
        if not text:
            return None
        return RuntimeEvent.status(text, payload=dict(payload))

    return None


def relay_event_from_interpreter_hook(payload: dict[str, Any]) -> RuntimeEvent | None:
    """Map interpreter execution_event_callback payloads to chat RuntimeEvents."""
    if not isinstance(payload, dict):
        return None
    phase = str(payload.get("phase", "")).strip().lower()
    code_preview = str(payload.get("code_preview") or "")

    if phase == "start":
        tool_args = {"code": code_preview} if code_preview else {}
        event = RuntimeEvent.tool_call(tool_name="repl_execute", tool_args=tool_args)
        event.payload["phase"] = "sandbox_exec"
        event.payload["code_hash"] = payload.get("code_hash")
        return event

    if phase == "complete":
        stdout = payload.get("stdout_preview") or ""
        stderr = payload.get("stderr_preview") or ""
        observation = stdout or stderr or ("ok" if payload.get("success") else "error")
        event = RuntimeEvent.tool_result(tool_name="repl_execute", observation=observation)
        event.payload["phase"] = "sandbox_exec"
        event.payload["success"] = payload.get("success")
        if stderr:
            event.payload["stderr_preview"] = stderr
        return event

    if phase == "progress":
        path = str(payload.get("path") or "").strip()
        label = str(payload.get("event_kind") or "progress")
        text = f"{label}: {path}" if path else label
        return RuntimeEvent.status(
            text,
            payload={
                "phase": "sandbox_output",
                "path": path or None,
                "bytes_written": payload.get("bytes_written"),
                "bytes_total": payload.get("bytes_total"),
            },
        )

    return None


def emit_turn_progress_from_payload(relay: Any, payload: dict[str, Any], *, source: str) -> None:
    """Emit a RuntimeEvent to a TurnProgressRelay from RLM or interpreter payloads."""
    if relay is None:
        return
    converter = relay_event_from_rlm_step if source == "rlm" else relay_event_from_interpreter_hook
    event = converter(payload)
    if event is None:
        return
    emit = getattr(relay, "emit_threadsafe", None)
    if callable(emit):
        emit(event)
