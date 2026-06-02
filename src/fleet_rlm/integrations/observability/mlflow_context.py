"""MLflow request-context and trace-correlation helpers."""

from __future__ import annotations

import contextvars
import json
import os
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from threading import Lock
from typing import Any

from fleet_rlm.integrations.config._env_utils import env_bool as _env_bool


@dataclass(slots=True)
class MlflowTraceRequestContext:
    """Per-request MLflow metadata carried through DSPy execution."""

    client_request_id: str
    session_id: str | None = None
    user_id: str | None = None
    app_env: str | None = None
    request_preview: str | None = None
    model_id: str | None = None
    resolved_trace_id: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    final_response_preview: str | None = None
    final_trace_metadata: dict[str, Any] = field(default_factory=dict)
    emitted_trace_tags: dict[str, str] = field(default_factory=dict)


_CURRENT_REQUEST_CONTEXT: contextvars.ContextVar[MlflowTraceRequestContext | None] = contextvars.ContextVar[
    MlflowTraceRequestContext | None
](
    "fleet_rlm_mlflow_request_context",
    default=None,
)
_CURRENT_TRACE_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar[str | None](
    "fleet_rlm_mlflow_trace_id",
    default=None,
)
_TRACE_ID_LOCK = Lock()
_TRACE_IDS_BY_CLIENT_REQUEST_ID: dict[str, str] = {}
_TRAJECTORY_VALUE_LIMIT = 8_000
_RLM_REPL_TOOL_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "repl_execute",
        "description": "Execute Python code in the Daytona-backed RLM REPL to inspect variables and produce observations.",
        "parameters": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Python code to execute in the sandboxed RLM REPL.",
                }
            },
            "required": ["code"],
        },
    },
}


def _runtime_module():
    from . import mlflow_runtime

    return mlflow_runtime


def new_client_request_id(*, prefix: str = "fleet") -> str:
    """Create a stable per-request client correlation id."""
    return f"{prefix}-{uuid.uuid4().hex}"


def current_request_context() -> MlflowTraceRequestContext | None:
    """Return the active MLflow request context, if any."""
    return _CURRENT_REQUEST_CONTEXT.get()


@contextmanager
def mlflow_request_context(context: MlflowTraceRequestContext):
    """Scope MLflow request metadata to the current execution context."""
    context_token = _CURRENT_REQUEST_CONTEXT.set(context)
    trace_token = _CURRENT_TRACE_ID.set(None)
    trace_state = "OK"
    try:
        yield context
    except BaseException:
        trace_state = "ERROR"
        raise
    finally:
        finalize_current_mlflow_trace(state=trace_state)
        capture_last_active_trace_id()
        _runtime_module().flush_mlflow_traces()
        if context.final_response_preview or context.final_trace_metadata:
            update_current_mlflow_trace(
                response_preview=context.final_response_preview,
                trace_metadata=context.final_trace_metadata,
            )
            _runtime_module().flush_mlflow_traces()
        with _TRACE_ID_LOCK:
            _TRACE_IDS_BY_CLIENT_REQUEST_ID.pop(context.client_request_id, None)
        _CURRENT_TRACE_ID.reset(trace_token)
        _CURRENT_REQUEST_CONTEXT.reset(context_token)


def _stringify_metadata(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _trim_preview(value: str | None, *, limit: int = 512) -> str | None:
    candidate = (value or "").strip()
    if not candidate:
        return None
    if len(candidate) <= limit:
        return candidate
    return candidate[: limit - 3].rstrip() + "..."


def _bounded_value(value: Any, *, limit: int = _TRAJECTORY_VALUE_LIMIT) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        if len(value) <= limit:
            return value
        return value[: limit - 3].rstrip() + "..."
    try:
        serialized = json.dumps(value, ensure_ascii=False, default=str)
    except TypeError:
        return _bounded_value(str(value), limit=limit)
    if len(serialized) <= limit:
        return value
    return serialized[: limit - 3].rstrip() + "..."


def _flat_trajectory_indices(raw: dict[str, Any]) -> list[int]:
    indices: set[int] = set()
    for key in raw:
        if "_" not in key:
            continue
        _, suffix = key.rsplit("_", 1)
        if suffix.isdigit():
            indices.add(int(suffix))
    return sorted(indices)


def _coerce_trajectory_steps(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        return [dict(step, index=step.get("index", index)) for index, step in enumerate(raw) if isinstance(step, dict)]

    if not isinstance(raw, dict):
        return []

    for key in ("trajectory", "steps"):
        nested = raw.get(key)
        if isinstance(nested, list):
            return [
                dict(step, index=step.get("index", index))
                for index, step in enumerate(nested)
                if isinstance(step, dict)
            ]

    steps: list[dict[str, Any]] = []
    for index in _flat_trajectory_indices(raw):
        step = {
            "index": index,
            "thought": raw.get(f"thought_{index}") or raw.get(f"reasoning_{index}"),
            "tool_name": raw.get(f"tool_name_{index}"),
            "tool_args": raw.get(f"tool_args_{index}"),
            "observation": raw.get(f"observation_{index}"),
            "code": raw.get(f"code_{index}"),
            "output": raw.get(f"output_{index}"),
        }
        if any(value is not None for key, value in step.items() if key != "index"):
            steps.append(step)
    return steps


def _trajectory_span_name(step: dict[str, Any]) -> str | None:
    tool_name = str(step.get("tool_name") or step.get("type") or "").strip()
    if step.get("code") is not None:
        return "repl_execute"
    if tool_name:
        normalized = tool_name.lower()
        if "repl" in normalized or "sandbox" in normalized:
            return "repl_execute"
        return f"rlm_tool:{tool_name}"
    if step.get("output") is not None or step.get("observation") is not None:
        return "rlm_observation"
    return None


def _trajectory_step_failed(step: dict[str, Any]) -> bool:
    values = [step.get("output"), step.get("observation")]
    for value in values:
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized.startswith("[error]") or normalized.startswith("execution error"):
                return True
        if isinstance(value, dict):
            status = str(value.get("status") or "").strip().lower()
            if status in {"error", "failed", "failure"}:
                return True
    return False


def _record_rlm_available_tools_span(mlflow: Any, start_span: Any) -> bool:
    """Expose the RLM REPL tool schema in the shape MLflow judges inspect."""
    tools = [_RLM_REPL_TOOL_SCHEMA]
    attributes = {
        "mlflow.chat.tools": json.dumps(tools, ensure_ascii=False),
        "fleet_rlm.synthetic_span": "available_tools",
        "fleet_rlm.available_tools": "repl_execute",
    }
    try:
        with start_span(name="rlm_available_tools", span_type="LLM", attributes=attributes) as span:
            span.set_inputs({"tools": tools})
            span.set_outputs({"available_tools": ["repl_execute"]})
        return True
    except Exception:
        _runtime_module().logger.debug("MLflow RLM available-tools span recording skipped.", exc_info=True)
        return False


def record_rlm_trajectory_spans(trajectory: Any) -> int:
    """Materialize RLM trajectory tool/REPL steps as child MLflow spans."""
    steps = _coerce_trajectory_steps(trajectory)
    if not steps:
        return 0

    runtime = _runtime_module()
    mlflow = runtime._import_mlflow()
    if mlflow is None or not _has_active_mlflow_trace(mlflow):
        return 0

    start_span = getattr(mlflow, "start_span", None)
    if not callable(start_span):
        return 0

    recorded = 0
    if any(_trajectory_span_name(step) is not None for step in steps):
        _record_rlm_available_tools_span(mlflow, start_span)

    for step in steps:
        span_name = _trajectory_span_name(step)
        if span_name is None:
            continue

        index = step.get("index")
        tool_name = step.get("tool_name") or ("repl_execute" if step.get("code") is not None else span_name)
        inputs = {
            "tool_name": tool_name,
            "tool_args": _bounded_value(step.get("tool_args") or step.get("input")),
            "code": _bounded_value(step.get("code")),
        }
        outputs = {
            "observation": _bounded_value(step.get("observation")),
            "output": _bounded_value(step.get("output")),
        }
        attributes = {
            "fleet_rlm.trajectory_index": str(index) if index is not None else "",
            "fleet_rlm.trajectory_tool_name": str(tool_name),
            "fleet_rlm.trajectory_has_code": str(step.get("code") is not None).lower(),
            "fleet_rlm.trajectory_has_output": str(
                step.get("output") is not None or step.get("observation") is not None
            ).lower(),
        }
        failed = _trajectory_step_failed(step)
        if failed:
            attributes["fleet_rlm.trajectory_error"] = "true"
        thought = step.get("thought") or step.get("reasoning")
        if thought:
            attributes["fleet_rlm.trajectory_reasoning_preview"] = str(_bounded_value(thought, limit=1_000))

        try:
            with start_span(name=span_name, span_type="TOOL", attributes=attributes) as span:
                span.set_inputs({key: value for key, value in inputs.items() if value is not None})
                span.set_outputs({key: value for key, value in outputs.items() if value is not None})
                if failed:
                    span.set_status("ERROR")
            recorded += 1
        except Exception:
            runtime.logger.debug("MLflow RLM trajectory span recording skipped.", exc_info=True)

    return recorded


def _trace_metadata_from_context(
    context: MlflowTraceRequestContext,
) -> dict[str, str]:
    metadata: dict[str, str] = {}
    if context.app_env:
        metadata["app_env"] = context.app_env

    for key, value in context.metadata.items():
        text = _stringify_metadata(value).strip()
        if text:
            metadata[key] = text

    return metadata


def _trace_tags_from_context(
    context: MlflowTraceRequestContext,
    *,
    metadata: dict[str, str],
) -> dict[str, str]:
    """Return queryable Fleet tags for the active trace.

    MLflow trace metadata is useful for immutable context, but local OSS
    tracking stores and scorer-generated traces do not always expose custom
    metadata consistently in trace search results. Mirroring Fleet-owned
    correlation fields into tags keeps trace triage and UI lookups reliable.
    """
    tags: dict[str, str] = {
        "fleet_rlm.trace_kind": "application",
        "fleet_rlm.client_request_id": context.client_request_id,
    }
    if context.session_id:
        tags["fleet_rlm.session_id"] = context.session_id
    if context.user_id:
        tags["fleet_rlm.user_id"] = context.user_id
    if context.app_env:
        tags["fleet_rlm.app_env"] = context.app_env

    for key, value in metadata.items():
        if key.startswith("fleet_rlm."):
            tags[key] = value
    return tags


def _apply_trace_tags_by_id(mlflow: Any, trace_id: str | None, tags: dict[str, str]) -> None:
    """Best-effort tag update for traces that are no longer current/active."""
    if not trace_id or not tags:
        return
    set_trace_tag = getattr(mlflow, "set_trace_tag", None)
    if not callable(set_trace_tag):
        return
    for key, value in tags.items():
        try:
            set_trace_tag(str(trace_id), str(key), str(value))
        except Exception:
            _runtime_module().logger.debug("MLflow trace tag update skipped.", exc_info=True)


def _new_active_trace_tags(
    context: MlflowTraceRequestContext,
    tags: dict[str, str],
) -> dict[str, str]:
    """Return tags not yet sent through update_current_trace for this request."""
    new_tags = {key: value for key, value in tags.items() if key not in context.emitted_trace_tags}
    context.emitted_trace_tags.update(new_tags)
    return new_tags


def _resolve_trace_id_by_client_request_id(context: MlflowTraceRequestContext) -> str | None:
    """Resolve a completed trace when MLflow no longer exposes it as active."""
    try:
        from .mlflow_traces import resolve_trace_by_client_request_id

        trace = resolve_trace_by_client_request_id(
            context.client_request_id,
            config=_runtime_module().get_mlflow_config(),
            max_results=25,
        )
    except Exception:
        _runtime_module().logger.debug("MLflow trace lookup by client request id skipped.", exc_info=True)
        return None

    info = getattr(trace, "info", None)
    trace_id = getattr(info, "trace_id", None) or getattr(info, "request_id", None)
    if not trace_id:
        return None

    context.resolved_trace_id = str(trace_id)
    _CURRENT_TRACE_ID.set(str(trace_id))
    with _TRACE_ID_LOCK:
        _TRACE_IDS_BY_CLIENT_REQUEST_ID[context.client_request_id] = str(trace_id)
    return str(trace_id)


def _has_active_mlflow_trace(mlflow: Any) -> bool:
    get_current_active_span = getattr(mlflow, "get_current_active_span", None)
    if callable(get_current_active_span):
        try:
            if get_current_active_span() is not None:
                return True
        except Exception:
            _runtime_module().logger.debug("Failed to inspect current MLflow span.", exc_info=True)

    get_active_trace_id = getattr(mlflow, "get_active_trace_id", None)
    if callable(get_active_trace_id):
        try:
            return bool(get_active_trace_id())
        except Exception:
            _runtime_module().logger.debug("Failed to inspect current MLflow trace id.", exc_info=True)

    return False


def update_current_mlflow_trace(
    *,
    response_preview: str | None = None,
    trace_metadata: dict[str, Any] | None = None,
) -> str | None:
    """Apply the current request context to the active MLflow trace."""
    context = current_request_context()
    if context is None:
        return None
    if response_preview is not None:
        context.final_response_preview = response_preview
    if trace_metadata:
        context.final_trace_metadata.update(trace_metadata)

    runtime = _runtime_module()
    mlflow = runtime._import_mlflow()
    if mlflow is None:
        return None
    if not _has_active_mlflow_trace(mlflow):
        trace_id = capture_last_active_trace_id()
        if trace_id is None:
            trace_id = _resolve_trace_id_by_client_request_id(context)
        metadata = _trace_metadata_from_context(context)
        if trace_metadata:
            metadata.update(trace_metadata)
        tags = _trace_tags_from_context(context, metadata=metadata)
        _apply_trace_tags_by_id(mlflow, trace_id, tags)
        return trace_id

    try:
        config = runtime.get_mlflow_config()
        model_id = context.model_id or config.active_model_id
        metadata = _trace_metadata_from_context(context)
        if trace_metadata:
            metadata.update(trace_metadata)
        tags = _trace_tags_from_context(context, metadata=metadata)
        tags = _new_active_trace_tags(context, tags)
        mlflow.update_current_trace(
            client_request_id=context.client_request_id,
            metadata=metadata,
            tags=tags if tags else None,
            request_preview=_trim_preview(context.request_preview),
            response_preview=_trim_preview(response_preview),
            model_id=model_id,
            session_id=context.session_id,
            user=context.user_id,
        )
    except Exception:
        runtime.logger.debug("MLflow trace update skipped.", exc_info=True)

    return capture_last_active_trace_id()


def finalize_current_mlflow_trace(*, state: str) -> str | None:
    """Mark the active MLflow trace as terminal when request processing ends."""
    context = current_request_context()

    runtime = _runtime_module()
    mlflow = runtime._import_mlflow()
    if mlflow is None:
        return None
    if not _has_active_mlflow_trace(mlflow):
        trace_id = capture_last_active_trace_id()
        if context is not None:
            tags = _trace_tags_from_context(context, metadata=_trace_metadata_from_context(context))
            _apply_trace_tags_by_id(mlflow, trace_id, tags)
        return trace_id

    try:
        tags: dict[str, str] = {}
        if context is not None:
            if context.total_input_tokens > 0:
                tags["mlflow.traceInputTokens"] = str(context.total_input_tokens)
            if context.total_output_tokens > 0:
                tags["mlflow.traceOutputTokens"] = str(context.total_output_tokens)
            total = context.total_input_tokens + context.total_output_tokens
            if total > 0:
                tags["mlflow.traceTotalTokens"] = str(total)
        mlflow.update_current_trace(state=state, tags=tags if tags else None)
    except Exception:
        runtime.logger.debug("MLflow trace finalization skipped.", exc_info=True)

    return capture_last_active_trace_id()


def capture_last_active_trace_id() -> str | None:
    """Cache and return the last active MLflow trace id for this execution."""
    context = current_request_context()

    trace_id = _CURRENT_TRACE_ID.get()
    if trace_id:
        if context is not None:
            context.resolved_trace_id = trace_id
            with _TRACE_ID_LOCK:
                _TRACE_IDS_BY_CLIENT_REQUEST_ID[context.client_request_id] = trace_id
        return trace_id

    if context is not None:
        if context.resolved_trace_id:
            _CURRENT_TRACE_ID.set(context.resolved_trace_id)
            return context.resolved_trace_id
        with _TRACE_ID_LOCK:
            request_trace_id = _TRACE_IDS_BY_CLIENT_REQUEST_ID.get(context.client_request_id)
        if request_trace_id:
            context.resolved_trace_id = request_trace_id
            _CURRENT_TRACE_ID.set(request_trace_id)
            return request_trace_id

    runtime = _runtime_module()
    mlflow = runtime._import_mlflow()
    if mlflow is None:
        return None

    get_active_trace_id = getattr(mlflow, "get_active_trace_id", None)
    if callable(get_active_trace_id):
        try:
            trace_id = get_active_trace_id()
        except Exception:
            trace_id = None
        if trace_id:
            _CURRENT_TRACE_ID.set(trace_id)
            if context is not None:
                context.resolved_trace_id = trace_id
            return trace_id

    try:
        trace_id = mlflow.get_last_active_trace_id(thread_local=True)
    except Exception:
        trace_id = None

    if trace_id:
        _CURRENT_TRACE_ID.set(trace_id)
        if context is not None:
            context.resolved_trace_id = trace_id
            with _TRACE_ID_LOCK:
                _TRACE_IDS_BY_CLIENT_REQUEST_ID[context.client_request_id] = trace_id
    return trace_id


def trace_result_metadata(
    *,
    response_preview: str | None = None,
    trace_metadata: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Return optional MLflow metadata to attach to final/result payloads."""
    if not _env_bool(os.getenv("MLFLOW_ENABLED"), default=True):
        return {}

    runtime = _runtime_module()
    config = runtime.get_mlflow_config()
    if not config.enabled:
        return {}
    if runtime._import_mlflow() is None:
        return {}
    if not runtime.initialize_mlflow(config):
        return {}

    trace_id = update_current_mlflow_trace(
        response_preview=response_preview,
        trace_metadata=trace_metadata,
    )
    context = current_request_context()
    payload: dict[str, str] = {}
    if trace_id:
        payload["mlflow_trace_id"] = trace_id
    if context is not None:
        payload["mlflow_client_request_id"] = context.client_request_id
    return payload


def merge_trace_result_metadata(
    payload: dict[str, Any] | None,
    *,
    response_preview: str | None = None,
    trace_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a payload with optional MLflow metadata merged in."""
    merged = dict(payload or {})
    merged.update(
        trace_result_metadata(
            response_preview=response_preview,
            trace_metadata=trace_metadata,
        )
    )
    return merged


__all__ = [
    "MlflowTraceRequestContext",
    "capture_last_active_trace_id",
    "current_request_context",
    "merge_trace_result_metadata",
    "mlflow_request_context",
    "new_client_request_id",
    "trace_result_metadata",
    "update_current_mlflow_trace",
]
