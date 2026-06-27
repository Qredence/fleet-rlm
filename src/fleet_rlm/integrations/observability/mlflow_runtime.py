"""MLflow lifecycle, trace correlation, and offline trace export helpers."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys
from threading import Lock
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import dspy
from dspy.utils.callback import BaseCallback

from fleet_rlm.utils.logging import sanitize_for_log

from .config import MlflowConfig
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

logger = logging.getLogger(__name__)

_CLIENT_LOCK = Lock()
_MLFLOW_IMPORT_LOCK = Lock()
_INIT_IDENTITY: tuple[Any, ...] | None = None
_LAST_INIT_WAS_AUTH_FAILURE = False
_ACTIVE_CONFIG: MlflowConfig | None = None
_CACHED_EXPERIMENT_ID: str | None = None


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
        config.auto_assessment_sample_rate,
        tuple(config.auto_assessment_scorers),
        config.enable_span_processors,
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


def _clear_partial_mlflow_import() -> None:
    """Remove partially initialized MLflow modules after a failed import."""

    for module_name in list(sys.modules):
        if module_name == "mlflow" or module_name.startswith("mlflow."):
            sys.modules.pop(module_name, None)


def _import_mlflow() -> Any | None:
    with _MLFLOW_IMPORT_LOCK:
        for attempt in range(2):
            try:
                import mlflow

                # MLflow circular-import failures can leave a top-level module
                # object without expected attributes. Touch a stable attribute so
                # the helper either returns a usable module or clears the partial
                # import before the request continues.
                _ = mlflow.version.VERSION
            except ImportError:
                return None
            except Exception:
                _clear_partial_mlflow_import()
                if attempt == 0:
                    continue
                logger.warning("Failed to import MLflow after clearing a partial import.", exc_info=True)
                return None
            else:
                return mlflow
        return None


def _sanitize_log_field(value: object) -> str:
    """Preserve the MLflow-runtime helper name for sibling modules/tests."""

    return sanitize_for_log(value)


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


def get_mlflow_experiment_id() -> str | None:
    """Return the cached MLflow experiment id from the last successful init."""
    return _CACHED_EXPERIMENT_ID


def initialize_mlflow(config: MlflowConfig | None = None) -> bool:
    """Best-effort idempotent MLflow initialization for DSPy runtimes."""
    resolved = config or MlflowConfig.from_env()
    identity = _mlflow_identity(resolved)

    global _CACHED_EXPERIMENT_ID, _LAST_INIT_WAS_AUTH_FAILURE, _INIT_IDENTITY, _ACTIVE_CONFIG
    with _CLIENT_LOCK:
        _ACTIVE_CONFIG = resolved

        # Preserve idempotency after success, and avoid hammering the same
        # tracking endpoint after an auth-forbidden failure until auth changes.
        if identity == _INIT_IDENTITY:
            if _LAST_INIT_WAS_AUTH_FAILURE or not resolved.enabled:
                _CACHED_EXPERIMENT_ID = None
                return False
            mlflow = _import_mlflow()
            if mlflow is None:
                _CACHED_EXPERIMENT_ID = None
                return False
            return True

        if not resolved.enabled:
            _LAST_INIT_WAS_AUTH_FAILURE = False
            _INIT_IDENTITY = identity
            _CACHED_EXPERIMENT_ID = None
            return False

        mlflow = _import_mlflow()
        if mlflow is None:
            logger.debug("MLflow is not installed; skipping runtime initialization.")
            _LAST_INIT_WAS_AUTH_FAILURE = False
            _INIT_IDENTITY = identity
            _CACHED_EXPERIMENT_ID = None
            return False

        try:
            mlflow.set_tracking_uri(resolved.tracking_uri)
            if resolved.experiment:
                mlflow.set_experiment(experiment_name=resolved.experiment)
                try:
                    experiment = mlflow.get_experiment_by_name(resolved.experiment)
                    if experiment is not None:
                        _CACHED_EXPERIMENT_ID = str(experiment.experiment_id)
                        client = mlflow.MlflowClient()
                        client.set_experiment_tag(
                            experiment.experiment_id,
                            "mlflow.experimentKind",
                            "genai_development",
                        )
                except Exception:
                    logger.debug("Failed to set mlflow.experimentKind tag", exc_info=True)
            if resolved.active_model_id:
                mlflow.set_active_model(name=resolved.active_model_id)
            mlflow.dspy.autolog(
                log_traces=True,
                log_traces_from_compile=resolved.dspy_log_traces_from_compile,
                log_traces_from_eval=resolved.dspy_log_traces_from_eval,
                log_compiles=resolved.dspy_log_compiles,
                log_evals=resolved.dspy_log_evals,
                disable=False,
                silent=True,
            )

            if resolved.enable_span_processors:
                try:
                    from .span_processors import build_span_processors

                    processors = build_span_processors(
                        app_env=os.getenv("APP_ENV"),
                        workspace_id=os.getenv("WS_DEFAULT_WORKSPACE_ID"),
                    )
                    mlflow.tracing.configure(span_processors=processors)
                except Exception:
                    logger.debug("Failed to configure span processors", exc_info=True)

            try:
                from .auto_assessment import configure_auto_assessment, warn_if_persisted_scorers_active

                if resolved.enable_auto_assessment:
                    configure_auto_assessment(resolved)
                else:
                    warn_if_persisted_scorers_active(resolved, mlflow=mlflow)
            except Exception:
                logger.debug("Failed to inspect/configure MLflow auto-assessment", exc_info=True)

            if _existing_trace_callback() is None:
                from .callback_registry import ensure_dspy_callbacks

                ensure_dspy_callbacks([ThinkTagStripCallback(), FleetMlflowTraceCallback()])

            _LAST_INIT_WAS_AUTH_FAILURE = False
            _INIT_IDENTITY = identity
            return True
        except Exception as exc:
            is_auth_failure = _is_auth_forbidden_failure(exc)
            _LAST_INIT_WAS_AUTH_FAILURE = is_auth_failure
            _CACHED_EXPERIMENT_ID = None
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
    flush_trace_async_logging = getattr(mlflow, "flush_trace_async_logging", None)
    if not callable(flush_trace_async_logging):
        return
    try:
        flush_trace_async_logging(terminate=terminate)
    except Exception:
        logger.warning("Failed to flush MLflow traces.", exc_info=True)


def shutdown_mlflow() -> None:
    """Flush and terminate MLflow async trace workers."""
    flush_mlflow_traces(terminate=True)


def _extract_token_usage(
    outputs: dict[str, Any] | None,
) -> tuple[int | None, int | None]:
    """Extract (input_tokens, output_tokens) from LM call outputs.

    Falls back to estimating tokens from text length (4 chars ≈ 1 token)
    when usage data is not available in the outputs.
    """
    if not isinstance(outputs, dict):
        return None, None

    # Try to extract from usage dict
    usage = outputs.get("usage")
    if not isinstance(usage, dict):
        usage = outputs.get("token_usage")
    if not isinstance(usage, dict):
        usage = outputs.get("usage_metadata")

    if isinstance(usage, dict):

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

        # If we got at least one, return both
        if input_tokens is not None or output_tokens is not None:
            return input_tokens, output_tokens

    # Fallback: estimate tokens from text length (4 chars ≈ 1 token)
    # Try to extract text from outputs
    input_text = None
    output_text = None

    # Try to get input text from messages or prompt
    messages = outputs.get("messages")
    if isinstance(messages, list):
        input_text = " ".join(str(m) for m in messages)
    elif "prompt" in outputs:
        input_text = str(outputs["prompt"])

    # Try to get output text from choices or text
    choices = outputs.get("choices")
    if isinstance(choices, list) and choices:
        first_choice = choices[0]
        if isinstance(first_choice, dict):
            message = first_choice.get("message")
            if isinstance(message, dict):
                output_text = message.get("content")
            if not output_text:
                output_text = first_choice.get("text")
    if not output_text and "text" in outputs:
        output_text = str(outputs["text"])

    # Estimate tokens: 4 characters ≈ 1 token
    input_tokens = len(input_text) // 4 if input_text else None
    output_tokens = len(output_text) // 4 if output_text else None

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
    except Exception:
        # Trace enrichment is best-effort and must never mask the original error.
        logger.debug("Failed to update MLflow span status", exc_info=True)


def _set_active_span_token_usage(input_tokens: int | None, output_tokens: int | None) -> None:
    if input_tokens is None and output_tokens is None:
        return
    mlflow = _import_mlflow()
    if mlflow is None:
        return
    try:
        span = mlflow.get_current_active_span()
    except Exception:
        logger.debug("Failed to inspect current MLflow span for token usage", exc_info=True)
        return
    if span is None:
        return

    usage: dict[str, int] = {}
    if input_tokens is not None:
        usage["input_tokens"] = input_tokens
    if output_tokens is not None:
        usage["output_tokens"] = output_tokens
    if input_tokens is not None or output_tokens is not None:
        usage["total_tokens"] = int(input_tokens or 0) + int(output_tokens or 0)

    attributes: dict[str, Any] = {
        "mlflow.chat.tokenUsage": usage,
        "mlflow.chat.tokenUsageJson": json.dumps(usage, sort_keys=True),
    }
    if input_tokens is not None:
        attributes["mlflow.chat.inputTokens"] = input_tokens
    if output_tokens is not None:
        attributes["mlflow.chat.outputTokens"] = output_tokens
    if usage.get("total_tokens") is not None:
        attributes["mlflow.chat.totalTokens"] = usage["total_tokens"]

    for candidate in (span, getattr(span, "_span", None)):
        if candidate is None:
            continue
        set_attributes = getattr(candidate, "set_attributes", None)
        if callable(set_attributes):
            try:
                set_attributes(attributes)
                return
            except Exception:
                logger.debug("Failed to set MLflow span token usage attributes in bulk", exc_info=True)
        set_attribute = getattr(candidate, "set_attribute", None)
        if not callable(set_attribute):
            continue
        wrote_attribute = False
        for key, value in attributes.items():
            try:
                set_attribute(key, value)
                wrote_attribute = True
            except Exception:
                if key == "mlflow.chat.tokenUsage":
                    try:
                        set_attribute(key, attributes["mlflow.chat.tokenUsageJson"])
                        wrote_attribute = True
                    except Exception:
                        logger.debug("Failed to set MLflow token usage span attribute", exc_info=True)
        if wrote_attribute:
            return


class FleetMlflowTraceCallback(BaseCallback):
    """DSPy callback that propagates per-request context into MLflow traces."""

    def on_module_start(self, call_id: str, instance: Any, inputs: dict[str, Any]) -> None:
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
                        first.get("text") or first.get("content") or first.get("message", {}).get("content") or ""
                    )
        # Accumulate token usage on the per-request context.
        input_tokens, output_tokens = _extract_token_usage(outputs)
        _set_active_span_token_usage(input_tokens, output_tokens)
        ctx = current_request_context()
        if ctx is not None:
            if input_tokens is not None:
                ctx.total_input_tokens += input_tokens
            if output_tokens is not None:
                ctx.total_output_tokens += output_tokens
        update_current_mlflow_trace(response_preview=preview)
        capture_last_active_trace_id()


# Matches the full <think>...</think> block that reasoning models like DeepSeek
# emit when they externalise chain-of-thought. The block must be stripped *before*
# DSPy's ChatAdapter scans for [[ ## field ## ]] delimiters, otherwise the
# closing </think> tag interrupts field parsing and forces an expensive JSONAdapter
# retry (adds ~8 s per turn).
_THINK_TAG_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
# Some models emit <think> in one token batch and </think> in another, so the
# paired regex above leaves orphaned tags after stripping complete pairs.
_ORPHAN_THINK_OPEN_RE = re.compile(r"<think>", re.IGNORECASE)
_ORPHAN_THINK_CLOSE_RE = re.compile(r"</think>", re.IGNORECASE)


def _strip_think_tags(text: str) -> str:
    text = _THINK_TAG_RE.sub("", text)
    text = _ORPHAN_THINK_OPEN_RE.sub("", text)
    text = _ORPHAN_THINK_CLOSE_RE.sub("", text)
    return text.lstrip("\n")


class ThinkTagStripCallback(BaseCallback):
    """Strip <think>…</think> blocks from LM completions before adapter parsing.

    DeepSeek-V4-Pro (and other reasoning models) emit a chain-of-thought block
    wrapped in ``<think>…</think>`` tags. When the closing tag appears inside a
    DSPy ``[[ ## field ## ]]`` block the ``ChatAdapter`` cannot locate subsequent
    output fields and raises ``AdapterParseError``, silently falling back to a
    second LM call via ``JSONAdapter``.  This callback removes the think block
    in-place on the raw LiteLLM response dict so no adapter ever sees it.
    """

    def on_lm_end(
        self,
        call_id: str,
        outputs: dict[str, Any] | None,
        exception: Exception | None = None,
    ) -> None:
        _ = call_id, exception
        if not isinstance(outputs, dict):
            return
        choices = outputs.get("choices")
        if not isinstance(choices, list):
            return
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            message = choice.get("message")
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str) and _THINK_TAG_RE.search(content):
                    message["content"] = _strip_think_tags(content)
            # text-completion style
            text = choice.get("text")
            if isinstance(text, str) and _THINK_TAG_RE.search(text):
                choice["text"] = _strip_think_tags(text)


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


def trace_to_dataset_row(trace: Any, *, config: MlflowConfig | None = None) -> dict[str, Any]:
    from .mlflow_traces import trace_to_dataset_row as _impl

    return _impl(trace, config=config)


def search_annotated_trace_rows(
    *,
    config: MlflowConfig | None = None,
    max_results: int = 5000,
) -> list[dict[str, Any]]:
    from .mlflow_traces import search_annotated_trace_rows as _impl

    return _impl(config=config, max_results=max_results)


__all__ = [
    "FleetMlflowTraceCallback",
    "ThinkTagStripCallback",
    "MlflowTraceRequestContext",
    "capture_last_active_trace_id",
    "current_request_context",
    "flush_mlflow_traces",
    "get_mlflow_config",
    "get_mlflow_experiment_id",
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
