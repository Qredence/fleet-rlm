"""MLflow tracing configuration for the Fleet RLM backend.

Tracing is engineering observability only — it must never affect Turn
outcomes.  All setup is fail-soft: if MLflow or the configured tracking
backend is unavailable, the backend continues without traces.

Fleet TOML policy (resolved through ``Settings``):
    mlflow.tracing_enabled  - master gate (default false)
    mlflow.experiment_name  - experiment passed to set_experiment
    mlflow.tracking_uri     - tracking target
    mlflow.expose_trace_id  - surface trace ids on Turn SSE metadata

Databricks auth remains outside FLEET secrets (SDK/CLI conventions):
    DATABRICKS_HOST  - Workspace URL (e.g. https://...gcp.databricks.com)
    DATABRICKS_TOKEN - PAT or service principal token (or databricks-cli keyring)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import socket
from collections.abc import Mapping
from typing import TYPE_CHECKING, cast
from urllib.parse import urlparse

if TYPE_CHECKING:
    from fleet_rlm.config import Settings

from fleet_rlm.config import FleetConfigurationError

logger = logging.getLogger(__name__)

_TRACING_CONFIGURED = False
_TRACING_ACTIVE = False
_DEFAULT_TRACKING_URI = "databricks"
_TRACE_DESTINATION_TAG = "mlflow.experiment.databricksTraceDestinationPath"
_CREDENTIAL_KEYS = frozenset({"api_key", "authorization", "credential", "password", "secret", "token"})
_CONTENT_BEARING_KEYS = frozenset(
    {
        "answer",
        "arguments",
        "candidate",
        "code",
        "content",
        "error",
        "exception",
        "feedback",
        "input",
        "inputs",
        "instruction",
        "message",
        "output",
        "outputs",
        "prompt",
        "query",
        "reasoning",
        "request",
        "response",
        "result",
        "text",
        "tool_input",
        "tool_output",
    }
)
_OPERATIONAL_TEXT_KEYS = frozenset(
    {
        "cache",
        "engine",
        "failure_category",
        "kind",
        "model",
        "model_type",
        "name",
        "phase_status",
        "provider",
        "provider_type",
        "role",
        "status",
        "termination_mode",
        "type",
    }
)


def _normalize_trace_key(key: str | None) -> str:
    """Normalize dotted, hyphenated, and camel-case span keys for policy checks."""
    raw = (key or "").strip()
    raw = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", raw)
    return re.sub(r"[^a-zA-Z0-9]+", "_", raw).strip("_").lower()


def _trace_digest(value: object) -> str:
    """Return a stable digest marker without exporting the original value."""
    try:
        encoded = json.dumps(value, sort_keys=True, default=lambda item: type(item).__name__, separators=(",", ":"))
    except (TypeError, ValueError):
        encoded = type(value).__name__
    return f"[redacted sha256={hashlib.sha256(encoded.encode()).hexdigest()}]"


def _sanitize_mlflow_value(value: object, *, key: str | None = None, depth: int = 0) -> object:
    """Preserve operational metadata while replacing content-bearing trace values."""
    if depth >= 8:
        return "[redacted depth]"
    normalized_key = _normalize_trace_key(key)
    if normalized_key in _CONTENT_BEARING_KEYS or normalized_key.endswith(
        ("_input", "_output", "_prompt", "_response")
    ):
        return _trace_digest(value)
    if isinstance(value, Mapping):
        return {
            str(item_key)[:128]: _sanitize_mlflow_value(item, key=str(item_key), depth=depth + 1)
            for item_key, item in list(value.items())[:50]
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize_mlflow_value(item, depth=depth + 1) for item in list(value)[:50]]
    if isinstance(value, str):
        if normalized_key in _CREDENTIAL_KEYS or normalized_key.endswith(
            tuple(f"_{credential_key}" for credential_key in _CREDENTIAL_KEYS)
        ):
            return "[redacted]"
        if normalized_key in _OPERATIONAL_TEXT_KEYS:
            return value
        # DSPy signatures may use arbitrary field names, so unknown string
        # fields must be treated as content rather than exported by default.
        return _trace_digest(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return type(value).__name__


def _sanitize_mlflow_span(span: object) -> None:
    """
    Sanitize an MLflow span's inputs, outputs, and attributes before export.

    Sanitization failures are suppressed so tracing does not affect the Turn outcome.

    Parameters:
        span (object): MLflow span to sanitize.
    """
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
        # A processor must never change the Turn outcome or break trace export.
        logger.debug("MLflow span sanitization failed; continuing", exc_info=True)


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


def configure_tracing(settings: Settings) -> None:
    """
    Configure fail-soft MLflow tracing and DSPy inference autologging according to Fleet settings.

    The configuration is applied at most once. Tracing remains disabled when it is
    disabled by policy, required settings are missing, or setup fails. Raises
    `FleetConfigurationError` when an existing experiment's Unity Catalog trace
    location conflicts with the configured destination; other setup failures are
    logged and suppressed.
    """
    global _TRACING_ACTIVE, _TRACING_CONFIGURED
    if _TRACING_CONFIGURED:
        return
    _TRACING_CONFIGURED = True
    _TRACING_ACTIVE = False

    if not settings.mlflow_tracing_enabled:
        logger.debug("MLflow tracing is disabled by Fleet policy")
        return

    tracking_uri = settings.mlflow_tracking_uri or _DEFAULT_TRACKING_URI
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
        return

    try:
        # The Databricks SDK authenticates from the process environment.
        # Preserve explicit exports and bridge the two external auth variables
        # only when they are present in the already-loaded dotenv values.
        for name in ("DATABRICKS_HOST", "DATABRICKS_TOKEN"):
            if not os.environ.get(name):
                value = settings._dotenv_values.get(name)
                if value:
                    os.environ[name] = value

        import mlflow
        import mlflow.dspy

        mlflow.set_tracking_uri(tracking_uri)

        # MLflow 3.15's sampler is process-global. Set it from the selected
        # Fleet policy so an ambient environment variable cannot change the
        # effective trace volume.
        os.environ["MLFLOW_TRACE_SAMPLING_RATIO"] = str(settings.mlflow_trace_sampling_ratio)

        # A local MLflow server is optional engineering observability. Avoid
        # entering MLflow's retry loop when the configured local endpoint is
        # absent; this keeps FastAPI app construction and OpenAPI generation
        # bounded while preserving tracing when the server is available.
        if (
            tracking_uri.startswith(("http://", "https://"))
            and getattr(mlflow, "__file__", None)
            and not _local_tracking_server_available(tracking_uri)
        ):
            logger.warning("MLflow tracking server is unavailable; continuing without traces")
            return

        # Preflight: catch trace-location mismatch before set_experiment.
        # FleetConfigurationError propagates — all other failures are soft.
        if tracking_uri == _DEFAULT_TRACKING_URI:
            _validate_experiment_trace_location(settings)

        if tracking_uri == _DEFAULT_TRACKING_URI:
            from mlflow.entities.trace_location import UnityCatalog

            os.environ["MLFLOW_TRACING_SQL_WAREHOUSE_ID"] = cast(str, settings.mlflow_tracing_sql_warehouse_id)
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

        config = getattr(mlflow, "config", None)
        enable_async_logging = getattr(config, "enable_async_logging", None)
        if callable(enable_async_logging):
            enable_async_logging(settings.mlflow_async_logging)

        tracing_api = getattr(mlflow, "tracing", None)
        configure_processors = getattr(tracing_api, "configure", None)
        if callable(configure_processors):
            configure_processors(span_processors=[_sanitize_mlflow_span])

        # Enable MLflow's DSPy inference callback. The 3.15 span processor
        # above is the export boundary that bounds and redacts its prompts,
        # generated code, tool arguments, and provider responses. Keep compile
        # and evaluator traces out of the live Turn experiment.
        mlflow.dspy.autolog(log_traces=True, log_traces_from_eval=False, silent=True)
        logger.info(
            "MLflow DSPy autolog enabled (inference=true tracking_uri=%s experiment=%s async=%s sampling=%s)",
            tracking_uri,
            settings.mlflow_experiment_name,
            settings.mlflow_async_logging,
            settings.mlflow_trace_sampling_ratio,
        )
        _TRACING_ACTIVE = True
    except FleetConfigurationError:
        raise  # Configuration errors propagate clearly
    except Exception:
        logger.warning("MLflow tracing setup failed; continuing without traces", exc_info=True)


def flush_tracing(*, terminate: bool = True) -> None:
    """
    Flush pending MLflow trace uploads during shutdown.

    Parameters:
        terminate (bool): Whether to terminate MLflow's asynchronous trace logging.
    """
    if not _TRACING_ACTIVE:
        return
    try:
        import mlflow

        flush = getattr(mlflow, "flush_trace_async_logging", None)
        if callable(flush):
            flush(terminate=terminate)
    except Exception:
        logger.warning("MLflow async trace flush failed; continuing shutdown", exc_info=True)
