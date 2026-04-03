"""MLflow lifecycle, trace correlation, and offline trace export helpers."""

from __future__ import annotations

import hashlib
import logging
import os
from threading import Lock
from typing import Any
from collections.abc import Callable
from urllib.parse import urlsplit, urlunsplit

import dspy
from dspy.utils.callback import BaseCallback

from .mlflow_context import (
    MlflowTraceRequestContext,
    capture_last_active_trace_id,
    current_request_context,
    merge_trace_result_metadata,
    mlflow_request_context,
    new_client_request_id,
    trace_result_metadata,
    update_current_mlflow_trace,
)
from .config import MlflowConfig

logger = logging.getLogger(__name__)

_CLIENT_LOCK = Lock()
_INIT_IDENTITY: tuple[Any, ...] | None = None
_LAST_INIT_WAS_AUTH_FAILURE = False
_ACTIVE_CONFIG: MlflowConfig | None = None


def _mlflow_identity(config: MlflowConfig) -> tuple[Any, ...]:
    return (
        config.enabled,
        config.tracking_uri,
        config.experiment,
        config.active_model_id,
        config.dspy_log_traces_from_compile,
        config.dspy_log_traces_from_eval,
        config.dspy_log_compiles,
        config.dspy_log_evals,
        config.enable_auto_assessment,
        *_mlflow_tracking_auth_identity(),
    )


def _hashed_env_var(name: str) -> str | None:
    value = (os.getenv(name) or "").strip()
    if not value:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _mlflow_tracking_auth_identity() -> tuple[Any, ...]:
    return (
        (os.getenv("MLFLOW_TRACKING_USERNAME") or "").strip() or None,
        _hashed_env_var("MLFLOW_TRACKING_PASSWORD"),
        _hashed_env_var("MLFLOW_TRACKING_TOKEN"),
        (os.getenv("MLFLOW_TRACKING_INSECURE_TLS") or "").strip().lower() or None,
    )


def _import_mlflow() -> Any | None:
    try:
        import mlflow
    except ImportError:
        return None
    return mlflow


def _sanitize_log_field(value: str) -> str:
    """Escape control characters before including user-provided ids in logs."""

    return value.replace("\r", "\\r").replace("\n", "\\n")


def _sanitize_tracking_uri(value: str) -> str:
    """Redact credentials and query strings before logging tracking URIs."""

    candidate = value.strip()
    if not candidate:
        return "<unset>"

    try:
        parsed = urlsplit(candidate)
    except ValueError:
        return candidate

    if not parsed.scheme and not parsed.netloc:
        return candidate

    netloc = parsed.netloc
    if "@" in netloc:
        userinfo, hostinfo = netloc.rsplit("@", 1)
        username = userinfo.split(":", 1)[0] if userinfo else ""
        redacted_userinfo = f"{username}:***" if username else "***"
        netloc = f"{redacted_userinfo}@{hostinfo}"

    return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))


def _has_mlflow_tracking_auth_configured() -> bool:
    username = (os.getenv("MLFLOW_TRACKING_USERNAME") or "").strip()
    password = (os.getenv("MLFLOW_TRACKING_PASSWORD") or "").strip()
    token = (os.getenv("MLFLOW_TRACKING_TOKEN") or "").strip()
    return bool(token or (username and password))


def _log_mlflow_initialization_failure(exc: Exception, *, tracking_uri: str) -> None:
    """Emit an actionable warning for MLflow init failures without crashing startup."""

    sanitized_tracking_uri = _sanitize_tracking_uri(tracking_uri)
    detail = str(exc)
    detail_lower = detail.lower()
    auth_guidance = (
        "Configure MLflow auth with MLFLOW_TRACKING_TOKEN or "
        "MLFLOW_TRACKING_USERNAME/MLFLOW_TRACKING_PASSWORD, or set "
        "MLFLOW_ENABLED=false to disable MLflow for this environment."
    )

    if "403" in detail_lower:
        guidance = auth_guidance
        if _has_mlflow_tracking_auth_configured():
            guidance = (
                "The current process already has MLflow auth environment variables set. "
                "Verify the credentials and experiment permissions for this tracking server."
            )
        logger.warning(
            "MLflow integration disabled for tracking URI '%s': the tracking server "
            "rejected experiment access (HTTP 403). %s",
            sanitized_tracking_uri,
            guidance,
        )
    else:
        logger.warning(
            "Failed to initialize MLflow integration for tracking URI '%s'. "
            "Startup will continue without MLflow. Check connectivity, permissions, "
            "and MLflow auth configuration. %s",
            sanitized_tracking_uri,
            auth_guidance,
        )

    logger.debug(
        "MLflow initialization failure details for '%s'.",
        sanitized_tracking_uri,
        exc_info=(type(exc), exc, exc.__traceback__),
    )


def _is_auth_forbidden_failure(exc: Exception) -> bool:
    return "403" in str(exc).lower()


def _mlflow_string_literal(value: str) -> str:
    """Escape single quotes for MLflow's SQL-like trace search DSL."""

    return value.replace("'", "''")


def _existing_trace_callback() -> FleetMlflowTraceCallback | None:
    callbacks = list(getattr(dspy.settings, "callbacks", []) or [])
    for callback in callbacks:
        if isinstance(callback, FleetMlflowTraceCallback):
            return callback
    return None


def get_mlflow_config() -> MlflowConfig:
    """Return the active MLflow config, falling back to env settings."""
    return _ACTIVE_CONFIG or MlflowConfig.from_env()


def initialize_mlflow(config: MlflowConfig | None = None) -> bool:
    """Best-effort idempotent MLflow initialization for DSPy runtimes."""
    resolved = config or MlflowConfig.from_env()
    identity = _mlflow_identity(resolved)

    global _LAST_INIT_WAS_AUTH_FAILURE, _INIT_IDENTITY, _ACTIVE_CONFIG
    with _CLIENT_LOCK:
        _ACTIVE_CONFIG = resolved

        # Preserve idempotency after success, and avoid hammering the same
        # tracking endpoint after an auth-forbidden failure until auth changes.
        if identity == _INIT_IDENTITY:
            if _LAST_INIT_WAS_AUTH_FAILURE or not resolved.enabled:
                return False
            mlflow = _import_mlflow()
            if mlflow is None:
                return False
            return True

        if not resolved.enabled:
            _LAST_INIT_WAS_AUTH_FAILURE = False
            _INIT_IDENTITY = identity
            return False

        mlflow = _import_mlflow()
        if mlflow is None:
            logger.debug("MLflow is not installed; skipping runtime initialization.")
            _LAST_INIT_WAS_AUTH_FAILURE = False
            _INIT_IDENTITY = identity
            return False

        try:
            mlflow.set_tracking_uri(resolved.tracking_uri)
            if resolved.experiment:
                mlflow.set_experiment(experiment_name=resolved.experiment)
            mlflow.dspy.autolog(
                log_traces=True,
                log_traces_from_compile=resolved.dspy_log_traces_from_compile,
                log_traces_from_eval=resolved.dspy_log_traces_from_eval,
                log_compiles=resolved.dspy_log_compiles,
                log_evals=resolved.dspy_log_evals,
                disable=False,
                silent=True,
            )

            if _existing_trace_callback() is None:
                callbacks = list(getattr(dspy.settings, "callbacks", []) or [])
                dspy.configure(callbacks=[*callbacks, FleetMlflowTraceCallback()])

            _LAST_INIT_WAS_AUTH_FAILURE = False
            _INIT_IDENTITY = identity
            return True
        except Exception as exc:
            is_auth_failure = _is_auth_forbidden_failure(exc)
            _LAST_INIT_WAS_AUTH_FAILURE = is_auth_failure
            # Only cache auth failures to avoid hammering the endpoint with bad creds.
            # Non-auth failures (transient errors) are not cached so the next call retries.
            if is_auth_failure:
                _INIT_IDENTITY = identity
            _log_mlflow_initialization_failure(
                exc,
                tracking_uri=resolved.tracking_uri,
            )
            return False


def flush_mlflow_traces(*, terminate: bool = False) -> None:
    """Flush pending async MLflow trace logging."""
    mlflow = _import_mlflow()
    if mlflow is None:
        return
    trace_exporter_getter: Callable[[], Any] | None = None
    try:
        from mlflow.tracing.provider import _get_trace_exporter
    except ImportError:
        pass
    else:
        trace_exporter_getter = _get_trace_exporter

    if trace_exporter_getter is not None:
        try:
            exporter = trace_exporter_getter()
        except Exception:
            exporter = None
        if exporter is None or not hasattr(exporter, "_async_queue"):
            return
    try:
        mlflow.flush_trace_async_logging(terminate=terminate)
    except Exception:
        logger.warning("Failed to flush MLflow traces.", exc_info=True)


def shutdown_mlflow() -> None:
    """Flush and terminate MLflow async trace workers."""
    flush_mlflow_traces(terminate=True)


def _extract_token_usage(
    outputs: dict[str, Any] | None,
) -> tuple[int | None, int | None]:
    """Extract (input_tokens, output_tokens) from LM call outputs."""
    if not isinstance(outputs, dict):
        return None, None
    usage = outputs.get("usage")
    if not isinstance(usage, dict):
        usage = outputs.get("token_usage")
    if not isinstance(usage, dict):
        return None, None

    def _int_or_none(value: Any) -> int | None:
        if isinstance(value, bool) or value is None:
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str) and value.isdigit():
            return int(value)
        return None

    input_tokens = _int_or_none(
        usage.get("prompt_tokens")
        or usage.get("input_tokens")
        or usage.get("promptTokens")
        or usage.get("inputTokens")
    )
    output_tokens = _int_or_none(
        usage.get("completion_tokens")
        or usage.get("output_tokens")
        or usage.get("completionTokens")
        or usage.get("outputTokens")
    )
    return input_tokens, output_tokens


def _set_span_error_description(exception: Exception) -> None:
    """Best-effort: propagate exception message to the active MLflow span."""
    mlflow = _import_mlflow()
    if mlflow is None:
        return
    try:
        span = mlflow.get_current_active_span()
        if span is None:
            return
        otel_span = getattr(span, "_span", None)
        if otel_span is None:
            return
        set_status = getattr(otel_span, "set_status", None)
        if not callable(set_status):
            return
        from opentelemetry.trace import StatusCode

        description = f"{type(exception).__name__}: {exception}"
        set_status(StatusCode.ERROR, description=description)
    except Exception as exc:
        # Trace enrichment is best-effort and must never mask the original error.
        logger.debug("Failed to update MLflow span status", exc_info=exc)


class FleetMlflowTraceCallback(BaseCallback):
    """DSPy callback that propagates per-request context into MLflow traces."""

    def on_module_start(
        self, call_id: str, instance: Any, inputs: dict[str, Any]
    ) -> None:
        _ = (call_id, instance, inputs)
        update_current_mlflow_trace()

    def on_module_end(
        self,
        call_id: str,
        outputs: Any | None,
        exception: Exception | None = None,
    ) -> None:
        _ = call_id
        if exception is not None:
            _set_span_error_description(exception)
        preview = outputs if isinstance(outputs, str) else None
        update_current_mlflow_trace(response_preview=preview)
        capture_last_active_trace_id()

    def on_lm_start(self, call_id: str, instance: Any, inputs: dict[str, Any]) -> None:
        _ = (call_id, instance, inputs)
        update_current_mlflow_trace()

    def on_lm_end(
        self,
        call_id: str,
        outputs: dict[str, Any] | None,
        exception: Exception | None = None,
    ) -> None:
        _ = call_id
        if exception is not None:
            _set_span_error_description(exception)
        preview: str | None = None
        if isinstance(outputs, dict):
            choices = outputs.get("choices")
            if isinstance(choices, list) and choices:
                first = choices[0]
                if isinstance(first, dict):
                    preview = str(
                        first.get("text")
                        or first.get("content")
                        or first.get("message", {}).get("content")
                        or ""
                    )
        # Accumulate token usage on the per-request context.
        input_tokens, output_tokens = _extract_token_usage(outputs)
        ctx = current_request_context()
        if ctx is not None:
            if input_tokens is not None:
                ctx.total_input_tokens += input_tokens
            if output_tokens is not None:
                ctx.total_output_tokens += output_tokens
        update_current_mlflow_trace(response_preview=preview)
        capture_last_active_trace_id()


def resolve_trace_by_client_request_id(
    client_request_id: str,
    *,
    config: MlflowConfig | None = None,
    max_results: int = 5000,
):
    from .mlflow_traces import resolve_trace_by_client_request_id as _impl

    return _impl(
        client_request_id,
        config=config,
        max_results=max_results,
    )


def resolve_trace(
    *,
    trace_id: str | None = None,
    client_request_id: str | None = None,
    config: MlflowConfig | None = None,
):
    from .mlflow_traces import resolve_trace as _impl

    return _impl(
        trace_id=trace_id,
        client_request_id=client_request_id,
        config=config,
    )


def log_trace_feedback(
    *,
    trace_id: str,
    is_correct: bool,
    source_id: str,
    comment: str | None = None,
    expected_response: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, bool]:
    from .mlflow_traces import log_trace_feedback as _impl

    return _impl(
        trace_id=trace_id,
        is_correct=is_correct,
        source_id=source_id,
        comment=comment,
        expected_response=expected_response,
        metadata=metadata,
    )


def trace_to_dataset_row(trace: Any) -> dict[str, Any]:
    from .mlflow_traces import trace_to_dataset_row as _impl

    return _impl(trace)


def search_annotated_trace_rows(
    *,
    config: MlflowConfig | None = None,
    max_results: int = 5000,
) -> list[dict[str, Any]]:
    from .mlflow_traces import search_annotated_trace_rows as _impl

    return _impl(config=config, max_results=max_results)


__all__ = [
    "FleetMlflowTraceCallback",
    "MlflowTraceRequestContext",
    "capture_last_active_trace_id",
    "current_request_context",
    "flush_mlflow_traces",
    "get_mlflow_config",
    "initialize_mlflow",
    "log_trace_feedback",
    "merge_trace_result_metadata",
    "mlflow_request_context",
    "new_client_request_id",
    "resolve_trace",
    "resolve_trace_by_client_request_id",
    "search_annotated_trace_rows",
    "shutdown_mlflow",
    "trace_result_metadata",
    "trace_to_dataset_row",
    "update_current_mlflow_trace",
]
