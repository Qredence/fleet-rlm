"""Streaming orchestration for the RLM ReAct chat agent.

Provides synchronous and asynchronous streaming iterators that yield
:class:`~fleet_rlm.models.StreamEvent` objects, plus a DSPy
:class:`StatusMessageProvider` for concise ReAct status messages.
"""

from __future__ import annotations

import logging
import json
import re
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any, Callable, Iterable, cast

import dspy
from dspy.streaming.messages import StatusMessage, StatusMessageProvider, StreamResponse
from dspy.streaming.streaming_listener import StreamListener

from ..models import StreamEvent

if TYPE_CHECKING:
    from .agent import RLMReActChatAgent

logger = logging.getLogger(__name__)
STREAM_EVENT_SCHEMA_VERSION = 2

# ---------------------------------------------------------------------------
# Status-message parsing helpers
# ---------------------------------------------------------------------------


def parse_tool_call_status(message: str) -> str | None:
    """Extract a tool-call description from a DSPy status message."""
    match = re.match(r"^Calling tool:\s*(.+)$", message.strip())
    if not match:
        return None
    return f"tool call: {match.group(1).strip()}"


def parse_tool_call_payload(message: str) -> dict[str, Any] | None:
    """Extract structured tool-call metadata from a status message."""
    match = re.match(r"^Calling tool:\s*(.+)$", message.strip())
    if not match:
        return None

    raw_call = match.group(1).strip()
    tool_name = raw_call.split("(", 1)[0].strip() if raw_call else ""
    args_snippet = ""
    if "(" in raw_call:
        args_snippet = raw_call.split("(", 1)[1].rsplit(")", 1)[0].strip()

    payload: dict[str, Any] = {
        "raw_status": message,
        "raw_call": raw_call,
    }
    if tool_name:
        payload["tool_name"] = tool_name
    if args_snippet:
        payload["tool_args"] = args_snippet
        payload["tool_input"] = args_snippet
    return payload


def parse_tool_result_status(message: str) -> str | None:
    """Detect a tool-finished status message."""
    stripped = message.strip()
    if stripped == "Tool finished.":
        return "tool result: finished"
    if stripped.startswith("Tool result:"):
        return "tool result: completed"
    return None


def parse_tool_result_payload(
    message: str, *, tool_name: str | None
) -> dict[str, Any] | None:
    """Extract structured tool-result metadata from a status message."""
    stripped = message.strip()
    if stripped != "Tool finished." and not stripped.startswith("Tool result:"):
        return None

    payload: dict[str, Any] = {
        "raw_status": message,
    }
    if tool_name:
        payload["tool_name"] = tool_name
    if stripped.startswith("Tool result:"):
        result_text = stripped.removeprefix("Tool result:").strip()
        if result_text:
            payload["tool_output"] = result_text
    return payload


def _normalize_trajectory(raw: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Convert DSPy ReAct flat trajectory to structured step list.

    DSPy 3.1.3 ReAct returns: {"thought_0": ..., "tool_name_0": ..., "input_0": ..., "output_0": ...}
    We normalize to: [{"index": 0, "thought": ..., "tool_name": ..., ...}, ...]

    Args:
        raw: Optional trajectory payload from DSPy ReAct.

    Returns:
        List of step dictionaries, each with an "index" field and relevant data fields.
    """
    if not raw:
        return []
    # If already structured (future DSPy versions), pass through
    if "steps" in raw and isinstance(raw["steps"], list):
        return raw["steps"]
    # Legacy structured shape used by some DSPy outputs
    if "trajectory" in raw and isinstance(raw["trajectory"], list):
        return raw["trajectory"]

    # Extract step indices from keys like "thought_0", "tool_name_1"
    indices: set[int] = set()
    for key in raw:
        parts = key.rsplit("_", 1)
        if len(parts) == 2 and parts[1].isdigit():
            indices.add(int(parts[1]))

    steps = []
    for i in sorted(indices):
        step: dict[str, Any] = {"index": i}
        for field in ("thought", "tool_name", "input", "output", "observation"):
            val = raw.get(f"{field}_{i}")
            if val is not None:
                step[field] = val
        steps.append(step)
    return steps


def _as_text(value: Any) -> str | None:
    if isinstance(value, str):
        trimmed = value.strip()
        return trimmed or None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return None


def _normalize_citation_entry(item: Any, *, index: int) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None

    url = _as_text(
        item.get("url") or item.get("source_url") or item.get("canonical_url")
    )
    if not url:
        return None

    number = _as_text(item.get("number")) or str(index + 1)
    title = (
        _as_text(item.get("title"))
        or _as_text(item.get("source_title"))
        or _as_text(item.get("source"))
        or f"Source {number}"
    )
    source_id = _as_text(item.get("source_id")) or f"source-{index + 1}"
    anchor_id = _as_text(item.get("anchor_id")) or f"anchor-{index + 1}"

    normalized: dict[str, Any] = {
        "number": number,
        "title": title,
        "url": url,
        "source_id": source_id,
        "anchor_id": anchor_id,
    }
    if _as_text(item.get("description")):
        normalized["description"] = _as_text(item.get("description"))
    if _as_text(item.get("quote")):
        normalized["quote"] = _as_text(item.get("quote"))
    if _as_text(item.get("evidence")) and "quote" not in normalized:
        normalized["quote"] = _as_text(item.get("evidence"))
    if isinstance(item.get("start_char"), int):
        normalized["start_char"] = item["start_char"]
    if isinstance(item.get("end_char"), int):
        normalized["end_char"] = item["end_char"]
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
        except Exception:
            return []
        return _extract_citations_from_output(parsed)

    return []


def _extract_final_citations(
    *, final_prediction: dspy.Prediction | None, trajectory: dict[str, Any]
) -> list[dict[str, Any]]:
    candidate_lists: list[list[dict[str, Any]]] = []

    if final_prediction is not None:
        raw_from_prediction = getattr(final_prediction, "citations", None)
        if isinstance(raw_from_prediction, list):
            candidate_lists.append(
                [item for item in raw_from_prediction if isinstance(item, dict)]
            )

    raw_from_trajectory = trajectory.get("citations")
    if isinstance(raw_from_trajectory, list):
        candidate_lists.append(
            [item for item in raw_from_trajectory if isinstance(item, dict)]
        )

    for step in _normalize_trajectory(trajectory):
        if not isinstance(step, dict):
            continue
        candidate = _extract_citations_from_output(step.get("output"))
        if candidate:
            candidate_lists.append(
                [item for item in candidate if isinstance(item, dict)]
            )

    merged: list[dict[str, Any]] = []
    for candidate in candidate_lists:
        merged.extend(candidate)

    normalized = []
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


def _build_sources_from_citations(
    citations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    sources_by_key: dict[str, dict[str, Any]] = {}
    for citation in citations:
        source_id = _as_text(citation.get("source_id")) or "source"
        url = _as_text(citation.get("url"))
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


def _extract_final_attachments(
    *, final_prediction: dspy.Prediction | None, trajectory: dict[str, Any]
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

    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for idx, item in enumerate(candidates):
        attachment_id = (
            _as_text(item.get("attachment_id") or item.get("id")) or f"att-{idx + 1}"
        )
        if attachment_id in seen:
            continue
        seen.add(attachment_id)
        normalized.append(
            {
                "attachment_id": attachment_id,
                "name": _as_text(item.get("name") or item.get("title")) or "Attachment",
                "url": _as_text(item.get("url") or item.get("download_url")),
                "preview_url": _as_text(item.get("preview_url")),
                "mime_type": _as_text(item.get("mime_type") or item.get("mimeType")),
                "media_type": _as_text(item.get("media_type") or item.get("mediaType")),
                "size_bytes": item.get("size_bytes")
                if isinstance(item.get("size_bytes"), int)
                else None,
                "kind": _as_text(item.get("kind")),
                "description": _as_text(item.get("description")),
            }
        )
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


# ---------------------------------------------------------------------------
# Status message provider
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Synchronous streaming
# ---------------------------------------------------------------------------


def iter_chat_turn_stream(
    agent: RLMReActChatAgent,
    message: str,
    trace: bool,
    cancel_check: Callable[[], bool] | None = None,
) -> Iterable[StreamEvent]:
    """Yield typed streaming events for one chat turn (synchronous).

    This is the canonical streaming surface used by interactive runtimes.
    """
    if not message or not message.strip():
        raise ValueError("message cannot be empty")

    agent.start()
    effective_max_iters = agent._prepare_turn(message)

    stream_listeners = [StreamListener(signature_field_name="assistant_response")]
    if trace:
        stream_listeners.append(
            StreamListener(signature_field_name="next_thought", allow_reuse=True)
        )

    try:
        stream_program = cast(
            Any,
            dspy.streamify(
                agent.react,
                status_message_provider=ReActStatusProvider(),
                stream_listeners=stream_listeners,
                include_final_prediction_in_output_stream=True,
                async_streaming=False,
            ),
        )
    except Exception as exc:
        logger.warning(
            "Streaming init failed, falling back: %s",
            exc,
            exc_info=True,
            extra={"error_type": type(exc).__name__},
        )
        fallback = agent.chat_turn(message)
        yield StreamEvent(
            kind="status",
            text=f"streaming unavailable; fell back to non-streaming ({exc})",
            payload={"fallback": True, "error_type": type(exc).__name__},
        )
        yield StreamEvent(
            kind="final",
            flush_tokens=True,
            text=str(fallback.get("assistant_response", "")),
            payload=_build_final_payload(
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
            ),
        )
        return

    assistant_chunks: list[str] = []
    final_prediction: dspy.Prediction | None = None
    last_tool_name: str | None = None

    try:
        stream = stream_program(
            user_request=message,
            history=agent.history,
            core_memory=agent.fmt_core_memory(),
            max_iters=effective_max_iters,
        )
        for value in stream:
            if cancel_check is not None and cancel_check():
                partial = "".join(assistant_chunks).strip()
                marked_partial = (
                    f"{partial}\n\n[cancelled]" if partial else "[cancelled]"
                )
                agent._append_history(message, marked_partial)
                yield StreamEvent(
                    kind="cancelled",
                    text=marked_partial,
                    payload={
                        "history_turns": agent.history_turns(),
                        **agent._turn_metrics(),
                    },
                )
                return

            if isinstance(value, StreamResponse):
                if value.signature_field_name == "assistant_response":
                    assistant_chunks.append(value.chunk)
                    yield StreamEvent(kind="assistant_token", text=value.chunk)
                elif value.signature_field_name == "next_thought" and trace:
                    yield StreamEvent(
                        kind="reasoning_step",
                        text=value.chunk,
                        payload={"source": "next_thought"},
                    )
            elif isinstance(value, StatusMessage):
                text = value.message
                yield StreamEvent(kind="status", text=text)
                tool_call = parse_tool_call_status(text)
                if tool_call:
                    tool_payload = parse_tool_call_payload(text) or {}
                    parsed_name = tool_payload.get("tool_name")
                    if isinstance(parsed_name, str) and parsed_name:
                        last_tool_name = parsed_name

                    event_kind = "tool_call"
                    if parsed_name == "plan_code_change":
                        event_kind = "plan_update"
                    elif parsed_name in (
                        "rlm_query",
                        "analyze_long_document",
                        "summarize_long_document",
                        "extract_from_logs",
                        "grounded_answer",
                        "triage_incident_logs",
                        "parallel_semantic_map",
                    ):
                        event_kind = "rlm_executing"
                    elif parsed_name and (
                        parsed_name.startswith("core_memory") or "memory" in parsed_name
                    ):
                        event_kind = "memory_update"

                    yield StreamEvent(
                        kind=event_kind,
                        text=tool_call,
                        payload=tool_payload,
                    )
                tool_result = parse_tool_result_status(text)
                if tool_result:
                    result_payload = (
                        parse_tool_result_payload(text, tool_name=last_tool_name) or {}
                    )
                    yield StreamEvent(
                        kind="tool_result",
                        text=tool_result,
                        payload=result_payload,
                    )
            elif isinstance(value, dspy.Prediction):
                final_prediction = value
                # Emit trajectory steps as they're captured
                trajectory = getattr(final_prediction, "trajectory", {})
                if trajectory and isinstance(trajectory, dict):
                    steps = _normalize_trajectory(trajectory)
                    if steps:
                        for idx, step in enumerate(steps):
                            if isinstance(step, dict):
                                step_text = step.get(
                                    "thought", step.get("action", str(step))
                                )
                                yield StreamEvent(
                                    kind="trajectory_step",
                                    flush_tokens=True,
                                    text=step_text,
                                    payload={
                                        "step_index": idx,
                                        "step_data": step,
                                        "total_steps": len(steps),
                                    },
                                )
    except Exception as exc:
        logger.error(
            "Streaming error, falling back: %s",
            exc,
            exc_info=True,
            extra={"error_type": type(exc).__name__},
        )
        fallback = agent.chat_turn(message)
        yield StreamEvent(
            kind="status",
            text=f"stream error; fell back to non-streaming ({exc})",
            payload={"fallback": True, "error_type": type(exc).__name__},
        )
        yield StreamEvent(
            kind="final",
            flush_tokens=True,
            text=str(fallback.get("assistant_response", "")),
            payload=_build_final_payload(
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
            ),
        )
        return

    if final_prediction is not None:
        assistant_response = str(
            getattr(final_prediction, "assistant_response", "")
        ).strip()
        trajectory = getattr(final_prediction, "trajectory", {})
        # Extract final reasoning/thinking
        final_reasoning = ""
        if hasattr(final_prediction, "reasoning"):
            final_reasoning = str(final_prediction.reasoning)
        elif trajectory and isinstance(trajectory, dict):
            # Try to construct reasoning from trajectory steps
            steps = _normalize_trajectory(trajectory)
            if steps:
                reasoning_parts = []
                for step in steps:
                    if isinstance(step, dict):
                        thought = step.get("thought", "")
                        if thought:
                            reasoning_parts.append(thought)
                final_reasoning = "\n".join(reasoning_parts)
    else:
        assistant_response = "".join(assistant_chunks).strip()
        trajectory = {}
        final_reasoning = ""

    agent._finalize_turn(trajectory)

    assistant_response, guardrail_warnings = agent._validate_assistant_response(
        assistant_response=assistant_response,
        trajectory=trajectory,
    )

    agent._append_history(message, assistant_response)
    yield StreamEvent(
        kind="final",
        flush_tokens=True,
        text=assistant_response,
        payload={
            **_build_final_payload(
                final_prediction=final_prediction,
                trajectory=cast(dict[str, Any], trajectory or {}),
                history_turns=agent.history_turns(),
                guardrail_warnings=guardrail_warnings,
                turn_metrics=agent._turn_metrics(),
                fallback=False,
            ),
            "final_reasoning": final_reasoning,
        },
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
    if not message or not message.strip():
        raise ValueError("message cannot be empty")

    agent.start()
    effective_max_iters = agent._prepare_turn(message)

    stream_listeners = [StreamListener(signature_field_name="assistant_response")]
    if trace:
        stream_listeners.append(
            StreamListener(signature_field_name="next_thought", allow_reuse=True)
        )

    try:
        stream_program = cast(
            Any,
            dspy.streamify(
                agent.react,
                status_message_provider=ReActStatusProvider(),
                stream_listeners=stream_listeners,
                include_final_prediction_in_output_stream=True,
                async_streaming=True,
            ),
        )
    except Exception as exc:
        logger.warning(
            "Async streaming init failed, falling back: %s",
            exc,
            exc_info=True,
            extra={"error_type": type(exc).__name__},
        )
        fallback = await agent.achat_turn(message)
        yield StreamEvent(
            kind="status",
            text=f"streaming unavailable; fell back to non-streaming ({exc})",
            payload={"fallback": True, "error_type": type(exc).__name__},
        )
        yield StreamEvent(
            kind="final",
            flush_tokens=True,
            text=str(fallback.get("assistant_response", "")),
            payload=_build_final_payload(
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
            ),
        )
        return

    assistant_chunks: list[str] = []
    final_prediction: dspy.Prediction | None = None

    try:
        output_stream = stream_program(
            user_request=message,
            history=agent.history,
            core_memory=agent.fmt_core_memory(),
            max_iters=effective_max_iters,
        )
        async for value in output_stream:
            if cancel_check is not None and cancel_check():
                partial = "".join(assistant_chunks).strip()
                marked_partial = (
                    f"{partial}\n\n[cancelled]" if partial else "[cancelled]"
                )
                agent._append_history(message, marked_partial)
                yield StreamEvent(
                    kind="cancelled",
                    text=marked_partial,
                    payload={
                        "history_turns": agent.history_turns(),
                        **agent._turn_metrics(),
                    },
                )
                return

            if isinstance(value, StreamResponse):
                if value.signature_field_name == "assistant_response":
                    assistant_chunks.append(value.chunk)
                    yield StreamEvent(kind="assistant_token", text=value.chunk)
                elif value.signature_field_name == "next_thought" and trace:
                    yield StreamEvent(
                        kind="reasoning_step",
                        text=value.chunk,
                        payload={"source": "next_thought"},
                    )
            elif isinstance(value, StatusMessage):
                text = value.message
                yield StreamEvent(kind="status", text=text)
                tool_call = parse_tool_call_status(text)
                if tool_call:
                    tool_payload = parse_tool_call_payload(text) or {}
                    parsed_name = tool_payload.get("tool_name")
                    if isinstance(parsed_name, str) and parsed_name:
                        last_tool_name = parsed_name

                    event_kind = "tool_call"
                    if parsed_name == "plan_code_change":
                        event_kind = "plan_update"
                    elif parsed_name in (
                        "rlm_query",
                        "analyze_long_document",
                        "summarize_long_document",
                        "extract_from_logs",
                        "grounded_answer",
                        "triage_incident_logs",
                        "parallel_semantic_map",
                    ):
                        event_kind = "rlm_executing"
                    elif parsed_name and (
                        parsed_name.startswith("core_memory") or "memory" in parsed_name
                    ):
                        event_kind = "memory_update"

                    yield StreamEvent(
                        kind=event_kind,
                        text=tool_call,
                        payload=tool_payload,
                    )
                tool_result = parse_tool_result_status(text)
                if tool_result:
                    result_payload = (
                        parse_tool_result_payload(text, tool_name=last_tool_name) or {}
                    )
                    yield StreamEvent(
                        kind="tool_result",
                        text=tool_result,
                        payload=result_payload,
                    )
            elif isinstance(value, dspy.Prediction):
                final_prediction = value
                # Emit trajectory steps as they're captured
                trajectory = getattr(final_prediction, "trajectory", {})
                if trajectory and isinstance(trajectory, dict):
                    steps = _normalize_trajectory(trajectory)
                    if steps:
                        for idx, step in enumerate(steps):
                            if isinstance(step, dict):
                                step_text = step.get(
                                    "thought", step.get("action", str(step))
                                )
                                yield StreamEvent(
                                    kind="trajectory_step",
                                    flush_tokens=True,
                                    text=step_text,
                                    payload={
                                        "step_index": idx,
                                        "step_data": step,
                                        "total_steps": len(steps),
                                    },
                                )
    except Exception as exc:
        logger.error(
            "Async streaming error, falling back: %s",
            exc,
            exc_info=True,
            extra={"error_type": type(exc).__name__},
        )
        fallback = await agent.achat_turn(message)
        yield StreamEvent(
            kind="status",
            text=f"stream error; fell back to non-streaming ({exc})",
            payload={"fallback": True, "error_type": type(exc).__name__},
        )
        yield StreamEvent(
            kind="final",
            flush_tokens=True,
            text=str(fallback.get("assistant_response", "")),
            payload=_build_final_payload(
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
            ),
        )
        return

    if final_prediction is not None:
        assistant_response = str(
            getattr(final_prediction, "assistant_response", "")
        ).strip()
        trajectory = getattr(final_prediction, "trajectory", {})
        # Extract final reasoning/thinking
        final_reasoning = ""
        if hasattr(final_prediction, "reasoning"):
            final_reasoning = str(final_prediction.reasoning)
        elif trajectory and isinstance(trajectory, dict):
            # Try to construct reasoning from trajectory steps
            steps = _normalize_trajectory(trajectory)
            if steps:
                reasoning_parts = []
                for step in steps:
                    if isinstance(step, dict):
                        thought = step.get("thought", "")
                        if thought:
                            reasoning_parts.append(thought)
                final_reasoning = "\n".join(reasoning_parts)
    else:
        assistant_response = "".join(assistant_chunks).strip()
        trajectory = {}
        final_reasoning = ""

    agent._finalize_turn(trajectory)

    assistant_response, guardrail_warnings = agent._validate_assistant_response(
        assistant_response=assistant_response,
        trajectory=trajectory,
    )

    agent._append_history(message, assistant_response)
    yield StreamEvent(
        kind="final",
        flush_tokens=True,
        text=assistant_response,
        payload={
            **_build_final_payload(
                final_prediction=final_prediction,
                trajectory=cast(dict[str, Any], trajectory or {}),
                history_turns=agent.history_turns(),
                guardrail_warnings=guardrail_warnings,
                turn_metrics=agent._turn_metrics(),
                fallback=False,
            ),
            "final_reasoning": final_reasoning,
        },
    )
