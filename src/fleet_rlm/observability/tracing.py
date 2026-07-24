"""MLflow tracing configuration for the Fleet RLM backend.

Tracing is engineering observability only — it must never affect Turn
outcomes.  All setup is fail-soft: if MLflow or the Databricks tracking
backend is unavailable, the backend continues without traces.

Fleet policy (``FLEET_*`` / ``Settings``):
    FLEET_MLFLOW_TRACING_ENABLED   - master gate (default true)
    FLEET_MLFLOW_EXPERIMENT_NAME   - experiment passed to set_experiment
    FLEET_MLFLOW_EXPOSE_TRACE_ID   - surface trace ids on Turn SSE metadata

Databricks auth remains outside FLEET secrets (SDK/CLI conventions):
    DATABRICKS_HOST  - Workspace URL (e.g. https://...gcp.databricks.com)
    DATABRICKS_TOKEN - PAT or service principal token (or databricks-cli keyring)
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fleet_rlm.config import Settings

logger = logging.getLogger(__name__)

_TRACING_CONFIGURED = False
_DATABRICKS_TRACKING_URI = "databricks"


def configure_tracing(settings: Settings) -> None:
    """Enable MLflow DSPy autolog against Databricks when Fleet policy enables it.

    Safe to call multiple times; only the first invocation takes effect.
    Never raises — logs warnings on failure.
    """
    global _TRACING_CONFIGURED  # noqa: PLW0603
    if _TRACING_CONFIGURED:
        return
    _TRACING_CONFIGURED = True

    if not settings.mlflow_tracing_enabled:
        logger.debug("FLEET_MLFLOW_TRACING_ENABLED is false; tracing disabled")
        return

    required_settings = {
        "FLEET_MLFLOW_EXPERIMENT_NAME": settings.mlflow_experiment_name,
        "FLEET_MLFLOW_TRACE_CATALOG": settings.mlflow_trace_catalog,
        "FLEET_MLFLOW_TRACE_SCHEMA": settings.mlflow_trace_schema,
        "FLEET_MLFLOW_TRACE_TABLE_PREFIX": settings.mlflow_trace_table_prefix,
        "FLEET_MLFLOW_TRACING_SQL_WAREHOUSE_ID": settings.mlflow_tracing_sql_warehouse_id,
    }
    missing_settings = sorted(name for name, value in required_settings.items() if not value)
    if missing_settings:
        logger.warning(
            "MLflow tracing enabled but required settings are missing; tracing disabled: %s",
            ", ".join(missing_settings),
        )
        return

    try:
        # Pydantic reads repository .env values for Fleet settings, but the
        # Databricks SDK authenticates from the process environment. Preserve
        # explicit exports and only bridge the two external auth variables
        # when they are present in the already-loaded dotenv values.
        for name in ("DATABRICKS_HOST", "DATABRICKS_TOKEN"):
            if not os.environ.get(name):
                value = settings._dotenv_values.get(name)
                if value:
                    os.environ[name] = value

        import mlflow
        import mlflow.dspy
        from mlflow.entities.trace_location import UnityCatalog

        mlflow.set_tracking_uri(_DATABRICKS_TRACKING_URI)
        os.environ["MLFLOW_TRACING_SQL_WAREHOUSE_ID"] = settings.mlflow_tracing_sql_warehouse_id
        mlflow.set_experiment(
            experiment_name=settings.mlflow_experiment_name,
            trace_location=UnityCatalog(
                catalog_name=settings.mlflow_trace_catalog,
                schema_name=settings.mlflow_trace_schema,
                table_prefix=settings.mlflow_trace_table_prefix,
            ),
        )
        mlflow.dspy.autolog()
        logger.info(
            "MLflow DSPy autolog enabled (tracking_uri=%s experiment=%s)",
            _DATABRICKS_TRACKING_URI,
            settings.mlflow_experiment_name,
        )
    except Exception:  # noqa: BLE001
        logger.warning("MLflow tracing setup failed; continuing without traces", exc_info=True)
