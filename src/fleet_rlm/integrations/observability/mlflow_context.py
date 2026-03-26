"""MLflow request-context and trace-correlation helpers."""

from __future__ import annotations

import contextvars
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


_CURRENT_REQUEST_CONTEXT: contextvars.ContextVar[MlflowTraceRequestContext | None] = (
    contextvars.ContextVar[MlflowTraceRequestContext | None](
        "fleet_rlm_mlflow_request_context",
        default=None,
    )
)
_CURRENT_TRACE_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar[
    str | None
](
    "fleet_rlm_mlflow_trace_id",
    default=None,
)
_TRACE_ID_LOCK = Lock()
_TRACE_IDS_BY_CLIENT_REQUEST_ID: dict[str, str] = {}


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
    try:
        yield context
    finally:
        capture_last_active_trace_id()
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


def _trace_metadata_from_context(
    context: MlflowTraceRequestContext,
) -> dict[str, str]:
    metadata: dict[str, str] = {}
    if context.session_id:
        metadata["mlflow.trace.session"] = context.session_id
    if context.user_id:
        metadata["mlflow.trace.user"] = context.user_id
    if context.app_env:
        metadata["app_env"] = context.app_env

    for key, value in context.metadata.items():
        text = _stringify_metadata(value).strip()
        if text:
            metadata[key] = text

    config = _runtime_module().get_mlflow_config()
    if config.active_model_id:
        metadata["fleet_rlm.active_model_id"] = config.active_model_id

    return metadata


def _has_active_mlflow_trace(mlflow: Any) -> bool:
    get_current_active_span = getattr(mlflow, "get_current_active_span", None)
    if callable(get_current_active_span):
        try:
            if get_current_active_span() is not None:
                return True
        except Exception:
            _runtime_module().logger.debug(
                "Failed to inspect current MLflow span.", exc_info=True
            )

    get_active_trace_id = getattr(mlflow, "get_active_trace_id", None)
    if callable(get_active_trace_id):
        try:
            return bool(get_active_trace_id())
        except Exception:
            _runtime_module().logger.debug(
                "Failed to inspect current MLflow trace id.", exc_info=True
            )

    return False


def update_current_mlflow_trace(
    *,
    response_preview: str | None = None,
) -> str | None:
    """Apply the current request context to the active MLflow trace."""
    context = current_request_context()
    if context is None:
        return None

    runtime = _runtime_module()
    mlflow = runtime._import_mlflow()
    if mlflow is None:
        return None
    if not _has_active_mlflow_trace(mlflow):
        return capture_last_active_trace_id()

    try:
        config = runtime.get_mlflow_config()
        model_id = context.model_id or config.active_model_id
        mlflow.update_current_trace(
            client_request_id=context.client_request_id,
            metadata=_trace_metadata_from_context(context),
            request_preview=_trim_preview(context.request_preview),
            response_preview=_trim_preview(response_preview),
            model_id=model_id,
        )
    except Exception:
        runtime.logger.debug("MLflow trace update skipped.", exc_info=True)

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
            request_trace_id = _TRACE_IDS_BY_CLIENT_REQUEST_ID.get(
                context.client_request_id
            )
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


def trace_result_metadata(*, response_preview: str | None = None) -> dict[str, str]:
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

    trace_id = update_current_mlflow_trace(response_preview=response_preview)
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
) -> dict[str, Any]:
    """Return a payload with optional MLflow metadata merged in."""
    merged = dict(payload or {})
    merged.update(trace_result_metadata(response_preview=response_preview))
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
