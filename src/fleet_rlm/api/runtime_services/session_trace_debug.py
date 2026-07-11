"""Session-scoped MLflow trace debugging helpers for the workspace UI."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Literal

from fastapi import HTTPException

from fleet_rlm.db.repos.identity import IdentityUpsertResult
from fleet_rlm.integrations.persistence_protocol import UnsupportedLocalCapabilityError
from fleet_rlm.observability.token_usage import int_or_none as _pure_int_or_none
from fleet_rlm.traces.classifier import (
    MappedRenderKind,
)
from fleet_rlm.traces.classifier import (
    classify_span as _pure_classify_span,
)
from fleet_rlm.traces.classifier import (
    component_type_hint as _pure_component_type_hint,
)
from fleet_rlm.traces.classifier import (
    fallback_reason as _pure_fallback_reason,
)
from fleet_rlm.traces.classifier import (
    span_attributes as _pure_span_attributes,
)
from fleet_rlm.traces.classifier import (
    span_status_code as _pure_span_status_code,
)
from fleet_rlm.traces.classifier import (
    span_type as _pure_span_type,
)
from fleet_rlm.traces.mlflow_ingest import sanitize_trace_info, sanitize_trace_spans
from fleet_rlm.traces.performance import (
    output_chars as _pure_output_chars,
)
from fleet_rlm.traces.performance import (
    span_duration_ms as _pure_span_duration_ms,
)
from fleet_rlm.traces.performance import (
    span_token_usage as _pure_span_token_usage,
)
from fleet_rlm.traces.performance import (
    summarize_spans,
)

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
    """Compatibility adapter for :func:`fleet_rlm.traces.classifier.span_attributes`."""
    return _pure_span_attributes(span)


def _span_status_code(span: dict[str, Any]) -> str | None:
    """Compatibility adapter for the pure classifier helper."""
    return _pure_span_status_code(span)


def _int_or_none(value: Any) -> int | None:
    """Compatibility adapter for the provider-neutral numeric helper."""
    return _pure_int_or_none(value)


def _span_timestamp(value: Any) -> int | None:
    return _int_or_none(value)


def _span_duration_ms(span: dict[str, Any]) -> int | None:
    """Compatibility adapter for the pure performance helper."""
    return _pure_span_duration_ms(span)


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
    """Compatibility adapter for the pure performance helper."""
    return _pure_output_chars(span)


def _token_usage(span: dict[str, Any]) -> tuple[int | None, int | None, int | None]:
    """Compatibility adapter for the pure performance helper."""
    usage = _pure_span_token_usage(span)
    return usage.input_tokens or None, usage.output_tokens or None, usage.total_tokens or None


def _fallback_reason(span: dict[str, Any]) -> str | None:
    """Compatibility adapter for the pure classifier helper."""
    return _pure_fallback_reason(span)


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


def _build_performance_summary(
    spans: list[dict[str, Any]],
    *,
    raw_spans: list[dict[str, Any]] | None = None,
) -> SessionTracePerformanceSummary:
    """Build a client-safe performance summary from sanitized trace spans.

    Adapter fallback detection returns only fixed category counts, so retain
    those two numeric signals from provider data without retaining any raw
    span name, output, or selected-skill value in the client response.
    """
    payload = summarize_spans(spans).as_dict()
    if raw_spans is not None:
        raw_summary = summarize_spans(raw_spans)
        payload["adapter_fallback_count"] = raw_summary.adapter_fallback_count
        payload["parse_error_count"] = raw_summary.parse_error_count
    return SessionTracePerformanceSummary.model_validate(payload)


def _span_type(span: dict[str, Any]) -> str | None:
    """Compatibility adapter for the pure classifier helper."""
    return _pure_span_type(span)


def _component_type_hint(tool_name: str) -> str:
    """Compatibility adapter for the pure classifier helper."""
    return _pure_component_type_hint(tool_name)


def _classify_span(span: dict[str, Any]) -> tuple[MappedRenderKind, str | None, str, str | None]:
    """Compatibility adapter for the pure classifier helper."""
    result = _pure_classify_span(span)
    return result.render_kind, result.component_type, result.rationale, result.tool_name


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
    info = sanitize_trace_info(_trace_info(trace))
    raw_spans = _trace_spans(trace)
    spans = sanitize_trace_spans(raw_spans)
    mapped_spans: list[SessionTraceDebugSpan] = []
    renderable_count = 0

    for raw_span, span in zip(raw_spans, spans, strict=True):
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
                retry_or_fallback_reason=_fallback_reason(raw_span),
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
        # Aggregate only the sanitized span copies: compact references such as
        # slowest-span names and selected skills are client-facing fields too.
        performance_summary=_build_performance_summary(spans, raw_spans=raw_spans),
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
