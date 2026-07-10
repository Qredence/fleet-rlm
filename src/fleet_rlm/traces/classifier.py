"""Pure MLflow/OpenTelemetry span-to-workspace classification."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

MappedRenderKind = Literal[
    "assistant_text",
    "reasoning",
    "tool",
    "sandbox",
    "status_note",
    "artifact",
    "task",
    "performance",
    "mlflow_span",
    "non_rendered",
]


@dataclass(frozen=True, slots=True)
class SpanClassification:
    """A trace-only mapping decision; transports decide what to render."""

    render_kind: MappedRenderKind
    component_type: str | None
    rationale: str
    tool_name: str | None


def optional_string(value: Any) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def span_attributes(span: dict[str, Any]) -> dict[str, Any]:
    attributes = span.get("attributes")
    return attributes if isinstance(attributes, dict) else {}


def span_type(span: dict[str, Any]) -> str | None:
    attributes = span_attributes(span)
    return optional_string(span.get("span_type")) or optional_string(attributes.get("mlflow.spanType"))


def span_status_code(span: dict[str, Any]) -> str | None:
    status = span.get("status")
    if isinstance(status, dict):
        return optional_string(status.get("code"))
    return None


def _preview(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True)
    except (TypeError, ValueError):
        return str(value)


def fallback_reason(span: dict[str, Any]) -> str | None:
    """Detect known adapter fallback signals without performing any I/O."""
    attributes = span_attributes(span)
    status = span.get("status")
    status_message = status.get("message") if isinstance(status, dict) else None
    haystack = " ".join(
        part
        for part in (
            optional_string(span.get("name")),
            span_status_code(span),
            optional_string(status_message),
            _preview(span.get("inputs")),
            _preview(span.get("outputs")),
            _preview(attributes),
        )
        if part
    ).lower()
    if "adapterparseerror" in haystack or "failed to parse" in haystack or "parse error" in haystack:
        return "adapter_parse_error"
    if "jsonadapter" in haystack and ("fallback" in haystack or "retry" in haystack):
        return "json_adapter_fallback"
    if "chatadapter" in haystack and ("fallback" in haystack or "retry" in haystack):
        return "chat_adapter_fallback"
    if "fallback" in haystack and "adapter" in haystack:
        return "adapter_fallback"
    return None


def component_type_hint(tool_name: str) -> str:
    """Use the established workspace tool-card vocabulary."""
    normalized = tool_name.lower()
    if normalized.startswith("mcp__"):
        return f"tool-{tool_name}"
    if any(
        token in normalized
        for token in ("bash", "exec", "command", "terminal", "run", "shell", "python", "repl", "interpreter", "sandbox")
    ):
        return "tool-Bash"
    if any(
        token in normalized
        for token in (
            "load_document",
            "load-document",
            "read_file",
            "read-file",
            "read_document",
            "read-document",
            "open_document",
            "document_read",
            "file_read",
        )
    ):
        return "tool-Read"
    if any(
        token in normalized
        for token in ("list_files", "list-files", "list_dir", "glob", "tree", "directory_listing", "browse_files")
    ):
        return "tool-Glob"
    if "write" in normalized or "create_file" in normalized:
        return "tool-Write"
    if any(token in normalized for token in ("edit", "patch", "notebook")):
        return "tool-Edit"
    if any(token in normalized for token in ("grep", "find", "search")):
        return "tool-WebSearch" if "web" in normalized else "tool-Grep"
    if any(token in normalized for token in ("webfetch", "fetch", "browser", "url")):
        return "tool-WebFetch"
    if any(token in normalized for token in ("todo", "task_list")):
        return "tool-TodoWrite"
    if any(token in normalized for token in ("plan", "planning")):
        return "tool-PlanWrite"
    if any(token in normalized for token in ("delegate", "sub_rlm", "agent", "recursive")):
        return "tool-Agent"
    if any(token in normalized for token in ("think", "reason")):
        return "tool-Thinking"
    sanitized = "".join(
        segment[:1].upper() + segment[1:] for segment in tool_name.replace("-", "_").split("_") if segment
    )
    return f"tool-{sanitized or 'Tool'}"


def classify_span(span: dict[str, Any]) -> SpanClassification:
    """Classify one normalized trace span without changing old render kinds."""
    name = optional_string(span.get("name")) or "unknown"
    attributes = span_attributes(span)
    resolved_type = (span_type(span) or "").upper()
    event_kind = optional_string(attributes.get("event_kind") or attributes.get("fleet_rlm.event_kind"))
    tool_name = optional_string(attributes.get("mlflow.spanFunctionName")) or name

    if event_kind == "mlflow_span" or resolved_type == "MLFLOW_SPAN":
        return SpanClassification(
            "mlflow_span",
            "tool-TraceSpan",
            "Canonical runtime MLflow-span metadata is available for the trace sidepanel.",
            None,
        )
    if resolved_type == "ARTIFACT":
        return SpanClassification("artifact", "tool-Artifact", "Artifact span maps to an artifact card.", None)
    if resolved_type == "TASK":
        return SpanClassification("task", "tool-Task", "Task span maps to a task-status card.", None)
    if resolved_type == "PERFORMANCE":
        return SpanClassification(
            "performance",
            "tool-Performance",
            "Performance span maps to a performance summary card.",
            None,
        )
    if resolved_type == "TOOL":
        component_type = component_type_hint(tool_name)
        if component_type == "tool-Bash":
            return SpanClassification(
                "sandbox",
                component_type,
                "TOOL span executes REPL or shell code and maps to a sandbox/Bash tool card.",
                tool_name,
            )
        return SpanClassification(
            "tool",
            component_type,
            "TOOL span maps to a concrete tool card using workspace classification rules.",
            tool_name,
        )
    if resolved_type in {"LLM", "CHAT_MODEL"}:
        if name == "rlm_available_tools":
            rationale = "Available-tool schemas are observability-only and intentionally absent from the transcript."
        else:
            rationale = "LLM/chat-model spans are observability-only; runtime events render transcript content."
        return SpanClassification("non_rendered", None, rationale, None)
    if resolved_type in {
        "AGENT",
        "WORKFLOW",
        "CHAIN",
        "MEMORY",
        "RETRIEVER",
        "EMBEDDING",
        "RERANKER",
        "PARSER",
        "GUARDRAIL",
        "EVALUATOR",
    }:
        return SpanClassification(
            "non_rendered",
            None,
            "This orchestration span provides trace context but not a standalone transcript component.",
            None,
        )
    if span_status_code(span) == "STATUS_CODE_ERROR":
        return SpanClassification(
            "status_note",
            "tool-Status",
            "Error-only span is a status/error note when surfaced by the trace sidepanel.",
            None,
        )
    return SpanClassification(
        "non_rendered",
        None,
        "Span does not correspond to a dedicated workspace transcript component.",
        None,
    )


__all__ = [
    "MappedRenderKind",
    "SpanClassification",
    "classify_span",
    "component_type_hint",
    "fallback_reason",
    "optional_string",
    "span_attributes",
    "span_status_code",
    "span_type",
]
