"""Redacted previews for operator activity events, and MLflow tracing setup."""

from __future__ import annotations

import json
import os
import re
from itertools import islice
from typing import Any

import mlflow

_PRIVATE_KEY = re.compile(
    r"password|secret|token|authorization|cookie|api.?key|credential|reasoning|thought|scratchpad", re.I
)
_CREDENTIAL = re.compile(
    r"(?i)(bearer\s+)[\w.\-]+|"
    r"((?:[\w-]*(?:password|secret|token|api[_-]?key)[\w-]*)[\"']?\s*[:=]\s*[\"']?)([^\s,;\"']+)"
)


def activity_text(text: str, *, limit: int = 20_000) -> str:
    """Keep code/output readable while redacting labelled credentials and controls."""
    text = _CREDENTIAL.sub(lambda m: (m[1] or m[2]) + "[redacted]", text)
    text = "".join(c for c in text if c in "\n\t" or c.isprintable())
    return text[:limit] + ("… [truncated]" if len(text) > limit else "")


def activity_preview(value: Any, *, limit: int = 4000) -> str:
    """Preview JSON-like tool data, never arbitrary object reprs or predictor internals.

    This is not a DLP boundary: unlabelled secrets in free text cannot be reliably
    detected. Limit depth, collection sizes, and the entire serialized preview.
    """
    remaining_nodes = 80

    def clean(item: Any, depth: int) -> Any:
        nonlocal remaining_nodes
        remaining_nodes -= 1
        if remaining_nodes < 0:
            return "[preview limit]"
        if depth > 4:
            return "[depth limit]"
        if isinstance(item, str):
            return activity_text(item, limit=limit)
        if item is None or isinstance(item, (bool, int, float)):
            return item
        if isinstance(item, dict):
            result = {
                str(k)[:100]: "[redacted]" if _PRIVATE_KEY.search(str(k)) else clean(v, depth + 1)
                for k, v in islice(item.items(), 20)
            }
            if len(item) > 20:
                result["…"] = "[truncated]"
            return result
        if isinstance(item, (list, tuple)):
            return [clean(v, depth + 1) for v in item[:20]] + (["[truncated]"] if len(item) > 20 else [])
        return f"[{type(item).__name__} omitted]"

    text = json.dumps(clean(value, 0), ensure_ascii=False)
    return text[:limit] + ("… [truncated]" if len(text) > limit else "")


def configure_mlflow_observability(
    experiment_name: str = "fleet-rlm-production",
    tracking_uri: str = "sqlite:///mlflow.db",
    *,
    trace_catalog: str = "",
    trace_schema: str = "",
    trace_table_prefix: str = "",
    tracing_sql_warehouse_id: str = "",
) -> str | None:
    """Enable DSPy tracing in MLflow without making tracking a serving dependency."""
    if tracking_uri:
        mlflow.set_tracking_uri(tracking_uri)
    if tracking_uri.startswith("databricks"):
        from mlflow.entities.trace_location import UnityCatalog

        os.environ["MLFLOW_TRACING_SQL_WAREHOUSE_ID"] = tracing_sql_warehouse_id
        if trace_table_prefix:
            trace_location = UnityCatalog(
                catalog_name=trace_catalog,
                schema_name=trace_schema,
                table_prefix=trace_table_prefix,
            )
        else:
            trace_location = UnityCatalog(catalog_name=trace_catalog, schema_name=trace_schema)
        mlflow.set_experiment(experiment_name, trace_location=trace_location)
    else:
        mlflow.set_experiment(experiment_name)
    mlflow.dspy.autolog(log_traces=True, log_traces_from_compile=False, log_traces_from_eval=False)
    experiment = mlflow.get_experiment_by_name(experiment_name)
    return experiment.experiment_id if experiment is not None else None
