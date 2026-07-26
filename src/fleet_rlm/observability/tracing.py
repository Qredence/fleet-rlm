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
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from fleet_rlm.config import Settings

logger = logging.getLogger(__name__)

_TRACING_CONFIGURED = False
_DEFAULT_TRACKING_URI = "databricks"


def configure_tracing(settings: Settings) -> None:
    """Enable MLflow DSPy autolog when Fleet policy enables it.

    The TOML-selected tracking URI can point at a normal MLflow server (for
    example ``http://localhost:5001``). Databricks Unity Catalog trace
    settings are required only when the URI is ``databricks``.

    Safe to call multiple times; only the first invocation takes effect.
    Never raises — logs warnings on failure.
    """
    global _TRACING_CONFIGURED  # noqa: PLW0603
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
        if tracking_uri == _DEFAULT_TRACKING_URI:
            from mlflow.entities.trace_location import UnityCatalog

            os.environ["MLFLOW_TRACING_SQL_WAREHOUSE_ID"] = cast(
                str, settings.mlflow_tracing_sql_warehouse_id
            )
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
        mlflow.dspy.autolog()
        logger.info(
            "MLflow DSPy autolog enabled (tracking_uri=%s experiment=%s)",
            tracking_uri,
            settings.mlflow_experiment_name,
        )
    except Exception:  # noqa: BLE001
        logger.warning("MLflow tracing setup failed; continuing without traces", exc_info=True)
