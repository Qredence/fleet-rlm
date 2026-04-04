"""Streaming orchestration for the RLM ReAct chat agent.

Consolidates streaming context, status parsing, citation handling, and
sync/async streaming iterators.  Yields :class:`~fleet_rlm.models.StreamEvent`
objects through DSPy's :func:`dspy.streamify` pipeline.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Iterable, Literal, cast
from urllib.parse import urlparse

import dspy
from dspy.streaming.messages import StatusMessage, StatusMessageProvider, StreamResponse
from dspy.streaming.streaming_listener import StreamListener

from fleet_rlm.runtime.config import build_dspy_context
from fleet_rlm.runtime.models.streaming import StreamEvent

if TYPE_CHECKING:
    from fleet_rlm.runtime.agent.chat_agent import RLMReActChatAgent


# ═══════════════════════════════════════════════════════════════════════
# Streaming context  (was streaming_context.py)
# ═══════════════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class StreamingContext:
    """Immutable snapshot of agent runtime metadata for one chat turn.

    Fields
    ------
    depth : int
        Current recursion depth (0 = root agent).
    max_depth : int
        Maximum allowed recursion depth.
    execution_profile : str
        Default execution profile label (e.g. ``"ROOT_INTERLOCUTOR"``).
    volume_name : str | None
        Modal Volume name when persistent storage is attached.
    sandbox_active : bool
        Whether a Modal Sandbox session is currently alive.
    effective_max_iters : int
        Iteration budget computed for this turn.
    execution_mode : str
        High-level execution mode label (for example ``"auto"`` or ``"rlm"``).
    sandbox_id : str | None
        Stable sandbox identifier when the provider exposes one.
    workspace_path : str | None
        Active sandbox workspace path when the provider exposes one.
    sandbox_transition : str | None
        Lifecycle transition for the active provider session (for example
        ``"created"``, ``"reused"``, or ``"resumed"``).
    runtime_degraded : bool
        Whether the active runtime degraded and the turn recovered via fallback.
    runtime_failure_category : str | None
        Stable failure category for the primary runtime error, when available.
    runtime_failure_phase : str | None
        Stable failure phase for the primary runtime error, when available.
    runtime_fallback_used : bool
        Whether the turn recovered via a runtime fallback after degradation.
    """

    depth: int = 0
    max_depth: int = 2
    execution_profile: str = "ROOT_INTERLOCUTOR"
    volume_name: str | None = None
    sandbox_active: bool = False
    effective_max_iters: int = 10
    execution_mode: str = "auto"
    sandbox_id: str | None = None
    workspace_path: str | None = None
    sandbox_transition: str | None = None
    runtime_degraded: bool = False
    runtime_failure_category: str | None = None
    runtime_failure_phase: str | None = None
    runtime_fallback_used: bool = False

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_agent(
        cls,
        agent: RLMReActChatAgent,
        *,
        effective_max_iters: int | None = None,
    ) -> StreamingContext:
        """Build a context snapshot from the live agent state."""
        interpreter = agent.interpreter
        runtime_metadata_fn = getattr(interpreter, "current_runtime_metadata", None)
        runtime_metadata = (
            runtime_metadata_fn() if callable(runtime_metadata_fn) else {}
        )
        session = getattr(interpreter, "_session", None)
        fallback_sandbox_active = (
            session is not None or getattr(interpreter, "_sandbox", None) is not None
        )
        sandbox_active = bool(
            runtime_metadata.get("sandbox_active", fallback_sandbox_active)
            if isinstance(runtime_metadata, dict)
            else fallback_sandbox_active
        )
        sandbox_id = None
        workspace_path = None
        sandbox_transition = None
        volume_name = getattr(interpreter, "volume_name", None)
        runtime_degraded = False
        runtime_failure_category = None
        runtime_failure_phase = None
        runtime_fallback_used = False
        if isinstance(runtime_metadata, dict):
            sandbox_id = runtime_metadata.get("sandbox_id")
            workspace_path = runtime_metadata.get("workspace_path")
            sandbox_transition = runtime_metadata.get("sandbox_transition")
            volume_name = runtime_metadata.get("volume_name", volume_name)
            runtime_degraded = bool(runtime_metadata.get("runtime_degraded", False))
            runtime_failure_category = runtime_metadata.get("runtime_failure_category")
            runtime_failure_phase = runtime_metadata.get("runtime_failure_phase")
            runtime_fallback_used = bool(
                runtime_metadata.get("runtime_fallback_used", False)
            )

        profile_name = "ROOT_INTERLOCUTOR"
        if hasattr(interpreter, "default_execution_profile"):
            raw = interpreter.default_execution_profile
            profile_name = str(raw.value) if hasattr(raw, "value") else str(raw)

        return cls(
            depth=agent.current_depth,
            max_depth=agent._max_depth,
            execution_profile=profile_name,
            volume_name=volume_name,
            sandbox_active=sandbox_active,
            effective_max_iters=(
                effective_max_iters
                if effective_max_iters is not None
                else agent._current_effective_max_iters
            ),
            execution_mode=str(getattr(agent, "execution_mode", "auto") or "auto"),
            sandbox_id=str(sandbox_id).strip() or None if sandbox_id else None,
            workspace_path=(
                str(workspace_path).strip() or None if workspace_path else None
            ),
            sandbox_transition=(
                str(sandbox_transition).strip() or None if sandbox_transition else None
            ),
            runtime_degraded=runtime_degraded,
            runtime_failure_category=(
                str(runtime_failure_category).strip() or None
                if runtime_failure_category
                else None
            ),
            runtime_failure_phase=(
                str(runtime_failure_phase).strip() or None
                if runtime_failure_phase
                else None
            ),
            runtime_fallback_used=runtime_fallback_used,
        )

    # ------------------------------------------------------------------
    # Payload helpers
    # ------------------------------------------------------------------

    def as_payload(self) -> dict[str, Any]:
        """Return a flat dict suitable for merging into event payloads."""
        ctx: dict[str, Any] = {
            "depth": self.depth,
            "max_depth": self.max_depth,
            "execution_profile": self.execution_profile,
            "sandbox_active": self.sandbox_active,
            "provider_session_active": self.sandbox_active,
            "effective_max_iters": self.effective_max_iters,
            "execution_mode": self.execution_mode,
        }
        if self.volume_name:
            ctx["volume_name"] = self.volume_name
            ctx["configured_volume_name"] = self.volume_name
        if self.sandbox_id:
            ctx["sandbox_id"] = self.sandbox_id
            ctx["provider_session_id"] = self.sandbox_id
        if self.workspace_path:
            ctx["workspace_path"] = self.workspace_path
        if self.sandbox_transition:
            ctx["sandbox_transition"] = self.sandbox_transition
        if self.runtime_degraded:
            ctx["runtime_degraded"] = True
        if self.runtime_failure_category:
            ctx["runtime_failure_category"] = self.runtime_failure_category
        if self.runtime_failure_phase:
            ctx["runtime_failure_phase"] = self.runtime_failure_phase
        if self.runtime_fallback_used:
            ctx["runtime_fallback_used"] = True
        return ctx

    def enrich(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Return *payload* merged with runtime context under ``"runtime"``."""
        runtime_payload = self.as_payload()
        existing_runtime = payload.get("runtime")
        if isinstance(existing_runtime, dict):
            runtime_payload = {**runtime_payload, **existing_runtime}

        enriched = dict(payload)
        for key, value in runtime_payload.items():
            enriched.setdefault(key, value)
        enriched["runtime"] = runtime_payload
        return enriched


# ═══════════════════════════════════════════════════════════════════════
# Status parsing and tool/HITL event helpers  (was streaming_status.py)
# ═══════════════════════════════════════════════════════════════════════

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


# ═══════════════════════════════════════════════════════════════════════
# Citation and final-payload helpers  (was streaming_citations.py)
# ═══════════════════════════════════════════════════════════════════════

STREAM_EVENT_SCHEMA_VERSION = 2
_ALLOWED_EXTERNAL_URL_SCHEMES = frozenset({"http", "https"})


def _extract_step_indices(raw: dict[str, Any]) -> list[int]:
    indices: set[int] = set()
    for key in raw:
        parts = key.rsplit("_", 1)
        if len(parts) == 2 and parts[1].isdigit():
            indices.add(int(parts[1]))
    return sorted(indices)


def _build_flat_trajectory_step(raw: dict[str, Any], index: int) -> dict[str, Any]:
    step: dict[str, Any] = {"index": index}
    thought = raw.get(f"thought_{index}")
    tool_name = raw.get(f"tool_name_{index}")
    tool_args = raw.get(f"tool_args_{index}")
    input_value = raw.get(f"input_{index}")
    observation = raw.get(f"observation_{index}")
    output = raw.get(f"output_{index}")

    if thought is not None:
        step["thought"] = thought
    if tool_name is not None:
        step["tool_name"] = tool_name

    final_input = input_value if input_value is not None else tool_args
    if final_input is not None:
        step["input"] = final_input
        step["tool_args"] = final_input

    final_output = output if output is not None else observation
    if final_output is not None:
        step["output"] = final_output
        step["observation"] = final_output

    return step


def _normalize_trajectory(raw: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Convert DSPy ReAct flat trajectory to structured step list."""
    if not raw:
        return []
    if "steps" in raw and isinstance(raw["steps"], list):
        return raw["steps"]
    if "trajectory" in raw and isinstance(raw["trajectory"], list):
        return raw["trajectory"]

    return [
        _build_flat_trajectory_step(raw, index) for index in _extract_step_indices(raw)
    ]


def _as_text(value: Any) -> str | None:
    if isinstance(value, str):
        trimmed = value.strip()
        return trimmed or None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return None


def _sanitize_external_url(value: Any) -> str | None:
    url = _as_text(value)
    if not url:
        return None
    parsed = urlparse(url)
    if parsed.scheme.lower() not in _ALLOWED_EXTERNAL_URL_SCHEMES:
        return None
    return url


def _normalize_citation_title(item: dict[str, Any], number: str) -> str:
    return (
        _as_text(item.get("title"))
        or _as_text(item.get("source_title"))
        or _as_text(item.get("source"))
        or f"Source {number}"
    )


def _normalize_citation_ids(item: dict[str, Any], index: int) -> tuple[str, str]:
    source_id = _as_text(item.get("source_id")) or f"source-{index + 1}"
    anchor_id = _as_text(item.get("anchor_id")) or f"anchor-{index + 1}"
    return source_id, anchor_id


def _append_optional_citation_fields(
    normalized: dict[str, Any],
    item: dict[str, Any],
) -> None:
    description = _as_text(item.get("description"))
    if description:
        normalized["description"] = description

    quote = _as_text(item.get("quote"))
    evidence = _as_text(item.get("evidence"))
    if quote:
        normalized["quote"] = quote
    elif evidence:
        normalized["quote"] = evidence

    if isinstance(item.get("start_char"), int):
        normalized["start_char"] = item["start_char"]
    if isinstance(item.get("end_char"), int):
        normalized["end_char"] = item["end_char"]


def _normalize_citation_entry(item: Any, *, index: int) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None

    url = _sanitize_external_url(
        item.get("url") or item.get("source_url") or item.get("canonical_url")
    )
    if not url:
        return None

    number = _as_text(item.get("number")) or str(index + 1)
    title = _normalize_citation_title(item, number)
    source_id, anchor_id = _normalize_citation_ids(item, index)

    normalized: dict[str, Any] = {
        "number": number,
        "title": title,
        "url": url,
        "source_id": source_id,
        "anchor_id": anchor_id,
    }
    _append_optional_citation_fields(normalized, item)
    return normalized


def _extract_citations_from_output(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []

    if isinstance(value, dict):
        raw = value.get("citations")
        if isinstance(raw, list):
            return raw
        return []

    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return []
        return _extract_citations_from_output(parsed)

    return []


def _prediction_citations(
    final_prediction: dspy.Prediction | None,
) -> list[dict[str, Any]]:
    if final_prediction is None:
        return []
    raw = getattr(final_prediction, "citations", None)
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _trajectory_citations(trajectory: dict[str, Any]) -> list[dict[str, Any]]:
    raw = trajectory.get("citations")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _trajectory_step_citation_candidates(
    trajectory: dict[str, Any],
) -> list[list[dict[str, Any]]]:
    candidates: list[list[dict[str, Any]]] = []
    for step in _normalize_trajectory(trajectory):
        if not isinstance(step, dict):
            continue
        citations = _extract_citations_from_output(step.get("output"))
        if citations:
            candidates.append([item for item in citations if isinstance(item, dict)])
    return candidates


def _merge_citation_candidates(
    candidate_lists: list[list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for candidate in candidate_lists:
        merged.extend(candidate)
    return merged


def _dedupe_normalized_citations(merged: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for idx, item in enumerate(merged):
        normalized_item = _normalize_citation_entry(item, index=idx)
        if not normalized_item:
            continue
        dedupe_key = (
            f"{normalized_item.get('source_id', '')}"
            f"|{normalized_item.get('anchor_id', '')}"
            f"|{normalized_item.get('url', '')}"
            f"|{normalized_item.get('quote', '')}"
        )
        if dedupe_key in seen_keys:
            continue
        seen_keys.add(dedupe_key)
        normalized.append(normalized_item)
    return normalized


def _extract_final_citations(
    *, final_prediction: dspy.Prediction | None, trajectory: dict[str, Any]
) -> list[dict[str, Any]]:
    candidate_lists: list[list[dict[str, Any]]] = []

    prediction = _prediction_citations(final_prediction)
    if prediction:
        candidate_lists.append(prediction)

    direct = _trajectory_citations(trajectory)
    if direct:
        candidate_lists.append(direct)

    candidate_lists.extend(_trajectory_step_citation_candidates(trajectory))
    merged = _merge_citation_candidates(candidate_lists)
    return _dedupe_normalized_citations(merged)


def _build_sources_from_citations(
    citations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    sources_by_key: dict[str, dict[str, Any]] = {}
    for citation in citations:
        source_id = _as_text(citation.get("source_id")) or "source"
        url = _sanitize_external_url(citation.get("url"))
        if not url:
            continue
        key = url.lower().rstrip("/")
        if key in sources_by_key:
            continue
        sources_by_key[key] = {
            "source_id": source_id,
            "kind": "web",
            "title": _as_text(citation.get("title")) or "Source",
            "canonical_url": url,
            "display_url": url,
            "description": _as_text(citation.get("description")),
            "quote": _as_text(citation.get("quote")),
        }
    return list(sources_by_key.values())


def _collect_attachment_candidates(
    *,
    final_prediction: dspy.Prediction | None,
    trajectory: dict[str, Any],
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    if final_prediction is not None:
        raw = getattr(final_prediction, "attachments", None)
        if isinstance(raw, list):
            candidates.extend(item for item in raw if isinstance(item, dict))
    raw_from_trajectory = trajectory.get("attachments")
    if isinstance(raw_from_trajectory, list):
        candidates.extend(
            item for item in raw_from_trajectory if isinstance(item, dict)
        )
    return candidates


def _normalize_attachment_entry(item: dict[str, Any], index: int) -> dict[str, Any]:
    attachment_id = (
        _as_text(item.get("attachment_id") or item.get("id")) or f"att-{index + 1}"
    )
    return {
        "attachment_id": attachment_id,
        "name": _as_text(item.get("name") or item.get("title")) or "Attachment",
        "url": _sanitize_external_url(item.get("url") or item.get("download_url")),
        "preview_url": _sanitize_external_url(item.get("preview_url")),
        "mime_type": _as_text(item.get("mime_type") or item.get("mimeType")),
        "media_type": _as_text(item.get("media_type") or item.get("mediaType")),
        "size_bytes": item.get("size_bytes")
        if isinstance(item.get("size_bytes"), int)
        else None,
        "kind": _as_text(item.get("kind")),
        "description": _as_text(item.get("description")),
    }


def _extract_final_attachments(
    *, final_prediction: dspy.Prediction | None, trajectory: dict[str, Any]
) -> list[dict[str, Any]]:
    candidates = _collect_attachment_candidates(
        final_prediction=final_prediction,
        trajectory=trajectory,
    )

    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for idx, item in enumerate(candidates):
        attachment_id = (
            _as_text(item.get("attachment_id") or item.get("id")) or f"att-{idx + 1}"
        )
        if attachment_id in seen:
            continue
        seen.add(attachment_id)
        normalized.append(_normalize_attachment_entry(item, idx))
    return normalized


def _build_final_payload(
    *,
    final_prediction: dspy.Prediction | None,
    trajectory: dict[str, Any],
    history_turns: int,
    guardrail_warnings: list[str],
    turn_metrics: dict[str, Any],
    fallback: bool,
    fallback_error_type: str | None = None,
    effective_max_iters: int | None = None,
) -> dict[str, Any]:
    citations = _extract_final_citations(
        final_prediction=final_prediction, trajectory=trajectory
    )
    sources = _build_sources_from_citations(citations)
    attachments = _extract_final_attachments(
        final_prediction=final_prediction, trajectory=trajectory
    )
    citation_anchors = [
        {
            "anchor_id": citation.get("anchor_id") or f"anchor-{idx + 1}",
            "source_id": citation.get("source_id") or f"source-{idx + 1}",
            "number": citation.get("number") or str(idx + 1),
            "start_char": citation.get("start_char"),
            "end_char": citation.get("end_char"),
        }
        for idx, citation in enumerate(citations)
    ]

    payload: dict[str, Any] = {
        "schema_version": STREAM_EVENT_SCHEMA_VERSION,
        "trajectory": trajectory,
        "history_turns": history_turns,
        "guardrail_warnings": guardrail_warnings,
        "citations": citations,
        "sources": sources,
        "attachments": attachments,
        "citation_anchors": citation_anchors,
        "fallback": fallback,
        **turn_metrics,
    }
    if fallback_error_type:
        payload["error_type"] = fallback_error_type
    if effective_max_iters is not None:
        payload["effective_max_iters"] = effective_max_iters
    return payload


# ═══════════════════════════════════════════════════════════════════════
# Streaming orchestration
# ═══════════════════════════════════════════════════════════════════════

logger = logging.getLogger(__name__)
TERMINAL_STREAM_EVENT_KINDS: frozenset[str] = frozenset({"final", "cancelled", "error"})


@dataclass(slots=True)
class PreparedStreamingTurn:
    """Shared per-turn streaming metadata used by sync and async flows."""

    effective_max_iters: int
    ctx: StreamingContext
    stream_listeners: list[StreamListener]


@dataclass(slots=True)
class _ActiveStreamingTurn:
    """Mutable per-stream state shared by sync and async iterators."""

    assistant_chunks: list[str] = field(default_factory=list)
    final_prediction: dspy.Prediction | None = None
    last_tool_name_ref: list[str | None] = field(default_factory=lambda: [None])
    pending_live_events: list[StreamEvent] = field(default_factory=list)
    previous_live_callback: Any = None


def is_terminal_stream_event_kind(kind: str) -> bool:
    """Return whether *kind* is terminal for both runtime and websocket flows."""
    return kind in TERMINAL_STREAM_EVENT_KINDS


def prepare_streaming_turn(
    agent: RLMReActChatAgent, *, message: str, trace: bool
) -> PreparedStreamingTurn:
    """Validate a message and build the shared streaming turn context."""
    if not message or not message.strip():
        raise ValueError("message cannot be empty")

    agent.start()
    effective_max_iters = agent._prepare_turn(message)
    stream_listeners = [StreamListener(signature_field_name="assistant_response")]
    if trace:
        stream_listeners.append(
            StreamListener(signature_field_name="next_thought", allow_reuse=True)
        )
    return PreparedStreamingTurn(
        effective_max_iters=effective_max_iters,
        ctx=StreamingContext.from_agent(agent, effective_max_iters=effective_max_iters),
        stream_listeners=stream_listeners,
    )


def build_cancelled_stream_event(
    *,
    agent: RLMReActChatAgent,
    message: str,
    assistant_chunks: list[str],
    ctx: StreamingContext,
) -> StreamEvent:
    """Build the canonical cancellation event for a partially streamed turn."""
    partial = "".join(assistant_chunks).strip()
    marked_partial = f"{partial}\n\n[cancelled]" if partial else "[cancelled]"
    agent._append_history(message, marked_partial)
    return StreamEvent(
        kind="cancelled",
        text=marked_partial,
        payload=ctx.enrich(
            {
                "history_turns": agent.history_turns(),
                **agent._turn_metrics(),
            }
        ),
    )


def _extract_final_response(
    *,
    final_prediction: dspy.Prediction | None,
    assistant_chunks: list[str],
) -> tuple[str, dict[str, Any], str]:
    """Normalize the final assistant text, trajectory, and reasoning summary."""
    if final_prediction is not None:
        assistant_response = str(
            getattr(final_prediction, "assistant_response", "")
        ).strip()
        raw_trajectory = getattr(final_prediction, "trajectory", {})
        trajectory = raw_trajectory if isinstance(raw_trajectory, dict) else {}
        final_reasoning = ""
        if hasattr(final_prediction, "reasoning"):
            final_reasoning = str(final_prediction.reasoning)
        elif trajectory:
            steps = _normalize_trajectory(trajectory)
            reasoning_parts = []
            for step in steps:
                if isinstance(step, dict):
                    thought = step.get("thought", "")
                    if thought:
                        reasoning_parts.append(thought)
            final_reasoning = "\n".join(reasoning_parts)
        return assistant_response, trajectory, final_reasoning

    return "".join(assistant_chunks).strip(), {}, ""


def build_final_stream_event(
    *,
    agent: RLMReActChatAgent,
    message: str,
    final_prediction: dspy.Prediction | None,
    assistant_chunks: list[str],
    ctx: StreamingContext,
) -> StreamEvent:
    """Build the canonical terminal final event for a completed streamed turn."""
    assistant_response, trajectory, final_reasoning = _extract_final_response(
        final_prediction=final_prediction,
        assistant_chunks=assistant_chunks,
    )
    agent._finalize_turn(trajectory)
    assistant_response, guardrail_warnings = agent._validate_assistant_response(
        assistant_response=assistant_response,
        trajectory=trajectory,
    )
    agent._append_history(message, assistant_response)
    return StreamEvent(
        kind="final",
        flush_tokens=True,
        text=assistant_response,
        payload=ctx.enrich(
            {
                **_build_final_payload(
                    final_prediction=final_prediction,
                    trajectory=cast(dict[str, Any], trajectory or {}),
                    history_turns=agent.history_turns(),
                    guardrail_warnings=guardrail_warnings,
                    turn_metrics=agent._turn_metrics(),
                    fallback=False,
                ),
                "final_reasoning": final_reasoning,
            }
        ),
    )


# ---------------------------------------------------------------------------
# Synchronous streaming
# ---------------------------------------------------------------------------


def _build_fallback_events(
    *,
    fallback: dict[str, Any],
    exc: Exception,
    effective_max_iters: int,
    agent: RLMReActChatAgent,
    init_phase: bool,
    ctx: StreamingContext,
) -> Iterable[StreamEvent]:
    prefix = "streaming unavailable" if init_phase else "stream error"
    yield StreamEvent(
        kind="status",
        text=f"{prefix}; fell back to non-streaming ({exc})",
        payload=ctx.enrich({"fallback": True, "error_type": type(exc).__name__}),
    )
    yield StreamEvent(
        kind="final",
        flush_tokens=True,
        text=str(fallback.get("assistant_response", "")),
        payload=ctx.enrich(
            _build_final_payload(
                final_prediction=None,
                trajectory=cast(dict[str, Any], fallback.get("trajectory", {}) or {}),
                history_turns=int(fallback.get("history_turns", agent.history_turns())),
                guardrail_warnings=[],
                turn_metrics={
                    "delegate_calls_turn": fallback.get("delegate_calls_turn", 0),
                    "delegate_fallback_count_turn": fallback.get(
                        "delegate_fallback_count_turn", 0
                    ),
                    "delegate_result_truncated_count_turn": fallback.get(
                        "delegate_result_truncated_count_turn", 0
                    ),
                },
                fallback=True,
                fallback_error_type=type(exc).__name__,
                effective_max_iters=int(
                    fallback.get("effective_max_iters", effective_max_iters)
                ),
            )
        ),
    )


def _drain_live_events(
    pending_events: list[StreamEvent],
) -> Iterable[StreamEvent]:
    """Yield and clear queued nested events emitted by child runtimes."""
    # Snapshot current events, then clear the list so callers see it as drained.
    # This preserves the original event order while avoiding O(N) pop(0) calls.
    events = list(pending_events)
    pending_events.clear()
    yield from events


def _process_stream_value(
    *,
    value: Any,
    trace: bool,
    assistant_chunks: list[str],
    last_tool_name_ref: list[str | None],
    ctx: StreamingContext | None = None,
) -> Iterable[StreamEvent]:
    if isinstance(value, StreamResponse):
        if value.signature_field_name == "assistant_response":
            assistant_chunks.append(value.chunk)
            yield StreamEvent(
                kind="assistant_token",
                text=value.chunk,
                payload=ctx.enrich({}) if ctx else {},
            )
        elif value.signature_field_name == "next_thought" and trace:
            yield StreamEvent(
                kind="reasoning_step",
                text=value.chunk,
                payload=ctx.enrich({"source": "next_thought"})
                if ctx
                else {"source": "next_thought"},
            )
        return

    if isinstance(value, StatusMessage):
        text = value.message
        yield StreamEvent(
            kind="status",
            text=text,
            payload=ctx.enrich({"raw_status": text}) if ctx else {"raw_status": text},
        )

        tool_call = parse_tool_call_status(text)
        if tool_call:
            tool_payload = parse_tool_call_payload(text) or {}
            parsed_name = tool_payload.get("tool_name")
            if isinstance(parsed_name, str) and parsed_name:
                last_tool_name_ref[0] = parsed_name

            yield StreamEvent(
                kind=classify_tool_event_kind(
                    parsed_name if isinstance(parsed_name, str) else None
                ),
                text=tool_call,
                payload=ctx.enrich(tool_payload) if ctx else tool_payload,
            )

        tool_result = parse_tool_result_status(text)
        if tool_result:
            result_payload = (
                parse_tool_result_payload(text, tool_name=last_tool_name_ref[0]) or {}
            )
            yield StreamEvent(
                kind="tool_result",
                text=tool_result,
                payload=ctx.enrich(result_payload) if ctx else result_payload,
            )

            # Check for HITL triggers
            hitl_req = try_parse_hitl_request(
                tool_name=last_tool_name_ref[0],
                payload=result_payload,
            )
            if hitl_req:
                if ctx:
                    hitl_req.payload = ctx.enrich(hitl_req.payload)
                yield hitl_req


def _emit_prediction_trajectory_events(
    final_prediction: dspy.Prediction,
    ctx: StreamingContext | None = None,
) -> Iterable[StreamEvent]:
    trajectory = getattr(final_prediction, "trajectory", {})
    if not trajectory or not isinstance(trajectory, dict):
        return

    steps = _normalize_trajectory(trajectory)
    if not steps:
        return

    for idx, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        step_text = step.get("thought", step.get("action", str(step)))
        step_payload: dict[str, Any] = {
            "step_index": idx,
            "step_data": step,
            "total_steps": len(steps),
        }
        yield StreamEvent(
            kind="trajectory_step",
            flush_tokens=True,
            text=step_text,
            payload=ctx.enrich(step_payload) if ctx else step_payload,
        )


def _build_stream_program(
    agent: RLMReActChatAgent,
    *,
    trace: bool,
    is_async_program: bool,
    async_streaming: bool,
    stream_listeners: list[StreamListener],
) -> Any:
    return cast(
        Any,
        dspy.streamify(
            agent.react,
            status_message_provider=ReActStatusProvider(),
            stream_listeners=stream_listeners,
            include_final_prediction_in_output_stream=True,
            is_async_program=is_async_program,
            async_streaming=async_streaming,
        ),
    )


def _activate_live_event_queue(agent: RLMReActChatAgent) -> _ActiveStreamingTurn:
    state = _ActiveStreamingTurn()
    state.previous_live_callback = getattr(agent, "_live_event_callback", None)

    def _queue_live_event(event: StreamEvent) -> None:
        if not isinstance(event, StreamEvent):
            return
        if event.kind in {"final", "cancelled", "error"}:
            return
        state.pending_live_events.append(event)

    agent._live_event_callback = _queue_live_event
    return state


def _restore_live_event_queue(
    agent: RLMReActChatAgent,
    state: _ActiveStreamingTurn,
) -> None:
    agent._live_event_callback = state.previous_live_callback


def _handle_stream_value(
    *,
    value: Any,
    trace: bool,
    state: _ActiveStreamingTurn,
    ctx: StreamingContext,
) -> Iterable[StreamEvent]:
    if isinstance(value, dspy.Prediction):
        state.final_prediction = value
        yield from _emit_prediction_trajectory_events(value, ctx)
        return

    yield from _process_stream_value(
        value=value,
        trace=trace,
        assistant_chunks=state.assistant_chunks,
        last_tool_name_ref=state.last_tool_name_ref,
        ctx=ctx,
    )


def iter_chat_turn_stream(
    agent: RLMReActChatAgent,
    message: str,
    trace: bool,
    cancel_check: Callable[[], bool] | None = None,
) -> Iterable[StreamEvent]:
    """Yield typed streaming events for one chat turn (synchronous).

    This is the canonical streaming surface used by interactive runtimes.
    """
    prepared_turn = prepare_streaming_turn(agent, message=message, trace=trace)
    effective_max_iters = prepared_turn.effective_max_iters
    ctx = prepared_turn.ctx
    stream_listeners = prepared_turn.stream_listeners

    try:
        stream_program = _build_stream_program(
            agent,
            trace=trace,
            is_async_program=False,
            async_streaming=False,
            stream_listeners=stream_listeners,
        )
    except Exception as exc:
        logger.warning(
            "Streaming init failed, falling back: %s",
            exc,
            exc_info=True,
            extra={"error_type": type(exc).__name__},
        )
        fallback = agent.chat_turn(message)
        yield from _build_fallback_events(
            fallback=fallback,
            exc=exc,
            effective_max_iters=effective_max_iters,
            agent=agent,
            init_phase=True,
            ctx=ctx,
        )
        return

    state = _activate_live_event_queue(agent)

    try:
        with build_dspy_context(allow_tool_async_sync_conversion=True):
            stream = stream_program(
                user_request=message,
                history=agent.history,
                core_memory=agent.fmt_core_memory(),
                max_iters=effective_max_iters,
            )
            for value in stream:
                if cancel_check is not None and cancel_check():
                    yield build_cancelled_stream_event(
                        agent=agent,
                        message=message,
                        assistant_chunks=state.assistant_chunks,
                        ctx=ctx,
                    )
                    return

                yield from _drain_live_events(state.pending_live_events)
                yield from _handle_stream_value(
                    value=value,
                    trace=trace,
                    state=state,
                    ctx=ctx,
                )
    except Exception as exc:
        logger.error(
            "Streaming error, falling back: %s",
            exc,
            exc_info=True,
            extra={"error_type": type(exc).__name__},
        )
        fallback = agent.chat_turn(message)
        yield from _build_fallback_events(
            fallback=fallback,
            exc=exc,
            effective_max_iters=effective_max_iters,
            agent=agent,
            init_phase=False,
            ctx=ctx,
        )
        return
    finally:
        _restore_live_event_queue(agent, state)

    yield from _drain_live_events(state.pending_live_events)
    yield build_final_stream_event(
        agent=agent,
        message=message,
        final_prediction=state.final_prediction,
        assistant_chunks=state.assistant_chunks,
        ctx=ctx,
    )


# ---------------------------------------------------------------------------
# Asynchronous streaming
# ---------------------------------------------------------------------------


async def aiter_chat_turn_stream(
    agent: RLMReActChatAgent,
    message: str,
    trace: bool,
    cancel_check: Callable[[], bool] | None = None,
) -> AsyncIterator[StreamEvent]:
    """Yield typed streaming events for one chat turn (async).

    This is the canonical async streaming surface used by the FastAPI
    WebSocket endpoint.  Yields the same ``StreamEvent`` types as
    :func:`iter_chat_turn_stream`.
    """
    prepared_turn = prepare_streaming_turn(agent, message=message, trace=trace)
    effective_max_iters = prepared_turn.effective_max_iters
    ctx = prepared_turn.ctx
    stream_listeners = prepared_turn.stream_listeners

    try:
        stream_program = _build_stream_program(
            agent,
            trace=trace,
            is_async_program=True,
            async_streaming=True,
            stream_listeners=stream_listeners,
        )
    except Exception as exc:
        logger.warning(
            "Async streaming init failed, falling back: %s",
            exc,
            exc_info=True,
            extra={"error_type": type(exc).__name__},
        )
        fallback = await agent.achat_turn(message)
        for event in _build_fallback_events(
            fallback=fallback,
            exc=exc,
            effective_max_iters=effective_max_iters,
            agent=agent,
            init_phase=True,
            ctx=ctx,
        ):
            yield event
        return

    state = _activate_live_event_queue(agent)

    try:
        output_stream = stream_program(
            user_request=message,
            history=agent.history,
            core_memory=agent.fmt_core_memory(),
            max_iters=effective_max_iters,
        )
        async for value in output_stream:
            if cancel_check is not None and cancel_check():
                yield build_cancelled_stream_event(
                    agent=agent,
                    message=message,
                    assistant_chunks=state.assistant_chunks,
                    ctx=ctx,
                )
                return

            for event in _drain_live_events(state.pending_live_events):
                yield event

            for event in _handle_stream_value(
                value=value,
                trace=trace,
                state=state,
                ctx=ctx,
            ):
                yield event
    except Exception as exc:
        logger.error(
            "Async streaming error, falling back: %s",
            exc,
            exc_info=True,
            extra={"error_type": type(exc).__name__},
        )
        fallback = await agent.achat_turn(message)
        for event in _build_fallback_events(
            fallback=fallback,
            exc=exc,
            effective_max_iters=effective_max_iters,
            agent=agent,
            init_phase=False,
            ctx=ctx,
        ):
            yield event
        return
    finally:
        _restore_live_event_queue(agent, state)

    for event in _drain_live_events(state.pending_live_events):
        yield event
    yield build_final_stream_event(
        agent=agent,
        message=message,
        final_prediction=state.final_prediction,
        assistant_chunks=state.assistant_chunks,
        ctx=ctx,
    )
