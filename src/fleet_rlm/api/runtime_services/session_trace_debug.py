"""Session-scoped MLflow trace debugging helpers for the workspace UI."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Literal

from fastapi import HTTPException

from fleet_rlm.integrations.database.repository_identity import IdentityUpsertResult
from fleet_rlm.integrations.persistence_protocol import UnsupportedLocalCapabilityError

from ..schemas.sessions import (
    SessionTraceDebugResponse,
    SessionTraceDebugSpan,
    SessionTracePerformanceSpanSummary,
    SessionTracePerformanceSummary,
)
from .session_helpers import optional_string
from .session_trace_export import (
    MLFLOW_EXPORT_MAX_RESULTS,
    _trace_info_payload,
    _trace_owned_by_session,
    ordered_runtime_session_ids,
    resolve_owned_chat_session,
)

ResolvedTraceSource = Literal["trace_id", "client_request_id", "session_row", "runtime_session_id"]
MappedRenderKind = Literal[
    "assistant_text",
    "reasoning",
    "tool",
    "sandbox",
    "status_note",
    "non_rendered",
]

logger = logging.getLogger(__name__)


def _truncate_text(value: str | None, *, max_chars: int = 240) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    if len(text) <= max_chars:
        return text
    return f"{text[: max_chars - 3]}..."


def _preview_value(value: Any, *, max_chars: int = 240) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return _truncate_text(value, max_chars=max_chars)
    try:
        rendered = json.dumps(value, ensure_ascii=True, sort_keys=True)
    except Exception:
        rendered = str(value)
    return _truncate_text(rendered, max_chars=max_chars)


def _unix_nano_string(value: Any) -> str | None:
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _span_attributes(span: dict[str, Any]) -> dict[str, Any]:
    attributes = span.get("attributes")
    return attributes if isinstance(attributes, dict) else {}


def _span_status_code(span: dict[str, Any]) -> str | None:
    status = span.get("status")
    if isinstance(status, dict):
        return optional_string(status.get("code"))
    return None


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.strip():
        try:
            return int(float(value.strip()))
        except ValueError:
            return None
    return None


def _span_timestamp(value: Any) -> int | None:
    return _int_or_none(value)


def _span_duration_ms(span: dict[str, Any]) -> int | None:
    start = _span_timestamp(span.get("start_time_unix_nano"))
    end = _span_timestamp(span.get("end_time_unix_nano"))
    if start is None or end is None or end <= start:
        return None
    return int((end - start) / 1_000_000)


def _jsonish_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return value
    if text[0] not in '[{"':
        return value
    try:
        return json.loads(text)
    except Exception:
        return value


def _span_inputs(span: dict[str, Any]) -> Any:
    if "inputs" in span:
        return _jsonish_value(span.get("inputs"))
    return _jsonish_value(_span_attributes(span).get("mlflow.spanInputs"))


def _span_outputs(span: dict[str, Any]) -> Any:
    if "outputs" in span:
        return _jsonish_value(span.get("outputs"))
    return _jsonish_value(_span_attributes(span).get("mlflow.spanOutputs"))


def _output_chars(span: dict[str, Any]) -> int | None:
    output = _span_outputs(span)
    if output is None:
        return None
    if isinstance(output, str):
        return len(output)
    try:
        return len(json.dumps(output, ensure_ascii=True, sort_keys=True))
    except Exception:
        return len(str(output))


def _token_usage(span: dict[str, Any]) -> tuple[int | None, int | None, int | None]:
    attributes = _span_attributes(span)
    usage = _jsonish_value(attributes.get("mlflow.chat.tokenUsage"))
    if not isinstance(usage, dict):
        usage = _jsonish_value(attributes.get("mlflow.chat.tokenUsageJson"))
    if not isinstance(usage, dict):
        usage = {}

    input_tokens = _int_or_none(
        usage.get("input_tokens")
        or usage.get("prompt_tokens")
        or usage.get("inputTokens")
        or attributes.get("mlflow.chat.inputTokens")
    )
    output_tokens = _int_or_none(
        usage.get("output_tokens")
        or usage.get("completion_tokens")
        or usage.get("outputTokens")
        or attributes.get("mlflow.chat.outputTokens")
    )
    total_tokens = _int_or_none(
        usage.get("total_tokens") or usage.get("totalTokens") or attributes.get("mlflow.chat.totalTokens")
    )
    if total_tokens is None and (input_tokens is not None or output_tokens is not None):
        total_tokens = int(input_tokens or 0) + int(output_tokens or 0)
    return input_tokens, output_tokens, total_tokens


def _fallback_reason(span: dict[str, Any]) -> str | None:
    haystack_parts = [
        optional_string(span.get("name")),
        optional_string(_span_status_code(span)),
        optional_string(span.get("status", {}).get("message") if isinstance(span.get("status"), dict) else None),
        _preview_value(_span_inputs(span), max_chars=500),
        _preview_value(_span_outputs(span), max_chars=500),
        _preview_value(_span_attributes(span), max_chars=500),
    ]
    haystack = " ".join(part for part in haystack_parts if part).lower()
    if "adapterparseerror" in haystack or "failed to parse" in haystack or "parse error" in haystack:
        return "adapter_parse_error"
    if "jsonadapter" in haystack and ("fallback" in haystack or "retry" in haystack):
        return "json_adapter_fallback"
    if "chatadapter" in haystack and ("fallback" in haystack or "retry" in haystack):
        return "chat_adapter_fallback"
    if "fallback" in haystack and "adapter" in haystack:
        return "adapter_fallback"
    return None


def _span_summary(span: dict[str, Any]) -> SessionTracePerformanceSpanSummary:
    input_tokens, output_tokens, total_tokens = _token_usage(span)
    return SessionTracePerformanceSpanSummary(
        span_id=str(span.get("span_id") or ""),
        name=str(span.get("name") or "unknown"),
        duration_ms=_span_duration_ms(span),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        output_chars=_output_chars(span),
    )


def _csv_attr_values(value: Any) -> list[str]:
    text = optional_string(value)
    if not text:
        return []
    return [item.strip() for item in text.split(",") if item.strip()]


def _build_performance_summary(spans: list[dict[str, Any]]) -> SessionTracePerformanceSummary:
    llm_duration = 0
    repl_duration = 0
    tool_duration = 0
    input_tokens_total = 0
    output_tokens_total = 0
    total_tokens_total = 0
    parse_error_count = 0
    fallback_count = 0
    selected_skills: list[str] = []
    action_max_tokens: int | None = None
    max_output_chars: int | None = None
    root_duration: int | None = None
    slowest_llm: dict[str, Any] | None = None
    largest_output: dict[str, Any] | None = None

    for span in spans:
        duration = _span_duration_ms(span) or 0
        span_type = (_span_type(span) or "").upper()
        name = str(span.get("name") or "")
        attributes = _span_attributes(span)

        if span.get("parent_span_id") is None and duration:
            root_duration = duration if root_duration is None else max(root_duration, duration)

        if span_type in {"LLM", "CHAT_MODEL"} or name == "LM.__call__":
            llm_duration += duration
            if slowest_llm is None or duration > (_span_duration_ms(slowest_llm) or 0):
                slowest_llm = span
        elif span_type == "TOOL" and "repl" in name.lower():
            repl_duration += duration
        elif span_type == "TOOL":
            tool_duration += duration

        input_tokens, output_tokens, total_tokens = _token_usage(span)
        input_tokens_total += int(input_tokens or 0)
        output_tokens_total += int(output_tokens or 0)
        total_tokens_total += int(total_tokens or 0)

        output_chars = _output_chars(span) or 0
        if largest_output is None or output_chars > (_output_chars(largest_output) or 0):
            largest_output = span

        reason = _fallback_reason(span)
        if reason is not None:
            if "parse" in reason:
                parse_error_count += 1
            if "fallback" in reason or "retry" in reason or reason == "adapter_parse_error":
                fallback_count += 1

        for skill in _csv_attr_values(attributes.get("fleet_rlm.selected_skills")):
            if skill not in selected_skills:
                selected_skills.append(skill)

        action_max_tokens = action_max_tokens or _int_or_none(attributes.get("fleet_rlm.rlm_action_max_tokens"))
        max_output_chars = max_output_chars or _int_or_none(attributes.get("fleet_rlm.rlm_max_output_chars"))

    known_duration = llm_duration + repl_duration + tool_duration
    root_overhead = None
    if root_duration is not None:
        root_overhead = max(0, root_duration - known_duration)

    expected_total = input_tokens_total + output_tokens_total
    token_total_mismatch = bool(total_tokens_total and total_tokens_total != expected_total)

    return SessionTracePerformanceSummary(
        total_duration_ms=root_duration,
        llm_duration_ms=llm_duration,
        repl_duration_ms=repl_duration,
        tool_duration_ms=tool_duration,
        root_overhead_ms=root_overhead,
        input_tokens=input_tokens_total,
        output_tokens=output_tokens_total,
        total_tokens=total_tokens_total or expected_total,
        token_total_mismatch=token_total_mismatch,
        adapter_fallback_count=fallback_count,
        parse_error_count=parse_error_count,
        selected_skills=selected_skills,
        rlm_action_max_tokens=action_max_tokens,
        rlm_max_output_chars=max_output_chars,
        slowest_llm_span=_span_summary(slowest_llm) if slowest_llm is not None else None,
        largest_output_span=_span_summary(largest_output) if largest_output is not None else None,
    )


def _span_type(span: dict[str, Any]) -> str | None:
    attributes = _span_attributes(span)
    return optional_string(span.get("span_type")) or optional_string(attributes.get("mlflow.spanType"))


def _component_type_hint(tool_name: str) -> str:
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


def _classify_span(span: dict[str, Any]) -> tuple[MappedRenderKind, str | None, str, str | None]:
    name = optional_string(span.get("name")) or "unknown"
    span_type = (_span_type(span) or "").upper()
    attributes = _span_attributes(span)
    tool_name = optional_string(attributes.get("mlflow.spanFunctionName")) or name

    if span_type == "TOOL":
        component_type = _component_type_hint(tool_name)
        if component_type == "tool-Bash":
            return (
                "sandbox",
                component_type,
                "TOOL span executes REPL or shell code and is rendered as a sandbox/Bash tool card.",
                tool_name,
            )
        return (
            "tool",
            component_type,
            "TOOL span is rendered as a concrete tool card using the frontend tool classification rules.",
            tool_name,
        )

    if span_type in {"LLM", "CHAT_MODEL"}:
        if name == "rlm_available_tools":
            return (
                "non_rendered",
                None,
                "This span records available tool schemas for MLflow judges and is intentionally not shown in the chat transcript.",
                None,
            )
        return (
            "non_rendered",
            None,
            "LLM/chat-model spans are observability-only; the chat transcript renders runtime-emitted reasoning and assistant text instead.",
            None,
        )

    if span_type in {
        "AGENT",
        "WORKFLOW",
        "TASK",
        "CHAIN",
        "MEMORY",
        "RETRIEVER",
        "EMBEDDING",
        "RERANKER",
        "PARSER",
        "GUARDRAIL",
        "EVALUATOR",
    }:
        return (
            "non_rendered",
            None,
            "This orchestration span provides trace context but does not map to a standalone chat transcript component.",
            None,
        )

    status_code = _span_status_code(span)
    if status_code == "STATUS_CODE_ERROR":
        return (
            "status_note",
            "tool-Status",
            "Error-only span is treated as a status/error note when surfaced in the transcript.",
            None,
        )

    return (
        "non_rendered",
        None,
        "Span does not correspond to a dedicated chat component under the current websocket contract.",
        None,
    )


def _trace_spans(trace: Any) -> list[dict[str, Any]]:
    to_dict = getattr(trace, "to_dict", None)
    if callable(to_dict):
        try:
            payload = to_dict()
        except Exception:
            payload = None
        if isinstance(payload, dict):
            data = payload.get("data")
            if isinstance(data, dict):
                spans = data.get("spans")
                if isinstance(spans, list):
                    return [span for span in spans if isinstance(span, dict)]

    data = getattr(trace, "data", None)
    spans = getattr(data, "spans", None)
    if isinstance(spans, list):
        normalized: list[dict[str, Any]] = []
        for span in spans:
            if isinstance(span, dict):
                normalized.append(span)
                continue
            if hasattr(span, "to_dict"):
                try:
                    payload = span.to_dict()
                except Exception:
                    payload = None
                if isinstance(payload, dict):
                    normalized.append(payload)
        return normalized
    return []


def _trace_info(trace: Any) -> dict[str, Any]:
    info = _trace_info_payload(trace)
    if info:
        return info
    to_dict = getattr(trace, "to_dict", None)
    if callable(to_dict):
        try:
            payload = to_dict()
        except Exception:
            payload = None
        if isinstance(payload, dict):
            raw_info = payload.get("info")
            if isinstance(raw_info, dict):
                return raw_info
    return {}


def build_session_trace_debug_response(
    *,
    trace: Any,
    resolved_from: ResolvedTraceSource,
    runtime_session_id: str | None = None,
) -> SessionTraceDebugResponse:
    info = _trace_info(trace)
    spans = _trace_spans(trace)
    mapped_spans: list[SessionTraceDebugSpan] = []
    renderable_count = 0

    for span in spans:
        mapped_render_kind, mapped_component_type, rationale, tool_name = _classify_span(span)
        if mapped_render_kind != "non_rendered":
            renderable_count += 1
        mapped_spans.append(
            SessionTraceDebugSpan(
                span_id=str(span.get("span_id") or ""),
                parent_span_id=optional_string(span.get("parent_span_id")),
                name=str(span.get("name") or "unknown"),
                span_type=_span_type(span),
                status_code=_span_status_code(span),
                tool_name=tool_name,
                mapped_render_kind=mapped_render_kind,
                mapped_component_type=mapped_component_type,
                rationale=rationale,
                input_preview=_preview_value(_span_inputs(span)),
                output_preview=_preview_value(_span_outputs(span)),
                start_time_unix_nano=_unix_nano_string(span.get("start_time_unix_nano")),
                end_time_unix_nano=_unix_nano_string(span.get("end_time_unix_nano")),
                duration_ms=_span_duration_ms(span),
                input_tokens=_token_usage(span)[0],
                output_tokens=_token_usage(span)[1],
                total_tokens=_token_usage(span)[2],
                output_chars=_output_chars(span),
                retry_or_fallback_reason=_fallback_reason(span),
            )
        )

    trace_id = optional_string(info.get("trace_id"))
    if trace_id is None:
        raise HTTPException(status_code=503, detail="Resolved MLflow trace is missing a trace id.")

    return SessionTraceDebugResponse(
        trace_id=trace_id,
        client_request_id=optional_string(info.get("client_request_id")),
        state=optional_string(info.get("state")),
        request_preview=_truncate_text(optional_string(info.get("request_preview"))),
        response_preview=_truncate_text(optional_string(info.get("response_preview"))),
        resolved_from=resolved_from,
        runtime_session_id=runtime_session_id,
        span_count=len(mapped_spans),
        renderable_span_count=renderable_count,
        non_rendered_span_count=len(mapped_spans) - renderable_count,
        performance_summary=_build_performance_summary(spans),
        spans=mapped_spans,
    )


async def get_owned_session_trace_debug(
    *,
    persistence: Any,
    persisted_identity: IdentityUpsertResult,
    session_id: str,
    trace_id: str | None = None,
    client_request_id: str | None = None,
) -> SessionTraceDebugResponse:
    # Lazy import: MLflow trace resolution pulls observability/runtime dependencies.
    from fleet_rlm.integrations.observability.mlflow_traces import (
        resolve_trace,
        search_traces_by_session_id,
    )

    session = await resolve_owned_chat_session(
        persistence=persistence,
        persisted_identity=persisted_identity,
        session_id=session_id,
    )

    explicit_trace_id = optional_string(trace_id)
    explicit_client_request_id = optional_string(client_request_id)
    if explicit_trace_id or explicit_client_request_id:
        try:
            trace = await asyncio.to_thread(
                resolve_trace,
                trace_id=explicit_trace_id,
                client_request_id=explicit_client_request_id,
            )
        except Exception as exc:
            logger.exception("Failed to resolve MLflow trace for explicit trace debug request.")
            raise HTTPException(status_code=503, detail="Failed to resolve MLflow trace.") from exc
        if trace is None:
            raise HTTPException(status_code=404, detail="Unable to resolve an MLflow trace for this session.")
        if not _trace_owned_by_session(
            trace,
            session=session,
            persisted_identity=persisted_identity,
        ):
            raise HTTPException(status_code=403, detail="Resolved MLflow trace is not authorized for this session.")
        return build_session_trace_debug_response(
            trace=trace,
            resolved_from="trace_id" if explicit_trace_id else "client_request_id",
        )

    session_uuid = session.id
    external_trace_lookup_supported = True
    try:
        trace_rows, _total = await persistence.list_external_traces_for_session(
            tenant_id=persisted_identity.tenant_id,
            session_id=session_uuid,
            workspace_id=persisted_identity.workspace_id,
            limit=50,
            offset=0,
        )
    except (UnsupportedLocalCapabilityError, NotImplementedError):
        trace_rows = []
        external_trace_lookup_supported = False

    for trace_row in trace_rows:
        row_trace_id = optional_string(getattr(trace_row, "trace_id", None))
        row_client_request_id = optional_string(getattr(trace_row, "client_request_id", None))
        if not row_trace_id and not row_client_request_id:
            continue
        try:
            trace = await asyncio.to_thread(
                resolve_trace,
                trace_id=row_trace_id,
                client_request_id=row_client_request_id,
            )
        except Exception as exc:
            logger.exception("Failed to resolve MLflow trace for session trace row.")
            raise HTTPException(status_code=503, detail="Failed to resolve MLflow trace.") from exc
        if trace is None:
            continue
        if not _trace_owned_by_session(
            trace,
            session=session,
            persisted_identity=persisted_identity,
        ):
            continue
        return build_session_trace_debug_response(trace=trace, resolved_from="session_row")

    runtime_session_ids = ordered_runtime_session_ids(
        session=session,
        persisted_identity=persisted_identity,
        mlflow_session_id_hint=None,
    )
    for runtime_session_id in runtime_session_ids:
        try:
            traces = await asyncio.to_thread(
                search_traces_by_session_id,
                runtime_session_id,
                allow_unfiltered_fallback=False,
                max_results=MLFLOW_EXPORT_MAX_RESULTS,
            )
        except Exception as exc:
            logger.exception("Failed to search MLflow traces for runtime session.")
            raise HTTPException(status_code=503, detail="Failed to search MLflow traces.") from exc
        for trace in traces:
            if not _trace_owned_by_session(
                trace,
                session=session,
                persisted_identity=persisted_identity,
            ):
                continue
            return build_session_trace_debug_response(
                trace=trace,
                resolved_from="runtime_session_id",
                runtime_session_id=runtime_session_id,
            )

    if external_trace_lookup_supported:
        raise HTTPException(status_code=404, detail="No resolvable MLflow traces are linked to this session.")
    raise HTTPException(
        status_code=404,
        detail="No resolvable MLflow traces were found for this session or its runtime session ids.",
    )
