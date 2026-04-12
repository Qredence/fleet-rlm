"""Event construction, status parsing, citation handling, and payload building
for the RLM ReAct chat agent streaming pipeline.

Extracted from :mod:`fleet_rlm.runtime.execution.streaming` to keep event
construction logic separate from stream orchestration.
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal
from urllib.parse import urlparse

import dspy
from dspy.streaming.messages import StatusMessageProvider

from fleet_rlm.runtime.models.streaming import StreamEvent

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


def _prediction_record(
    final_prediction: dspy.Prediction | None,
    field_name: str,
) -> dict[str, Any]:
    if final_prediction is None:
        return {}
    raw = getattr(final_prediction, field_name, None)
    return raw if isinstance(raw, dict) else {}


def _prediction_text(
    final_prediction: dspy.Prediction | None,
    field_name: str,
) -> str | None:
    if final_prediction is None:
        return None
    return _as_text(getattr(final_prediction, field_name, None))


def _prediction_text_list(
    final_prediction: dspy.Prediction | None,
    field_name: str,
) -> list[str]:
    if final_prediction is None:
        return []
    raw = getattr(final_prediction, field_name, None)
    if not isinstance(raw, list):
        return []
    return [item for item in (_as_text(entry) for entry in raw) if item]


def _build_human_review_payload(
    final_prediction: dspy.Prediction | None,
) -> dict[str, Any] | None:
    recursive_repair = _prediction_record(final_prediction, "recursive_repair")
    if _as_text(recursive_repair.get("repair_mode")) != "needs_human_review":
        return None

    repair_steps = _prediction_text_list(final_prediction, "repair_steps")
    if not repair_steps:
        raw_steps = recursive_repair.get("repair_steps")
        if isinstance(raw_steps, list):
            repair_steps = [item for item in (_as_text(entry) for entry in raw_steps) if item]

    reason = (
        _prediction_text(final_prediction, "final_reasoning")
        or _as_text(recursive_repair.get("repair_rationale"))
        or _as_text(recursive_repair.get("repair_target"))
        or "Recursive repair requested human review before continuing."
    )

    return {
        "required": True,
        "reason": reason,
        "repair_mode": "needs_human_review",
        "repair_target": _as_text(recursive_repair.get("repair_target")),
        "repair_steps": repair_steps,
    }


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
    recursive_repair = _prediction_record(final_prediction, "recursive_repair")
    if recursive_repair:
        payload["recursive_repair"] = recursive_repair
    human_review = _build_human_review_payload(final_prediction)
    if human_review is not None:
        payload["human_review"] = human_review
    if fallback_error_type:
        payload["error_type"] = fallback_error_type
    if effective_max_iters is not None:
        payload["effective_max_iters"] = effective_max_iters
    return payload
