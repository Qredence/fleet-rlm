"""MLflow lifecycle, trace correlation, and offline trace export helpers."""

from __future__ import annotations

import contextvars
import hashlib
import json
import logging
import os
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from threading import Lock
from typing import TYPE_CHECKING, Any, Callable
from urllib.parse import urlsplit, urlunsplit

import dspy
from dspy.utils.callback import BaseCallback

from .config import MlflowConfig

if TYPE_CHECKING:
    from mlflow.entities.trace import Trace

logger = logging.getLogger(__name__)

_CLIENT_LOCK = Lock()
_TRACE_ID_LOCK = Lock()
_INIT_IDENTITY: tuple[Any, ...] | None = None
_INIT_ATTEMPTED = False
_LAST_INIT_WAS_AUTH_FAILURE = False
_INITIALIZED = False
_ACTIVE_CONFIG: MlflowConfig | None = None
_TRACE_IDS_BY_CLIENT_REQUEST_ID: dict[str, str] = {}


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
    contextvars.ContextVar("fleet_rlm_mlflow_request_context", default=None)
)
_CURRENT_TRACE_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "fleet_rlm_mlflow_trace_id",
    default=None,
)


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


def _existing_trace_callback() -> "FleetMlflowTraceCallback | None":
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

    global _INIT_ATTEMPTED, _LAST_INIT_WAS_AUTH_FAILURE
    global _INIT_IDENTITY, _INITIALIZED, _ACTIVE_CONFIG
    with _CLIENT_LOCK:
        _ACTIVE_CONFIG = resolved

        # Preserve idempotency after success, and avoid hammering the same
        # tracking endpoint after an auth-forbidden failure until auth changes.
        if (
            _INIT_ATTEMPTED
            and _INIT_IDENTITY == identity
            and (_INITIALIZED or _LAST_INIT_WAS_AUTH_FAILURE)
        ):
            return _INITIALIZED

        if not resolved.enabled:
            _INIT_ATTEMPTED = True
            _LAST_INIT_WAS_AUTH_FAILURE = False
            _INITIALIZED = False
            _INIT_IDENTITY = identity
            return False

        mlflow = _import_mlflow()
        if mlflow is None:
            logger.debug("MLflow is not installed; skipping runtime initialization.")
            _INIT_ATTEMPTED = True
            _LAST_INIT_WAS_AUTH_FAILURE = False
            _INITIALIZED = False
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

            _INIT_ATTEMPTED = True
            _LAST_INIT_WAS_AUTH_FAILURE = False
            _INITIALIZED = True
            _INIT_IDENTITY = identity
            return True
        except Exception as exc:
            _INIT_ATTEMPTED = True
            _LAST_INIT_WAS_AUTH_FAILURE = _is_auth_forbidden_failure(exc)
            _log_mlflow_initialization_failure(
                exc,
                tracking_uri=resolved.tracking_uri,
            )
            _INITIALIZED = False
            _INIT_IDENTITY = identity
            return False


def flush_mlflow_traces(*, terminate: bool = False) -> None:
    """Flush pending async MLflow trace logging."""
    mlflow = _import_mlflow()
    if mlflow is None:
        return
    trace_exporter_getter: Callable[[], Any] | None = None
    try:
        from mlflow.tracing.provider import _get_trace_exporter
    except Exception:
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

    config = get_mlflow_config()
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
            logger.debug("Failed to inspect current MLflow span.", exc_info=True)

    get_active_trace_id = getattr(mlflow, "get_active_trace_id", None)
    if callable(get_active_trace_id):
        try:
            return bool(get_active_trace_id())
        except Exception:
            logger.debug("Failed to inspect current MLflow trace id.", exc_info=True)

    return False


def update_current_mlflow_trace(
    *,
    response_preview: str | None = None,
) -> str | None:
    """Apply the current request context to the active MLflow trace."""
    context = current_request_context()
    if context is None:
        return None

    mlflow = _import_mlflow()
    if mlflow is None:
        return None
    if not _has_active_mlflow_trace(mlflow):
        return capture_last_active_trace_id()

    try:
        config = get_mlflow_config()
        model_id = context.model_id or config.active_model_id
        mlflow.update_current_trace(
            client_request_id=context.client_request_id,
            metadata=_trace_metadata_from_context(context),
            request_preview=_trim_preview(context.request_preview),
            response_preview=_trim_preview(response_preview),
            model_id=model_id,
        )
    except Exception:
        logger.debug("MLflow trace update skipped.", exc_info=True)

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

    mlflow = _import_mlflow()
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
    config = get_mlflow_config()
    if not config.enabled:
        return {}
    if _import_mlflow() is None:
        return {}
    if not initialize_mlflow(config):
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
        _ = (call_id, exception)
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
        _ = (call_id, exception)
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
        update_current_mlflow_trace(response_preview=preview)
        capture_last_active_trace_id()


def _trace_experiment_ids(config: MlflowConfig) -> list[str]:
    mlflow = _import_mlflow()
    if mlflow is None:
        return []
    if not initialize_mlflow(config):
        return []
    experiment = mlflow.get_experiment_by_name(config.experiment)
    if experiment is None:
        return []
    return [experiment.experiment_id]


def resolve_trace_by_client_request_id(
    client_request_id: str,
    *,
    config: MlflowConfig | None = None,
    max_results: int = 5000,
) -> "Trace | None":
    """Resolve the most recent trace for a given client request id."""
    mlflow = _import_mlflow()
    if mlflow is None:
        return None

    resolved = config or get_mlflow_config()
    experiment_ids = _trace_experiment_ids(resolved)
    if not experiment_ids:
        return None

    try:
        traces = mlflow.search_traces(
            experiment_ids=experiment_ids,
            filter_string=(
                "trace.client_request_id = "
                f"'{_mlflow_string_literal(client_request_id)}'"
            ),
            max_results=max_results,
            return_type="list",
            include_spans=False,
        )
    except Exception:
        logger.warning(
            "Failed to search MLflow traces for client request id '%s'.",
            _sanitize_log_field(client_request_id),
            exc_info=True,
        )
        return None
    matches = [
        trace
        for trace in traces
        if getattr(getattr(trace, "info", None), "client_request_id", None)
        == client_request_id
    ]
    if not matches:
        return None
    matches.sort(
        key=lambda trace: int(
            getattr(getattr(trace, "info", None), "timestamp_ms", 0) or 0
        ),
        reverse=True,
    )
    return matches[0]


def resolve_trace(
    *,
    trace_id: str | None = None,
    client_request_id: str | None = None,
    config: MlflowConfig | None = None,
) -> "Trace | None":
    """Resolve a trace by explicit trace id or fallback client request id."""
    mlflow = _import_mlflow()
    if mlflow is None:
        return None

    if trace_id:
        try:
            return mlflow.get_trace(trace_id)
        except Exception:
            logger.warning(
                "Failed to load MLflow trace '%s'.",
                _sanitize_log_field(trace_id),
                exc_info=True,
            )
            return None

    if client_request_id:
        return resolve_trace_by_client_request_id(
            client_request_id,
            config=config,
        )
    return None


def log_trace_feedback(
    *,
    trace_id: str,
    is_correct: bool,
    source_id: str,
    comment: str | None = None,
    expected_response: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, bool]:
    """Log human feedback and optional ground-truth expectation for a trace."""
    mlflow = _import_mlflow()
    if mlflow is None:
        raise RuntimeError("MLflow is not installed.")

    source = mlflow.entities.AssessmentSource(
        source_type="HUMAN", source_id=source_id or "anonymous"
    )
    mlflow.log_feedback(
        trace_id=trace_id,
        name="response_is_correct",
        value=is_correct,
        source=source,
        rationale=(comment or None),
        metadata=metadata,
    )

    expectation_logged = False
    candidate = (expected_response or "").strip()
    if candidate:
        mlflow.log_expectation(
            trace_id=trace_id,
            name="expected_response",
            value=candidate,
            source=source,
            metadata=metadata,
        )
        expectation_logged = True

    return {
        "feedback_logged": True,
        "expectation_logged": expectation_logged,
    }


def _parse_trace_metadata_field(
    metadata: dict[str, Any],
    key: str,
) -> Any:
    raw = metadata.get(key)
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    if not isinstance(raw, str):
        return raw
    text = raw.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _trace_assessment_dicts(trace: "Trace") -> list[dict[str, Any]]:
    assessments = []
    try:
        raw = trace.search_assessments()
    except Exception:
        raw = []
    for item in raw or []:
        if hasattr(item, "to_dictionary"):
            data = item.to_dictionary()
        elif hasattr(item, "to_dict"):
            data = item.to_dict()
        else:
            data = None
        if isinstance(data, dict):
            assessments.append(data)
    return assessments


def trace_to_dataset_row(trace: "Trace") -> dict[str, Any]:
    """Convert an MLflow trace into an evaluation/export dataset row."""
    payload = trace.to_dict()
    info = payload.get("info", {}) if isinstance(payload, dict) else {}
    metadata = info.get("trace_metadata", {}) if isinstance(info, dict) else {}

    inputs = _parse_trace_metadata_field(metadata, "mlflow.traceInputs")
    outputs = _parse_trace_metadata_field(metadata, "mlflow.traceOutputs")
    if inputs is None:
        inputs = info.get("request_preview")
    if outputs is None:
        outputs = info.get("response_preview")

    expectations: dict[str, Any] = {}
    feedback: dict[str, Any] = {}
    for assessment in _trace_assessment_dicts(trace):
        name = str(assessment.get("assessment_name") or "assessment")
        source = assessment.get("source") or {}
        source_id = source.get("source_id") if isinstance(source, dict) else None

        expectation = assessment.get("expectation")
        if isinstance(expectation, dict) and "value" in expectation:
            expectations[name] = expectation["value"]

        feedback_payload = assessment.get("feedback")
        if isinstance(feedback_payload, dict) and "value" in feedback_payload:
            feedback[name] = {
                "value": feedback_payload["value"],
                "rationale": assessment.get("rationale"),
                "source_id": source_id,
            }

    row: dict[str, Any] = {
        "trace_id": info.get("trace_id"),
        "client_request_id": info.get("client_request_id"),
        "inputs": inputs,
        "outputs": outputs,
        "expectations": expectations,
    }
    if feedback:
        row["feedback"] = feedback
    return row


def search_annotated_trace_rows(
    *,
    config: MlflowConfig | None = None,
    max_results: int = 5000,
) -> list[dict[str, Any]]:
    """Search the configured experiment and return rows for annotated traces."""
    mlflow = _import_mlflow()
    if mlflow is None:
        return []

    resolved = config or get_mlflow_config()
    experiment_ids = _trace_experiment_ids(resolved)
    if not experiment_ids:
        return []

    try:
        traces = mlflow.search_traces(
            experiment_ids=experiment_ids,
            max_results=max_results,
            return_type="list",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Failed to search MLflow traces for experiments %s: %s",
            experiment_ids,
            exc,
        )
        return []
    rows: list[dict[str, Any]] = []
    for trace in traces:
        row = trace_to_dataset_row(trace)
        if row.get("expectations") or row.get("feedback"):
            rows.append(row)
    rows.sort(
        key=lambda row: str(row.get("trace_id") or ""),
    )
    return rows


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
