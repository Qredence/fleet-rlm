"""Redacted previews for operator activity events, and MLflow tracing setup."""

from __future__ import annotations

import json
import os
import re
from contextlib import suppress
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
_TRACE_STRING_LIMIT = 20_000
_TRACE_NODE_LIMIT = 200


def trace_object(value: Any) -> dict[str, Any]:
    """Decode a trace object, including Unity Catalog's extra JSON string wrapper."""
    for _ in range(2):
        if isinstance(value, dict):
            return value
        if not isinstance(value, str):
            return {}
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    return value if isinstance(value, dict) else {}


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


def trace_value(value: Any, *, nodes: list[int], depth: int = 0) -> Any:
    """Bound and redact framework-captured trace data before MLflow exports it."""
    nodes[0] += 1
    if nodes[0] > _TRACE_NODE_LIMIT:
        return "[trace size limit]"
    if depth > 8:
        return "[trace depth limit]"
    if isinstance(value, str):
        return activity_text(value, limit=_TRACE_STRING_LIMIT)
    if isinstance(value, dict):
        if "base64" in value and "sha256" in value:
            import base64

            # Encoded artifact bytes must not bypass recursive credential redaction.
            try:
                raw = base64.b64decode(value["base64"], validate=True)
                value = {"sha256": value["sha256"], "text": raw.decode("utf-8")}
            except (ValueError, TypeError, UnicodeError):
                value = {"sha256": value["sha256"], "text": "[binary artifact omitted]"}
        result = {}
        for key, child in islice(value.items(), _TRACE_NODE_LIMIT):
            name = str(key)[:100]
            result[name] = (
                "[redacted]" if _PRIVATE_KEY.search(name) else trace_value(child, nodes=nodes, depth=depth + 1)
            )
        if len(value) > _TRACE_NODE_LIMIT:
            result["…"] = "[truncated]"
        return result
    if isinstance(value, (list, tuple)):
        items = list(islice(value, 50))
        result = [trace_value(child, nodes=nodes, depth=depth + 1) for child in items]
        if len(value) > len(items):
            result.append("[truncated]")
        return result
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return f"[{type(value).__name__} omitted]"


def sanitize_trace_span(span: Any) -> None:
    """Apply bounded recursive sanitization to all captured inputs and outputs before export."""
    for name in ("inputs", "outputs"):
        value = getattr(span, name, None)
        if value is not None:
            setattr_fn = getattr(span, f"set_{name}", None)
            if callable(setattr_fn):
                cleaned = trace_value(value, nodes=[0])
                # Only Fleet's root reference is lossless; arbitrary nested payloads remain previews.
                if name == "inputs" and getattr(span, "name", None) == "fleet_turn" and isinstance(value, dict):
                    from fleet_rlm.workspace import validate_session_id

                    for key in ("session_id", "turn_id"):
                        with suppress(ValueError):
                            cleaned[key] = validate_session_id(value.get(key))
                    if type(value.get("replay_snapshot_selected")) is bool:
                        cleaned["replay_snapshot_selected"] = value["replay_snapshot_selected"]
                    digest = value.get("replay_snapshot_sha256")
                    if isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest):
                        cleaned["replay_snapshot_sha256"] = digest
                setattr_fn(cleaned)


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
    # DSPy autolog captures nested prompts and tool payloads; sanitize those spans before export.
    mlflow.tracing.configure(span_processors=[sanitize_trace_span])
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
