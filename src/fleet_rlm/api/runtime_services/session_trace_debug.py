"""Session-scoped MLflow trace debugging helpers for the workspace UI."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Literal

from fastapi import HTTPException

from fleet_rlm.integrations.database.repository_identity import IdentityUpsertResult
from fleet_rlm.integrations.persistence_protocol import UnsupportedLocalCapabilityError

from ..schemas.sessions import SessionTraceDebugResponse, SessionTraceDebugSpan
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


def _span_attributes(span: dict[str, Any]) -> dict[str, Any]:
    attributes = span.get("attributes")
    return attributes if isinstance(attributes, dict) else {}


def _span_status_code(span: dict[str, Any]) -> str | None:
    status = span.get("status")
    if isinstance(status, dict):
        return optional_string(status.get("code"))
    return None


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
                input_preview=_preview_value(span.get("inputs")),
                output_preview=_preview_value(span.get("outputs")),
                start_time_unix_nano=span.get("start_time_unix_nano")
                if isinstance(span.get("start_time_unix_nano"), int)
                else None,
                end_time_unix_nano=span.get("end_time_unix_nano")
                if isinstance(span.get("end_time_unix_nano"), int)
                else None,
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
            raise HTTPException(status_code=503, detail=f"Failed to resolve MLflow trace: {exc}") from exc
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
            raise HTTPException(status_code=503, detail=f"Failed to resolve MLflow trace: {exc}") from exc
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
            raise HTTPException(status_code=503, detail=f"Failed to search MLflow traces: {exc}") from exc
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
