"""MLflow tracing configuration for the Fleet RLM backend.

Tracing is engineering observability only — it must never affect Turn
outcomes.  All setup is fail-soft: if MLflow or the Databricks tracking
backend is unavailable, the backend continues without traces.

Environment:
    MLFLOW_TRACKING_URI   – "databricks" for the Databricks workspace backend.
    DATABRICKS_HOST       – Workspace URL (e.g. https://…gcp.databricks.com).
    DATABRICKS_TOKEN      – PAT or service principal token.
    MLFLOW_EXPERIMENT_NAME – Workspace path or UC experiment name.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_TRACING_CONFIGURED = False


def configure_tracing() -> None:
    """Enable MLflow DSPy autolog if a tracking URI is configured.

    Safe to call multiple times; only the first invocation takes effect.
    Never raises — logs warnings on failure.
    """
    global _TRACING_CONFIGURED  # noqa: PLW0603
    if _TRACING_CONFIGURED:
        return
    _TRACING_CONFIGURED = True

    from dotenv import load_dotenv

    load_dotenv(override=False)

    tracking_uri = os.environ.get("MLFLOW_TRACKING_URI", "").strip()
    if not tracking_uri:
        logger.debug("MLFLOW_TRACKING_URI not set; tracing disabled")
        return

    try:
        import mlflow
        import mlflow.dspy

        mlflow.dspy.autolog()
        logger.info("MLflow DSPy autolog enabled (tracking_uri=%s)", tracking_uri)
    except Exception:  # noqa: BLE001
        logger.warning("MLflow tracing setup failed; continuing without traces", exc_info=True)
