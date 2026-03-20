"""Analytics integration helpers for PostHog and MLflow."""

from __future__ import annotations

import dspy

from .client import flush_posthog_client, shutdown_posthog_client
from .config import MlflowConfig, PostHogConfig
from .mlflow_runtime import (
    FleetMlflowTraceCallback,
    MlflowTraceRequestContext,
    capture_last_active_trace_id,
    current_request_context,
    flush_mlflow_traces,
    get_mlflow_config,
    initialize_mlflow,
    merge_trace_result_metadata,
    mlflow_request_context,
    new_client_request_id,
    shutdown_mlflow,
    update_current_mlflow_trace,
)
from .mlflow_traces import (
    log_trace_feedback,
    resolve_trace,
    resolve_trace_by_client_request_id,
    search_annotated_trace_rows,
    trace_to_dataset_row,
)
from .mlflow_runtime import trace_result_metadata
from .posthog_callback import PostHogLLMCallback
from .scorers import (
    build_rlm_scorers,
    get_default_judge_model,
    reasoning_quality_scorer,
)


__all__ = [
    "MlflowConfig",
    "MlflowTraceRequestContext",
    "FleetMlflowTraceCallback",
    "PostHogConfig",
    "PostHogLLMCallback",
    "capture_last_active_trace_id",
    "configure_analytics",
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
    "flush_posthog_client",
    "shutdown_posthog_client",
    "trace_result_metadata",
    "trace_to_dataset_row",
    "update_current_mlflow_trace",
    "build_rlm_scorers",
    "get_default_judge_model",
    "reasoning_quality_scorer",
]


def _get_existing_callback() -> PostHogLLMCallback | None:
    callbacks = list(getattr(dspy.settings, "callbacks", []) or [])
    for callback in callbacks:
        if isinstance(callback, PostHogLLMCallback):
            return callback
    return None


def configure_analytics(
    *,
    api_key: str | None = None,
    host: str = "https://us.i.posthog.com",
    distinct_id: str | None = None,
    enabled: bool | None = None,
) -> PostHogLLMCallback | None:
    """Configure and register PostHog analytics callback with DSPy."""
    base = PostHogConfig.from_env()
    resolved_host = (
        base.host if host == "https://us.i.posthog.com" and base.host else host
    )
    config = PostHogConfig(
        enabled=base.enabled if enabled is None else enabled,
        api_key=api_key if api_key is not None else base.api_key,
        host=resolved_host,
        flush_interval=base.flush_interval,
        flush_at=base.flush_at,
        enable_dspy_optimization=base.enable_dspy_optimization,
        input_truncation_chars=base.input_truncation_chars,
        output_truncation_chars=base.output_truncation_chars,
        redact_sensitive=base.redact_sensitive,
    )

    if not config.enabled or not config.api_key:
        return None

    existing = _get_existing_callback()
    if existing is not None:
        # Keep registration idempotent and avoid callback duplication.
        return existing

    callback = PostHogLLMCallback(config, distinct_id=distinct_id)
    existing_callbacks = list(getattr(dspy.settings, "callbacks", []) or [])
    dspy.configure(callbacks=[*existing_callbacks, callback])
    return callback
