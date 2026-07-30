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

import logging
import os
import socket
from typing import TYPE_CHECKING, cast
from urllib.parse import urlparse

if TYPE_CHECKING:
    from fleet_rlm.config import Settings

from fleet_rlm.config import FleetConfigurationError

logger = logging.getLogger(__name__)

_TRACING_CONFIGURED = False
_DEFAULT_TRACKING_URI = "databricks"
_TRACE_DESTINATION_TAG = "mlflow.experiment.databricksTraceDestinationPath"


def _local_tracking_server_available(tracking_uri: str) -> bool:
    """Bound local MLflow startup probing so tracing cannot stall app import."""
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
    """Enable MLflow DSPy autolog when Fleet policy enables it.

    The TOML-selected tracking URI can point at a normal MLflow server (for
    example ``http://localhost:5001``). Databricks Unity Catalog trace
    settings are required only when the URI is ``databricks``.

    Safe to call multiple times; only the first invocation takes effect.
    Raises ``FleetConfigurationError`` when the experiment's Unity Catalog trace
    location doesn't match the configured destination; otherwise logs warnings
    on failure and continues.
    """
    global _TRACING_CONFIGURED
    if _TRACING_CONFIGURED:
        return
    _TRACING_CONFIGURED = True

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
        # Fleet owns the bounded redacted spans in ``turn_tracing``. DSPy's
        # callback otherwise records full prompts, generated code, tool args,
        # and provider responses, which is unsuitable for live traces.
        mlflow.dspy.autolog(log_traces=False, log_traces_from_eval=False, silent=True)
        logger.info(
            "MLflow DSPy autolog enabled (tracking_uri=%s experiment=%s)",
            tracking_uri,
            settings.mlflow_experiment_name,
        )
    except FleetConfigurationError:
        raise  # Configuration errors propagate clearly
    except Exception:
        logger.warning("MLflow tracing setup failed; continuing without traces", exc_info=True)
