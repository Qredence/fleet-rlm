"""MLflow tracing configuration for the Fleet RLM backend.

Tracing is engineering observability only — it must never affect Turn
outcomes.  All setup is fail-soft: if MLflow or the configured tracking
backend is unavailable, the backend continues without traces.

Fleet TOML policy (resolved through ``Settings``):
    mlflow.tracing_enabled  - master gate (field default false; committed
                              [defaults.mlflow] policy enables it by default)
    mlflow.experiment_name  - experiment passed to set_experiment
    mlflow.tracking_uri     - tracking target
    mlflow.expose_trace_id  - surface trace ids on Turn SSE metadata
    mlflow.trace_content_enabled - bounded sanitized content gate; enabled by default
    mlflow.trace_content_max_chars - per-field bound for readable content
    mlflow.http_request_timeout_seconds - bounded MLflow HTTP request timeout

Databricks auth remains outside FLEET secrets (SDK/CLI conventions):
    DATABRICKS_HOST  - Workspace URL (e.g. https://...gcp.databricks.com)
    DATABRICKS_TOKEN - PAT or service principal token (or databricks-cli keyring)
"""

from __future__ import annotations

import json
import logging
import os
import re
import socket
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from typing import TYPE_CHECKING, Any, Literal, cast, get_args
from urllib.parse import urlparse
from uuid import UUID

if TYPE_CHECKING:
    from fleet_rlm.config.settings import Settings

from fleet_rlm.config.settings import FleetConfigurationError

logger = logging.getLogger(__name__)


_DEFAULT_TRACKING_URI = "databricks"
_TRACE_DESTINATION_TAG = "mlflow.experiment.databricksTraceDestinationPath"
_EXPERIMENT_PURPOSE_TAG = "fleet.experiment.purpose"
_TRACE_CONTENT_MAX_CHARS = 10_000
_TRACE_CONTENT_ENABLED = False
# Set only after configure_tracing succeeds. Policy may request tracing while the
# tracking backend is absent; Turn spans must not enter MLflow's HTTP retry loop
# and starve claim heartbeats.
_TRACING_ACTIVE = False

# MLflow and DSPy keep process-global configuration. Keep the small amount of
# state Fleet changes so an application lifespan can restore it on shutdown.
# Databricks credentials are only bridged when absent from the process
# environment, so they are never copied into this snapshot.
_TRACE_ENVIRONMENT_KEYS = frozenset(
    {
        "DATABRICKS_HOST",
        "DATABRICKS_TOKEN",
        "MLFLOW_DISABLE_AGENT_HINT",
        "MLFLOW_TRACE_SAMPLING_RATIO",
        "MLFLOW_ENABLE_ASYNC_TRACE_LOGGING",
        "MLFLOW_ASYNC_TRACE_LOGGING_MAX_QUEUE_SIZE",
        "MLFLOW_ASYNC_TRACE_LOGGING_MAX_WORKERS",
        "MLFLOW_ASYNC_TRACE_LOGGING_RETRY_TIMEOUT",
        "MLFLOW_HTTP_REQUEST_TIMEOUT",
        "MLFLOW_TRACING_SQL_WAREHOUSE_ID",
    }
)
_TRACE_ENVIRONMENT_SNAPSHOT: dict[str, str | None] = {}
_TRACE_ENVIRONMENT_APPLIED: dict[str, str] = {}
_UNKNOWN_TRACKING_URI = object()
_TRACKING_URI_BEFORE: object = _UNKNOWN_TRACKING_URI
_TRACKING_URI_APPLIED: str | None = None
_TRACE_CONFIG_CONTEXT: Any | None = None
_DSPY_AUTOLOG_ENABLED = False
_MLFLOW_EXPORT_DISTRIBUTIONS = (
    ("mlflow", "3.16.0"),
    ("mlflow-skinny", "3.16.0"),
    ("mlflow-tracing", "3.16.0"),
    ("opentelemetry-sdk", "1.44.0"),
)


def _mlflow_export_versions_are_certified() -> bool:
    """Return whether the installed MLflow/OTel export distributions are certified."""
    try:
        versions = tuple(package_version(name) for name, _expected in _MLFLOW_EXPORT_DISTRIBUTIONS)
    except PackageNotFoundError:
        logger.warning("MLflow export compatibility dependencies are unavailable; continuing without traces")
        return False
    expected = tuple(expected for _name, expected in _MLFLOW_EXPORT_DISTRIBUTIONS)
    if versions != expected:
        logger.warning("MLflow export compatibility is uncertified; continuing without traces")
        return False
    return True


def is_tracing_active() -> bool:
    """Return whether configure_tracing successfully activated Turn spans."""
    return _TRACING_ACTIVE


def set_tracing_active_for_tests(active: bool) -> None:
    """Test-only override for Turn-span gate without a real tracking backend."""
    global _DSPY_AUTOLOG_ENABLED, _TRACE_CONFIG_CONTEXT, _TRACKING_URI_APPLIED, _TRACKING_URI_BEFORE
    global _TRACE_CONTENT_ENABLED, _TRACE_CONTENT_MAX_CHARS, _TRACING_ACTIVE
    _TRACING_ACTIVE = active
    if not active:
        # Test doubles are installed and removed per test. Do not let a prior
        # fake configuration make a later test look like a live process
        # lifespan, and do not import or mutate the real MLflow module here.
        _DSPY_AUTOLOG_ENABLED = False
        _TRACE_CONFIG_CONTEXT = None
        _TRACKING_URI_BEFORE = _UNKNOWN_TRACKING_URI
        _TRACKING_URI_APPLIED = None
        _TRACE_CONTENT_ENABLED = False
        _TRACE_CONTENT_MAX_CHARS = 10_000
        _TRACE_ENVIRONMENT_SNAPSHOT.clear()
        _TRACE_ENVIRONMENT_APPLIED.clear()


_CREDENTIAL_KEYS = frozenset(
    {"api_key", "authorization", "credential", "credentials", "password", "private_key", "secret", "token"}
)
_OPERATIONAL_TEXT_KEYS = frozenset(
    {
        "cache",
        "delivery",
        "detail_type",
        "artifact_id",
        "artifact_kind",
        "attachment_id",
        "affordances",
        "action_status",
        "call_index",
        "cleanup_owned",
        "code_chars",
        "engine",
        "failure_category",
        "kind",
        "history_length_before",
        "input_keys",
        "iteration",
        "max_iters",
        "max_llm_calls",
        "model",
        "model_type",
        "mlflow_span_type",
        "name",
        "path",
        "phase_status",
        "provider",
        "provider_type",
        "prompt_chars",
        "role",
        "result_kind",
        "recovered",
        "recursive_depth",
        "phase",
        "schema_id",
        "schema_version",
        "skill_id",
        "status",
        "tool_call_id",
        "tool_name",
        "trace_id",
        "termination_mode",
        "type",
        "trust",
        "version",
        "mlflow_trace_request_id",
        "mlflow_experiment_id",
        "mlflow_llm_model",
        "mlflow_llm_provider",
        "runtime_variant",
        "program_fingerprint",
        "image_identity",
        "settlement_status",
        "settlement_durable",
        "run_id",
        "session_id",
        "child_call_id",
    }
)


def _set_trace_content_max_chars(max_chars: int) -> None:
    """Apply the bounded MLflow trace payload limit for this process."""
    global _TRACE_CONTENT_MAX_CHARS
    try:
        normalized_max_chars = int(max_chars)
    except (TypeError, ValueError):
        normalized_max_chars = 10_000
    _TRACE_CONTENT_MAX_CHARS = max(256, min(normalized_max_chars, 50_000))


def trace_content_max_chars() -> int:
    """Return the configured per-field bound for readable trace content."""
    return _TRACE_CONTENT_MAX_CHARS


def _apply_trace_environment(name: str, value: str) -> None:
    """Set one Fleet-owned environment value while retaining a safe restore point."""
    if name not in _TRACE_ENVIRONMENT_KEYS:
        raise ValueError(f"unsupported MLflow environment key: {name}")
    if name not in _TRACE_ENVIRONMENT_SNAPSHOT:
        _TRACE_ENVIRONMENT_SNAPSHOT[name] = os.environ.get(name)
    os.environ[name] = value
    _TRACE_ENVIRONMENT_APPLIED[name] = value


def _restore_trace_environment() -> None:
    """Restore Fleet-owned environment values unless another owner changed them."""
    for name, original in list(_TRACE_ENVIRONMENT_SNAPSHOT.items()):
        applied = _TRACE_ENVIRONMENT_APPLIED.get(name)
        if applied is not None and os.environ.get(name) == applied:
            if original is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = original
    _TRACE_ENVIRONMENT_SNAPSHOT.clear()
    _TRACE_ENVIRONMENT_APPLIED.clear()


def _remember_tracking_uri(mlflow: Any) -> None:
    """Remember MLflow's current tracking URI before Fleet changes it."""
    global _TRACKING_URI_BEFORE
    if _TRACKING_URI_BEFORE is not _UNKNOWN_TRACKING_URI:
        return
    getter = getattr(mlflow, "get_tracking_uri", None)
    if not callable(getter):
        return
    try:
        _TRACKING_URI_BEFORE = getter()
    except Exception:
        # A missing or unavailable getter should not make optional tracing
        # affect application startup.
        _TRACKING_URI_BEFORE = _UNKNOWN_TRACKING_URI


def _restore_tracking_uri(mlflow: Any) -> bool:
    """Restore MLflow's URI only when Fleet still owns the current value."""
    global _TRACKING_URI_APPLIED, _TRACKING_URI_BEFORE
    applied = _TRACKING_URI_APPLIED
    before = _TRACKING_URI_BEFORE
    restored = True
    try:
        setter = getattr(mlflow, "set_tracking_uri", None)
        getter = getattr(mlflow, "get_tracking_uri", None)
        if (
            applied is not None
            and before is not _UNKNOWN_TRACKING_URI
            and callable(setter)
            and callable(getter)
            and getter() == applied
        ):
            setter(before)
    except Exception:
        logger.debug("MLflow tracking URI restore skipped")
        restored = False
    finally:
        if restored:
            _TRACKING_URI_APPLIED = None
            _TRACKING_URI_BEFORE = _UNKNOWN_TRACKING_URI
    return restored


def _restore_trace_processors() -> bool:
    """Restore the MLflow span-processor configuration replaced by Fleet."""
    global _TRACE_CONFIG_CONTEXT
    context = _TRACE_CONFIG_CONTEXT
    if context is None:
        return True
    restore = getattr(context, "__exit__", None)
    if not callable(restore):
        _TRACE_CONFIG_CONTEXT = None
        return True
    try:
        restore(None, None, None)
    except Exception:
        logger.debug("MLflow span-processor restore skipped")
        return False
    _TRACE_CONFIG_CONTEXT = None
    return True


def reset_tracing() -> bool:
    """Disable Fleet's DSPy autologging and restore process-global MLflow state."""
    global _DSPY_AUTOLOG_ENABLED, _TRACE_CONTENT_ENABLED, _TRACE_CONTENT_MAX_CHARS
    global _TRACKING_URI_APPLIED, _TRACKING_URI_BEFORE, _TRACING_ACTIVE

    cleanup_ok = True
    mlflow: Any | None = None
    if _DSPY_AUTOLOG_ENABLED:
        try:
            import mlflow as mlflow_module

            mlflow = mlflow_module
            dspy_module = getattr(mlflow_module, "dspy", None)
            autolog = getattr(dspy_module, "autolog", None)
            if callable(autolog):
                # MLflow's DSPy integration removes only its callback. Running
                # this on the same owner thread as configure_tracing keeps
                # DSPy's configure-owner invariant intact.
                autolog(disable=True, silent=True)
        except Exception:
            # Cleanup must never make FastAPI shutdown fail, and the exception
            # may include provider or credential material.
            logger.warning("MLflow DSPy autolog cleanup failed; continuing shutdown")
            cleanup_ok = False

    if mlflow is None and _TRACKING_URI_APPLIED is not None:
        try:
            import mlflow as mlflow_module

            mlflow = mlflow_module
        except Exception:
            mlflow = None

    cleanup_ok = _restore_trace_processors() and cleanup_ok
    if mlflow is not None:
        cleanup_ok = _restore_tracking_uri(mlflow) and cleanup_ok
    else:
        # There is no SDK object to restore, but the bookkeeping must still be
        # cleared so a later explicit setup starts from a clean boundary.
        _TRACKING_URI_BEFORE = _UNKNOWN_TRACKING_URI
        _TRACKING_URI_APPLIED = None
    _restore_trace_environment()
    _TRACING_ACTIVE = False
    _TRACE_CONTENT_ENABLED = False
    _TRACE_CONTENT_MAX_CHARS = 10_000
    if cleanup_ok:
        _DSPY_AUTOLOG_ENABLED = False
    return cleanup_ok


def _abort_tracing_setup(message: str) -> bool:
    """Log a safe setup outcome, undo partial state, and return ``False``."""
    logger.warning(message)
    reset_tracing()
    return False


def _normalize_trace_key(key: str | None) -> str:
    """Normalize dotted, hyphenated, and camel-case span keys for policy checks."""
    raw = (key or "").strip()
    raw = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", raw)
    return re.sub(r"[^a-zA-Z0-9]+", "_", raw).strip("_").lower()


def trace_content_preview(value: object) -> str:
    """Return a readable, bounded preview for MLflow trace-level metadata."""
    from fleet_rlm.rlm.result import sanitize_trace_text

    if not _TRACE_CONTENT_ENABLED:
        return "[content suppressed]"
    return sanitize_trace_text(str(value or ""), max_len=_TRACE_CONTENT_MAX_CHARS)


def _sanitize_mlflow_value(
    value: object,
    *,
    key: str | None = None,
    depth: int = 0,
) -> object:
    """Preserve readable trace values while protecting secrets and bounding payloads."""
    if depth >= 8:
        return "[redacted depth]"
    normalized_key = _normalize_trace_key(key)
    if (
        normalized_key in {"reasoning", "reasoning_content", "chain_of_thought", "thinking", "system_prompt"}
        and not _TRACE_CONTENT_ENABLED
    ):
        return "[content suppressed]"
    if normalized_key in _CREDENTIAL_KEYS or normalized_key.endswith(
        tuple(f"_{credential_key}" for credential_key in _CREDENTIAL_KEYS)
    ):
        return "[redacted]"
    if isinstance(value, Mapping):
        return {
            str(item_key)[:128]: _sanitize_mlflow_value(
                item,
                key=str(item_key),
                depth=depth + 1,
            )
            for item_key, item in list(value.items())[:50]
        }
    if isinstance(value, (list, tuple)):
        child_key = key if normalized_key in _OPERATIONAL_TEXT_KEYS else None
        return [
            _sanitize_mlflow_value(
                item,
                key=child_key,
                depth=depth + 1,
            )
            for item in list(value)[:50]
        ]
    if isinstance(value, str):
        from fleet_rlm.rlm.result import sanitize_trace_text

        if normalized_key in _OPERATIONAL_TEXT_KEYS:
            return sanitize_trace_text(value, max_len=256)
        if not _TRACE_CONTENT_ENABLED:
            return "[content suppressed]"
        return sanitize_trace_text(value, max_len=_TRACE_CONTENT_MAX_CHARS)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return type(value).__name__


def _sanitize_mlflow_span(span: object) -> None:
    """
    Protect secrets and bound an MLflow span's inputs, outputs, and attributes before export.

    Real SDK spans are cleared before sanitization, so failure cannot export
    the original content. Lightweight test doubles retain the public setter seam.

    Parameters:
        span (object): MLflow span to sanitize.
    """
    if getattr(span, "_span", None) is not None:
        _sanitize_live_mlflow_span(span)
        return
    try:
        inputs = getattr(span, "inputs", None)
        if inputs is not None:
            setter = getattr(span, "set_inputs", None)
            if callable(setter):
                setter(_sanitize_mlflow_value(inputs))

        outputs = getattr(span, "outputs", None)
        if outputs is not None:
            setter = getattr(span, "set_outputs", None)
            if callable(setter):
                setter(_sanitize_mlflow_value(outputs))

        attributes = getattr(span, "attributes", None)
        if isinstance(attributes, Mapping):
            setter = getattr(span, "set_attributes", None)
            if callable(setter):
                sanitized = _sanitize_mlflow_value(attributes)
                if isinstance(sanitized, dict):
                    setter(sanitized)
    except Exception:
        # This branch supports non-exporting test doubles only. Real SDK spans
        # use the clear-before-restore boundary above.
        logger.debug("MLflow test span sanitization failed")


def _sanitize_live_mlflow_span(span: Any) -> None:
    """MLflow 3.16.0 export compatibility, certified against actual SDK spans.

    Public setters merge attributes and cannot remove exception events or
    attachments. Keep the necessary private access here, at the existing export
    owner. Snapshot serialized values, detach ALL content, then restore only
    sanitized values. A sanitizer or setter failure leaves a content-free span.
    Parent IDs and timing stay intact. No process-global state changes here.
    """
    from opentelemetry.attributes import BoundedAttributes
    from opentelemetry.sdk.util import BoundedList
    from opentelemetry.trace import Status

    otel = span._span
    raw_attributes = otel._attributes
    raw_name = otel._name
    raw_links = list(getattr(span, "_links", ()))
    # This executes synchronously before OTel end/export. Never rely on MLflow
    # propagating processor errors: its processor runner suppresses them.
    otel._attributes = BoundedAttributes(maxlen=128, immutable=False)
    otel._events = BoundedList(0)
    otel._links = BoundedList(0)
    otel._name = "Fleet.operation"
    otel._status = Status(otel._status.status_code)
    span._links = []
    span._attachments.clear()
    try:

        def decode(value: object) -> object:
            if isinstance(value, str):
                try:
                    return json.loads(value)
                except json.JSONDecodeError:
                    pass  # OTel-native attributes need not be JSON encoded.
            return value

        # Decode after detaching raw attributes. Preserve routing identities
        # even when ordinary fields exceed the bounded content allowance.
        routing_keys = {
            "mlflow.traceRequestId",
            "mlflow.spanType",
            "mlflow.experimentId",
            "mlflow.chat.tokenUsage",
            "mlflow.llm.model",
            "mlflow.llm.provider",
        }
        decoded = {}
        for key, value in raw_attributes.items():
            if key in routing_keys:
                decoded[key] = decode(value)
        for key, value in raw_attributes.items():
            if key not in routing_keys and len(decoded) < 50:
                if not _TRACE_CONTENT_ENABLED and _normalize_trace_key(key) not in _OPERATIONAL_TEXT_KEYS:
                    continue
                decoded[key] = decode(value)
        sanitized = _sanitize_mlflow_value(decoded)
        if not isinstance(sanitized, dict):
            raise ValueError("invalid sanitized attributes")
        # Setters are additive, but now operate on an empty backing store.
        span.set_attributes(sanitized)
        # MLflow 3.16 stores links in both the MLflow LiveSpan and its backing
        # OpenTelemetry span. Keep the OTel collection bounded but appendable
        # while restoring the one validated Fleet relationship link; a zero-
        # capacity collection would make ``LiveSpan.add_link`` silently omit
        # the restored link from the exported OTel representation.
        otel._links = BoundedList(128)
        # Restore only the one Fleet relationship link, with validated IDs and
        # a fixed attribute set. Any SDK/autolog link with an unknown shape is
        # dropped at this privacy boundary.
        for link in raw_links:
            attributes = getattr(link, "attributes", None)
            if not isinstance(attributes, Mapping) or attributes.get("fleet.relationship") != "preparation":
                continue
            trace_id = getattr(link, "trace_id", None)
            span_id = getattr(link, "span_id", None)
            if not isinstance(trace_id, str) or not isinstance(span_id, str):
                continue
            try:
                from mlflow.entities import Link

                span.add_link(
                    Link(
                        trace_id=trace_id[:_PREPARATION_TRACE_ID_MAX_CHARS],
                        span_id=span_id[:32],
                        attributes={"fleet.relationship": "preparation"},
                    )
                )
            except Exception:
                # A malformed optional link is observability-only and must not
                # make the span exporter or Turn fail.
                continue
        otel._name = str(_sanitize_mlflow_value(raw_name, key="name"))
    except Exception:
        # A setter may have partially restored fields before failing. Discard
        # those too; do not log the exception, which may contain raw content.
        otel._attributes = BoundedAttributes(maxlen=128, immutable=False)
        otel._links = BoundedList(0)
        span._links = []
        otel._name = "Fleet.redaction_failed"
        logger.debug("MLflow span content suppressed after redaction failure")


def _local_tracking_server_available(tracking_uri: str) -> bool:
    """
    Check whether an HTTP(S) tracking server is reachable within 0.5 seconds.

    Parameters:
        tracking_uri (str): Tracking server URI to probe.

    Returns:
        bool: `True` if the URI is not HTTP(S) or the server is reachable, `False` otherwise.
    """
    parsed = urlparse(tracking_uri)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return True
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((parsed.hostname, port), timeout=0.5):
            return True
    except OSError:
        return False


def _validate_experiment_trace_location(settings: Settings) -> None:
    """Verify the experiment's trace location matches Fleet configuration.

    When the experiment already exists and is linked to a Unity Catalog trace
    location, compare the stored destination path with the Fleet config. A
    mismatch will cause ``mlflow.set_experiment`` to raise ``MlflowException``
    (silently caught by ``configure_tracing``), so this preflight check catches
    the mismatch early with a clear, actionable error.

    Best-effort: if mlflow is unavailable or the Databricks call fails, it
    returns silently — the normal code path will handle the mismatch later.

    Raises:
        FleetConfigurationError: If the existing experiment's trace destination
            path differs from the configured Unity Catalog settings.
    """
    import mlflow

    experiment_name = settings.mlflow_experiment_name
    if experiment_name is None:
        return

    try:
        from mlflow.exceptions import MlflowException
    except ImportError:
        return

    try:
        mlflow.set_tracking_uri(_DEFAULT_TRACKING_URI)
        experiment = mlflow.get_experiment_by_name(experiment_name)
    except (MlflowException, AttributeError):
        return  # Unavailable — will fail at set_experiment time if mismatch

    if experiment is None:
        return

    existing_destination = experiment.tags.get(_TRACE_DESTINATION_TAG)
    if existing_destination is None:
        return

    expected_destination = (
        f"{settings.mlflow_trace_catalog}.{settings.mlflow_trace_schema}.{settings.mlflow_trace_table_prefix}"
    )

    if existing_destination != expected_destination:
        raise FleetConfigurationError(
            f"MLflow experiment {settings.mlflow_experiment_name!r} is already "
            f"linked to trace location {existing_destination!r}, but Fleet config "
            f"specifies {expected_destination!r}. "
            "Update FLEET_MLFLOW_TRACE_CATALOG, FLEET_MLFLOW_TRACE_SCHEMA, and "
            "FLEET_MLFLOW_TRACE_TABLE_PREFIX in .env to match the existing experiment, "
            "or set FLEET_MLFLOW_EXPERIMENT_NAME to create a new experiment."
        )


def _apply_experiment_purpose(settings: Settings) -> None:
    """Record the configured purpose on the active MLflow experiment.

    Purpose tags separate runtime, evaluation, and optimization experiments
    without recreating managed experiments or moving trace locations. An
    experiment already carrying a different recorded purpose is an
    intentional configuration error; an unavailable backend or a fresh
    experiment stays best-effort.
    """
    import mlflow

    purpose = settings.mlflow_experiment_purpose
    experiment_name = settings.mlflow_experiment_name
    if not purpose or experiment_name is None:
        return

    try:
        from mlflow.exceptions import MlflowException
    except ImportError:
        return

    try:
        experiment = mlflow.get_experiment_by_name(experiment_name)
    except (MlflowException, AttributeError):
        return  # Unavailable — the experiment tag is best-effort metadata

    if experiment is None:
        return

    existing_purpose = experiment.tags.get(_EXPERIMENT_PURPOSE_TAG)
    if existing_purpose is not None and existing_purpose != purpose:
        raise FleetConfigurationError(
            f"MLflow experiment {experiment_name!r} is already marked with purpose "
            f"{existing_purpose!r}, but Fleet config specifies {purpose!r}. "
            "Keep one purpose per experiment: update mlflow.experiment_purpose in the selected "
            "Fleet TOML policy to the recorded purpose or choose a different experiment name."
        )

    if existing_purpose == purpose:
        return

    try:
        # The fluent setter tags the experiment just selected by set_experiment.
        mlflow.set_experiment_tag(_EXPERIMENT_PURPOSE_TAG, purpose)
    except (MlflowException, AttributeError):
        logger.warning("Could not record experiment purpose tag on %r; continuing", experiment_name)


def configure_tracing(settings: Settings) -> bool:
    """Configure fail-soft MLflow tracing and return whether tracing is active.

    Tracing remains inactive when disabled by policy, required settings are
    missing, or setup fails. A successful configuration is idempotent until
    :func:`reset_tracing` runs, because MLflow and DSPy both retain process-wide
    configuration. `FleetConfigurationError` still reports an intentional
    Unity Catalog trace-location or experiment-purpose conflict; other failures
    are logged and suppressed.
    """
    global _DSPY_AUTOLOG_ENABLED, _TRACE_CONFIG_CONTEXT, _TRACKING_URI_APPLIED
    global _TRACING_ACTIVE, _TRACE_CONTENT_ENABLED
    if _TRACING_ACTIVE:
        return True
    if (
        _DSPY_AUTOLOG_ENABLED or _TRACE_CONFIG_CONTEXT is not None or _TRACE_ENVIRONMENT_SNAPSHOT
    ) and not reset_tracing():
        logger.warning("MLflow tracing cleanup is incomplete; refusing to reconfigure")
        return False

    _TRACING_ACTIVE = False
    _TRACE_CONTENT_ENABLED = settings.mlflow_trace_content_enabled
    _set_trace_content_max_chars(getattr(settings, "mlflow_trace_content_max_chars", _TRACE_CONTENT_MAX_CHARS))

    if not settings.mlflow_tracing_enabled:
        logger.debug("MLflow tracing is disabled by Fleet policy")
        return False

    tracking_uri = (settings.mlflow_tracking_uri or _DEFAULT_TRACKING_URI).strip()
    required_settings = {"mlflow.experiment_name": settings.mlflow_experiment_name}
    if tracking_uri == _DEFAULT_TRACKING_URI:
        required_settings.update(
            {
                "mlflow.trace_catalog": settings.mlflow_trace_catalog,
                "mlflow.trace_schema": settings.mlflow_trace_schema,
                "mlflow.trace_table_prefix": settings.mlflow_trace_table_prefix,
                "mlflow.tracing_sql_warehouse_id": settings.mlflow_tracing_sql_warehouse_id,
            }
        )
    missing_settings = sorted(name for name, value in required_settings.items() if not value)
    if missing_settings:
        logger.warning(
            "MLflow tracing enabled but required settings are missing; tracing disabled: %s",
            ", ".join(missing_settings),
        )
        return False

    try:
        # The Databricks SDK authenticates from the process environment.
        # Preserve explicit exports and bridge the two external auth variables
        # only when they are present in the already-loaded dotenv values.
        dotenv_values = getattr(settings, "_dotenv_values", {})
        for name in ("DATABRICKS_HOST", "DATABRICKS_TOKEN"):
            if not os.environ.get(name):
                value = dotenv_values.get(name)
                if value:
                    _apply_trace_environment(name, value)

        # Set policy-owned SDK controls before importing MLflow. In particular,
        # the sampling provider reads its ratio during initialization.
        _apply_trace_environment("MLFLOW_DISABLE_AGENT_HINT", "1")
        _apply_trace_environment("MLFLOW_TRACE_SAMPLING_RATIO", str(settings.mlflow_trace_sampling_ratio))
        _apply_trace_environment(
            "MLFLOW_ENABLE_ASYNC_TRACE_LOGGING",
            str(bool(settings.mlflow_async_logging)).lower(),
        )
        _apply_trace_environment(
            "MLFLOW_ASYNC_TRACE_LOGGING_MAX_QUEUE_SIZE",
            str(settings.mlflow_trace_export_queue_size),
        )
        _apply_trace_environment(
            "MLFLOW_ASYNC_TRACE_LOGGING_MAX_WORKERS",
            str(settings.mlflow_trace_export_workers),
        )
        _apply_trace_environment(
            "MLFLOW_ASYNC_TRACE_LOGGING_RETRY_TIMEOUT",
            str(settings.mlflow_trace_export_retry_seconds),
        )
        _apply_trace_environment("MLFLOW_HTTP_REQUEST_TIMEOUT", str(settings.mlflow_http_request_timeout_seconds))
        if tracking_uri == _DEFAULT_TRACKING_URI:
            _apply_trace_environment(
                "MLFLOW_TRACING_SQL_WAREHOUSE_ID",
                cast(str, settings.mlflow_tracing_sql_warehouse_id),
            )

        import mlflow
        import mlflow.dspy

        _remember_tracking_uri(mlflow)

        # The export fence uses private fields because public setters cannot
        # delete events/attachments. Certify a new lock resolution before
        # enabling export with it; execution continues without tracing.
        if getattr(mlflow, "__file__", None) and not _mlflow_export_versions_are_certified():
            return _abort_tracing_setup("MLflow export compatibility is uncertified; continuing without traces")

        # A local MLflow server is optional engineering observability. Probe
        # before set_tracking_uri so a dead HTTP endpoint never becomes the
        # process-global tracking target for later Turn spans. Skip the probe
        # for non-installed/fake mlflow modules (no ``__file__``) used in tests.
        if (
            tracking_uri.startswith(("http://", "https://"))
            and getattr(mlflow, "__file__", None)
            and not _local_tracking_server_available(tracking_uri)
        ):
            return _abort_tracing_setup("MLflow tracking server is unavailable; continuing without traces")

        mlflow.set_tracking_uri(tracking_uri)
        _TRACKING_URI_APPLIED = tracking_uri

        # Preflight: catch trace-location mismatch before set_experiment.
        # FleetConfigurationError propagates — all other failures are soft.
        if tracking_uri == _DEFAULT_TRACKING_URI:
            _validate_experiment_trace_location(settings)

        if tracking_uri == _DEFAULT_TRACKING_URI:
            from mlflow.entities.trace_location import UnityCatalog

            mlflow.set_experiment(
                experiment_name=settings.mlflow_experiment_name,
                trace_location=UnityCatalog(
                    catalog_name=cast(str, settings.mlflow_trace_catalog),
                    schema_name=cast(str, settings.mlflow_trace_schema),
                    table_prefix=cast(str, settings.mlflow_trace_table_prefix),
                ),
            )
        else:
            mlflow.set_experiment(experiment_name=settings.mlflow_experiment_name)

        _apply_experiment_purpose(settings)

        config = getattr(mlflow, "config", None)
        enable_async_logging = getattr(config, "enable_async_logging", None)
        if callable(enable_async_logging):
            enable_async_logging(settings.mlflow_async_logging)

        tracing_api = getattr(mlflow, "tracing", None)
        configure_processors = getattr(tracing_api, "configure", None)
        if not callable(configure_processors):
            return _abort_tracing_setup("MLflow export processor unavailable; continuing without traces")
        _TRACE_CONFIG_CONTEXT = configure_processors(span_processors=[_sanitize_mlflow_span])

        # Enable MLflow's DSPy inference callback. The 3.16 span processor
        # above is the export boundary that bounds readable trace content and
        # protects credentials, paths, and system-prompt dumps. Keep
        # compile and evaluator traces out of the live Turn experiment.
        mlflow.dspy.autolog(
            log_traces=True,
            log_traces_from_eval=False,
            log_traces_from_compile=False,
            log_compiles=False,
            log_evals=False,
            silent=True,
        )
        _DSPY_AUTOLOG_ENABLED = True
        logger.info(
            "MLflow DSPy autolog enabled (inference=true backend=%s async=%s sampling=%s content_enabled=%s "
            "content_max_chars=%s)",
            "managed" if tracking_uri == _DEFAULT_TRACKING_URI else "http",
            settings.mlflow_async_logging,
            settings.mlflow_trace_sampling_ratio,
            _TRACE_CONTENT_ENABLED,
            _TRACE_CONTENT_MAX_CHARS,
        )
        _TRACING_ACTIVE = True
        return True
    except FleetConfigurationError:
        reset_tracing()
        raise  # Configuration errors propagate clearly
    except Exception:
        reset_tracing()
        logger.warning("MLflow tracing setup failed; continuing without traces")
        return False


def flush_tracing(*, terminate: bool = True) -> None:
    """
    Flush pending MLflow trace uploads during shutdown.

    Parameters:
        terminate (bool): Whether to terminate MLflow's asynchronous trace logging.
    """
    try:
        import mlflow

        flush = getattr(mlflow, "flush_trace_async_logging", None)
        if callable(flush):
            flush(terminate=terminate)
    except Exception:
        logger.warning("MLflow async trace flush failed; continuing shutdown")


# ---------------------------------------------------------------------------
# Per-Turn engineering-observability spans (merged from turn_tracing)
#
# Fail-soft per-Turn MLflow root spans.  Must never affect Turn outcomes: when
# disabled or when MLflow is unavailable, ``turn_trace`` yields a no-op handle
# with ``trace_id=None``.
# ---------------------------------------------------------------------------

_MAX_TRACE_TEXT_CHARS = 1_000


def trace_preview_limit(default: int = _MAX_TRACE_TEXT_CHARS) -> int:
    """Return the configured readable preview bound, or the local default."""
    try:
        return trace_content_max_chars()
    except Exception:
        return default


def _trace_value(value: object) -> object:
    """
    Sanitize and bound a value for safe inclusion in engineering traces.

    Parameters:
        value (object): The value to sanitize for tracing.

    Returns:
        object: A bounded sanitized value, the original primitive value, or the value's type name.
    """
    from fleet_rlm.rlm.result import sanitize_trace_value

    return sanitize_trace_value(value, max_len=trace_preview_limit())


def _trace_content_preview(value: object) -> str:
    """Return a safe trace-level preview even if policy lookup fails."""
    try:
        return trace_content_preview(value)
    except Exception:
        return "[redacted]"


def _trace_mapping(values: Mapping[str, object]) -> dict[str, object]:
    """Convert trace data to a sanitized dictionary, returning an empty dictionary if conversion fails."""
    sanitized = _trace_value(values)
    if isinstance(sanitized, dict):
        return cast(dict[str, object], sanitized)
    return {}


def _trace_attributes(values: Mapping[str, object]) -> dict[str, object]:
    """Project only allowlisted, low-cardinality values into span attributes."""
    attributes: dict[str, object] = {}
    for key, value in values.items():
        if _normalize_trace_key(str(key)) not in _OPERATIONAL_TEXT_KEYS:
            continue
        sanitized = _sanitize_mlflow_value(value, key=str(key))
        if isinstance(sanitized, (str, bool, int, float)) or (
            isinstance(sanitized, list) and all(isinstance(item, (str, bool, int, float)) for item in sanitized)
        ):
            attributes[str(key)] = sanitized
    return attributes


# Runtime Events must NOT be echoed into MLflow as spans. A single Turn emits
# dozens to hundreds of events (status, step lifecycle, reasoning/code/output
# values, usage), and representing them as zero-duration ``Turn.progress.*``
# spans produced noisy, non-standard traces that duplicated the product
# evidence stream (RuntimeEvents -> SSE -> TUI). Engineering observability
# comes from the ``fleet_turn`` root span, explicit ``turn_phase_span`` phases,
# and standard DSPy autolog spans (module/LM/tool calls).

_LOCAL_BYOK_USER = "fleet-local"
_LOCAL_SUPERVISED_TRACKING_URI = "http://127.0.0.1:5001"
_SPAN_NAME = "fleet_turn"
# Closed phase set so one Fleet Run (preparation + execution fleet_turn roots)
# remains searchable by exactly these values, never by ad-hoc strings.
TracePhase = Literal["preparation", "execution"]
_TRACE_PHASES: frozenset[str] = frozenset(get_args(TracePhase))
# MLflow trace tag/metadata keys. One-way link: the execution trace carries the
# preparation trace id; the preparation trace never references the execution.
_TRACE_PHASE_TAG = "fleet.trace_phase"
_PREPARATION_TRACE_ID_TAG = "fleet.preparation_trace_id"
_PREPARATION_TRACE_ID_MAX_CHARS = 256
try:
    _FLEET_APP_VERSION = package_version("fleet-rlm")
except PackageNotFoundError:
    _FLEET_APP_VERSION = "unknown"
_current_trace_id: ContextVar[str | None] = ContextVar("fleet_mlflow_trace_id", default=None)
_current_trace_failed: ContextVar[bool] = ContextVar("fleet_mlflow_trace_failed", default=False)
# True only while a fleet_turn root span is open. Phase spans gate on this so
# tracing-disabled turns never import or touch MLflow at all.
_fleet_trace_active: ContextVar[bool] = ContextVar("fleet_turn_trace_active", default=False)


@dataclass(frozen=True, slots=True)
class TraceHandle:
    """Public-safe handle for an optional active Turn trace."""

    trace_id: str | None
    # Internal-only span identity used for cross-trace links. This field must
    # never be copied into product events, durable turns, or public API data.
    _span_id: str | None = field(default=None, repr=False)


def _is_local_supervised_tracking_uri(uri: str | None) -> bool:
    """Return whether ``uri`` identifies Fleet's supervised local MLflow server."""
    if not isinstance(uri, str):
        return False
    try:
        parsed = urlparse(uri.rstrip("/"))
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "http"
        and parsed.hostname == "127.0.0.1"
        and port == 5001
        and not parsed.path
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
        and parsed.username is None
        and parsed.password is None
    )


def _set_current_trace_state(state: str) -> None:
    """Persist a terminal MLflow trace state without affecting the Turn."""
    try:
        import mlflow

        trace_update = getattr(mlflow, "update_current_trace", None)
        if callable(trace_update):
            trace_update(state=state)
    except Exception:
        logger.debug("MLflow trace state update failed; continuing")


def annotate_trace_io(
    *,
    request: str,
    response_text: str | None = None,
    response_outputs: dict[str, object] | None = None,
    failed: bool = False,
) -> None:
    """
    Annotate the active trace with sanitized request and response data.

    Parameters:
        request: The request content to record.
        response_text: Optional response text to record.
        response_outputs: Optional named response values to record.
        failed: Whether to mark the active trace as failed.
    """
    try:
        import mlflow

        span = mlflow.get_current_active_span()
        if span is None:
            return

        span.set_inputs({"request": _trace_value(request)})

        response: dict[str, object] = {}
        if response_text is not None:
            response["answer"] = _trace_value(response_text)
        if response_outputs is not None:
            for key in ("answer", "final_reasoning"):
                if key in response_outputs:
                    response[key] = _trace_value(response_outputs[key])

        span.set_outputs(response or {"answer": response_text or ""})
        trace_update = getattr(mlflow, "update_current_trace", None)
        if callable(trace_update):
            preview_kwargs: dict[str, object] = {
                "request_preview": _trace_content_preview(request),
            }
            if response_text is not None:
                preview_kwargs["response_preview"] = _trace_content_preview(response_text)
            trace_update(**preview_kwargs)
        if failed:
            _current_trace_failed.set(True)
            _set_current_trace_state("ERROR")
            try:
                span.set_status("ERROR")
            except Exception:
                logger.debug("annotate_trace_io status update failed; continuing")
    except Exception:
        logger.debug("annotate_trace_io failed; continuing without root span I/O")


def annotate_turn_attributes(attributes: Mapping[str, object]) -> None:
    """Attach bounded, sanitized attributes to the active ``fleet_turn`` span.

    Fail-soft by contract: with no active Turn trace (or when MLflow is
    unavailable) this is a no-op that never imports MLflow, and annotation
    faults are logged at debug level without affecting the Turn.
    Callers supply only bounded low-cardinality metadata.
    """
    if not _fleet_trace_active.get():
        return
    try:
        import mlflow

        span = mlflow.get_current_active_span()
        if span is None:
            return
        setter = getattr(span, "set_attributes", None)
        if callable(setter):
            setter(_trace_mapping(attributes))
    except Exception:
        logger.debug("annotate_turn_attributes failed; continuing")


def record_settlement_status(status: str, *, durable: bool) -> None:
    """Record Fleet's durable settlement separately from MLflow span state."""
    if status not in {"completed", "failed", "cancelled", "timeout"}:
        return
    annotate_turn_attributes({"settlement_status": status, "settlement_durable": durable})


def current_turn_trace_id() -> str | None:
    """Return the active Turn trace id for this context, if any."""
    return _current_trace_id.get()


@dataclass(slots=True)
class TraceSpanHandle:
    """Fail-soft lifecycle handle for a bounded nested MLflow span.

    The handle supports callbacks whose start and end hooks are separate
    invocations. It never exposes raw exception details to MLflow and never
    lets tracing failures affect the owning Turn.
    """

    _span_context: Any | None = None
    _span: Any | None = None
    outputs: dict[str, object] = field(default_factory=dict)
    _closed: bool = False

    def set_outputs(self, outputs: Mapping[str, object]) -> None:
        try:
            self.outputs.update(dict(outputs))
        except Exception:
            logger.debug("trace span output accumulation failed; continuing")

    def finish(
        self,
        *,
        phase_status: str,
        outputs: Mapping[str, object] | None = None,
        attributes: Mapping[str, object] | None = None,
    ) -> None:
        """Close the span with bounded outputs and a sanitized status."""
        if self._closed:
            return
        self._closed = True
        if outputs is not None:
            self.set_outputs(outputs)
        if self._span is None or self._span_context is None:
            return
        try:
            self._span.set_outputs({**_trace_mapping(self.outputs), "phase_status": phase_status})
        except Exception:
            logger.debug("trace span output annotation failed; continuing")
        if attributes:
            try:
                setter = getattr(self._span, "set_attributes", None)
                if callable(setter):
                    setter(_trace_mapping(attributes))
            except Exception:
                logger.debug("trace span attribute annotation failed; continuing")
        if phase_status != "completed":
            try:
                self._span.set_status("ERROR")
            except Exception:
                logger.debug("trace span status annotation failed; continuing")
        try:
            # Do not pass provider exceptions to MLflow: their messages can
            # contain prompts, generated code, or gateway response bodies.
            self._span_context.__exit__(None, None, None)
        except BaseException:
            logger.debug("trace span close failed; continuing")


def start_turn_span(
    name: str,
    *,
    inputs: Mapping[str, object],
    span_type: str = "CHAIN",
) -> TraceSpanHandle:
    """Start a bounded nested span when a ``fleet_turn`` trace is active.

    MLflow's manual span API is used because DSPy callback start/end hooks are
    separate events and cannot be represented by a single ``with`` body.
    """
    handle = TraceSpanHandle()
    if not _fleet_trace_active.get():
        return handle

    try:
        import mlflow
        from mlflow.entities import SpanType

        active_span = mlflow.get_current_active_span()
        if active_span is None:
            return handle
        span_context = mlflow.start_span(
            name=name,
            span_type=getattr(SpanType, span_type, SpanType.CHAIN),
        )
        span = span_context.__enter__()
    except Exception:
        logger.debug("MLflow lifecycle span setup failed; continuing")
        return handle

    handle._span_context = span_context
    handle._span = span
    try:
        span.set_inputs(_trace_mapping(inputs))
    except Exception:
        logger.debug("trace span input annotation failed; continuing")
    try:
        setter = getattr(span, "set_attributes", None)
        attributes = _trace_attributes(inputs)
        if callable(setter) and attributes:
            setter(attributes)
    except Exception:
        logger.debug("trace span attribute annotation failed; continuing")
    return handle


@contextmanager
def turn_phase_span(name: str, *, inputs: Mapping[str, object]) -> Iterator[TraceSpanHandle]:
    """Record one bounded, nested Turn phase without affecting its outcome.

    The caller supplies bounded operational metadata and sanitized previews
    when step-level debugging needs them. Unbounded prompts, generated
    programs, interpreter output, and sensitive values must never be attached.
    Yields a ``TraceSpanHandle`` so callers can attach bounded outputs at exit
    time.
    Outside an active ``fleet_turn`` trace this is a no-op that never imports
    MLflow, keeping tracing-disabled turns free of any MLflow footprint.
    """
    handle = start_turn_span(name, inputs=inputs)
    try:
        yield handle
    except BaseException as exc:
        # Categorize failures without passing the exception object or message
        # into MLflow. Provider and interpreter exceptions frequently carry
        # prompts, generated code, URLs, or gateway response bodies.
        from fleet_rlm.observability.diagnostics import trace_failure_category

        handle.finish(
            phase_status="failed",
            outputs={"failure_category": trace_failure_category(exc)},
        )
        raise
    else:
        handle.finish(phase_status="completed")


@contextmanager
def turn_trace(
    session_id: UUID,
    run_id: UUID,
    *,
    enabled: bool,
    expose_trace_id: bool = True,
    trace_phase: TracePhase | None = None,
    preparation_trace_id: str | None = None,
    preparation_span_id: str | None = None,
    runtime_variant: str | None = None,
    program_fingerprint: str | None = None,
    image_identity: str | None = None,
) -> Iterator[TraceHandle]:
    """
    Open a root ``fleet_turn`` span for a Fleet turn when tracing is available.

    Parameters:
        session_id (UUID): Identifier for the session associated with the turn.
        run_id (UUID): Identifier for the run associated with the turn.
        enabled (bool): Whether tracing is enabled for the turn.
        expose_trace_id (bool): Whether the yielded handle exposes the root trace identifier.
        trace_phase (TracePhase | None): Optional phase marker, either ``"preparation"`` or
            ``"execution"``, recorded on the trace.
        preparation_trace_id (str | None): Optional preparation trace identifier to associate
            with an execution trace.
        preparation_span_id (str | None): Optional preparation span identifier used for a local
            cross-trace Span Link. This is internal-only and is never exposed by the handle.
        runtime_variant: Optional bounded runtime identity for an execution trace.
        program_fingerprint: Optional opaque program digest for an execution trace.
        image_identity: Optional opaque immutable image identity for an execution trace.

    Yields:
        TraceHandle: The root trace identifier when tracing succeeds and exposure is enabled;
            otherwise, a no-op handle.
    """
    if not enabled:
        yield TraceHandle(trace_id=None)
        return
    if not is_tracing_active():
        # Policy may still request tracing after configure_tracing failed soft
        # (for example a dead local tracking URI). Opening spans would enter
        # MLflow's HTTP retry loop and starve claim heartbeats.
        yield TraceHandle(trace_id=None)
        return

    token = _current_trace_id.set(None)
    failed_token = _current_trace_failed.set(False)
    active_token: Token[bool] | None = None
    try:
        try:
            import mlflow
            from mlflow.entities import SpanType
        except Exception:
            logger.warning("MLflow import failed for turn span; continuing without traces")
            yield TraceHandle(trace_id=None)
            return

        try:
            span_context = mlflow.start_span(
                name=_SPAN_NAME,
                span_type=SpanType.CHAIN,
                log_level="INFO",
            )
            span = span_context.__enter__()
        except Exception:
            logger.warning("MLflow turn span setup failed; continuing without traces")
            yield TraceHandle(trace_id=None)
            return

        if (
            trace_phase == "execution"
            and preparation_trace_id
            and preparation_span_id
            and _is_local_supervised_tracking_uri(_TRACKING_URI_APPLIED)
        ):
            try:
                from mlflow.entities import Link

                add_link = getattr(span, "add_link", None)
                if callable(add_link):
                    add_link(
                        Link(
                            trace_id=str(preparation_trace_id)[:_PREPARATION_TRACE_ID_MAX_CHARS],
                            span_id=str(preparation_span_id)[:32],
                            attributes={"fleet.relationship": "preparation"},
                        )
                    )
            except Exception:
                # Cross-trace navigation is optional observability. A malformed
                # or unsupported link must never prevent the Turn trace itself.
                logger.warning("MLflow preparation Span Link setup failed; continuing without link")

        active_token = _fleet_trace_active.set(True)
        tags: dict[str, str] = {
            "fleet.run_id": str(run_id),
            "fleet.session_id": str(session_id),
        }
        metadata: dict[str, str] = {
            "fleet.run_id": str(run_id),
            "fleet.app_version": _FLEET_APP_VERSION,
        }
        if trace_phase is not None:
            if trace_phase in _TRACE_PHASES:
                tags[_TRACE_PHASE_TAG] = trace_phase
                metadata[_TRACE_PHASE_TAG] = trace_phase
            else:
                logger.debug("ignoring unrecognized trace phase %r", trace_phase)
        if preparation_trace_id and trace_phase == "execution":
            # Strictly one-way: only the execution root may carry the
            # preparation link, never a preparation or phase-less root.
            bounded_id = str(preparation_trace_id)[:_PREPARATION_TRACE_ID_MAX_CHARS]
            tags[_PREPARATION_TRACE_ID_TAG] = bounded_id
            metadata[_PREPARATION_TRACE_ID_TAG] = bounded_id
        if trace_phase == "execution":
            for key, value in (
                ("fleet.runtime_variant", runtime_variant),
                ("fleet.program_fingerprint", program_fingerprint),
                ("fleet.image_identity", image_identity),
            ):
                if isinstance(value, str) and value and len(value) <= 256:
                    tags[key] = value
                    metadata[key] = value
        span_id: str | None = None
        try:
            mlflow.update_current_trace(
                session_id=str(session_id),
                user=_LOCAL_BYOK_USER,
                tags=tags,
                metadata=metadata,
            )
        except Exception:
            logger.warning("MLflow update_current_trace failed; continuing")
        trace_id: str | None = None
        try:
            # The root span is the only authoritative identity for this Turn.
            # ``get_last_active_trace_id`` can refer to a prior trace after a
            # preparation span has already been closed, which would leak the
            # previous trace ID into this Turn's SSE events.
            raw = getattr(span, "request_id", None)
            if raw is None:
                get_current_active_span = getattr(mlflow, "get_current_active_span", None)
                current_span = get_current_active_span() if callable(get_current_active_span) else None
                raw = getattr(current_span, "request_id", None)
            if raw is not None:
                trace_id = str(raw)
                if expose_trace_id:
                    _current_trace_id.set(trace_id)
            raw_span_id = getattr(span, "span_id", None)
            if raw_span_id is not None:
                span_id = str(raw_span_id)
        except Exception:
            logger.warning("MLflow active trace ID lookup failed; continuing")

        try:
            yield TraceHandle(trace_id=trace_id if expose_trace_id else None, _span_id=span_id)
        except BaseException as exc:
            from fleet_rlm.observability.diagnostics import trace_failure_category

            _current_trace_failed.set(True)
            _set_current_trace_state("ERROR")
            try:
                # Never pass the live exception into MLflow. Even though the
                # exporter sanitizer strips recorded exception events, doing
                # so at the ownership boundary avoids depending on an SDK
                # implementation detail and leaves a useful, safe diagnosis.
                try:
                    span.set_outputs({"failure_category": _trace_value(trace_failure_category(exc))})
                except BaseException:
                    logger.warning("MLflow turn span failure output annotation failed; continuing")
                try:
                    span.set_status("ERROR")
                except BaseException:
                    logger.warning("MLflow turn span failure status annotation failed; continuing")
            except BaseException:
                logger.warning("MLflow turn span failure annotation failed; continuing")
            finally:
                try:
                    span_context.__exit__(None, None, None)
                except BaseException:
                    logger.warning("MLflow turn span teardown failed; continuing")
            raise
        else:
            _set_current_trace_state("ERROR" if _current_trace_failed.get() else "OK")
            try:
                span_context.__exit__(None, None, None)
            except BaseException:
                logger.warning("MLflow turn span teardown failed; continuing")
    finally:
        if active_token is not None:
            _fleet_trace_active.reset(active_token)
        _current_trace_failed.reset(failed_token)
        _current_trace_id.reset(token)
