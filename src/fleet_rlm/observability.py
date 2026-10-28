"""Redacted previews for operator activity events, and MLflow tracing setup."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Generator
from contextlib import contextmanager, suppress
from itertools import islice
from typing import Any

import mlflow
from mlflow.entities import SpanType

_CREDENTIAL_KEY = r"password|secret|token|authorization|cookie|api.?key|credential"
_PRIVATE_KEY = re.compile(rf"{_CREDENTIAL_KEY}|reasoning|thought|scratchpad", re.I)
# Traces keep reasoning text for debugging; activity streams and judge inputs still redact it.
_TRACE_PRIVATE_KEY = re.compile(rf"{_CREDENTIAL_KEY}|thought|scratchpad", re.I)
_CREDENTIAL = re.compile(
    r"(?i)(bearer\s+)[\w.\-]+|"
    r"((?:[\w-]*(?:password|secret|token|api[_-]?key)[\w-]*)[\"']?\s*[:=]\s*[\"']?)([^\s,;\"']+)"
)
_TRACE_STRING_LIMIT = 20_000
_TRACE_NODE_LIMIT = 200

try:  # MLflow's DSPy hook module is an integration detail; never fail import over it.
    from mlflow.dspy.callback import MlflowCallback as _MlflowDspyCallback
except ImportError:  # pragma: no cover - the integration moved or was removed
    _MlflowDspyCallback = None


def without_mlflow_dspy_callback(callbacks: list[Any]) -> list[Any]:
    """TypeSafe uses manual evidence spans rather than MLflow's generative-LM hook."""
    if _MlflowDspyCallback is not None:
        return [callback for callback in callbacks if not isinstance(callback, _MlflowDspyCallback)]
    return [callback for callback in callbacks if callback.__class__.__name__ != "MlflowCallback"]


@contextmanager
def evidence_span(name: str, *, inputs: dict[str, Any], span_type: Any = SpanType.LLM) -> Generator[Any, None, None]:
    """Start one decision span and record its bounded inputs, so callers do not repeat the pattern."""
    with mlflow.start_span(name=name, span_type=span_type) as span:
        span.set_inputs(inputs)
        yield span


@contextmanager
def phase_span(name: str, **inputs: Any) -> Generator[Any, None, None]:
    """Time one Turn phase and record its bounded counts, never payloads or credentials.

    A phase span without inputs or outputs tells an operator nothing about where the time went,
    so the caller sets `outputs` on the yielded span once the phase has produced its result.
    """
    with mlflow.start_span(name=name, span_type=SpanType.CHAIN) as span:
        if inputs:
            span.set_inputs(inputs)
        yield span


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


def _sanitize_tree(
    value: Any,
    *,
    nodes: list[int],
    depth: int,
    max_nodes: int,
    max_depth: int,
    max_dict_items: int,
    max_seq_items: int,
    string_limit: int,
    key_pattern: re.Pattern[str],
    node_limit_marker: str,
    depth_limit_marker: str,
    decode_base64_artifacts: bool = False,
) -> Any:
    """Shared recursive tree sanitizer for UI activity previews and MLflow trace spans."""
    nodes[0] += 1
    if nodes[0] > max_nodes:
        return node_limit_marker
    if depth > max_depth:
        return depth_limit_marker
    if isinstance(value, str):
        return activity_text(value, limit=string_limit)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, dict):
        if decode_base64_artifacts and "base64" in value and "sha256" in value:
            import base64

            # Encoded artifact bytes must not bypass recursive credential redaction.
            try:
                raw = base64.b64decode(value["base64"], validate=True)
                value = {"sha256": value["sha256"], "text": raw.decode("utf-8")}
            except (ValueError, TypeError, UnicodeError):
                value = {"sha256": value["sha256"], "text": "[binary artifact omitted]"}
        result = {}
        for key, child in islice(value.items(), max_dict_items):
            name = str(key)[:100]
            result[name] = (
                "[redacted]"
                if key_pattern.search(name)
                else _sanitize_tree(
                    child,
                    nodes=nodes,
                    depth=depth + 1,
                    max_nodes=max_nodes,
                    max_depth=max_depth,
                    max_dict_items=max_dict_items,
                    max_seq_items=max_seq_items,
                    string_limit=string_limit,
                    key_pattern=key_pattern,
                    node_limit_marker=node_limit_marker,
                    depth_limit_marker=depth_limit_marker,
                    decode_base64_artifacts=decode_base64_artifacts,
                )
            )
        if len(value) > max_dict_items:
            result["…"] = "[truncated]"
        return result
    if isinstance(value, (list, tuple)):
        items = list(islice(value, max_seq_items))
        result = [
            _sanitize_tree(
                child,
                nodes=nodes,
                depth=depth + 1,
                max_nodes=max_nodes,
                max_depth=max_depth,
                max_dict_items=max_dict_items,
                max_seq_items=max_seq_items,
                string_limit=string_limit,
                key_pattern=key_pattern,
                node_limit_marker=node_limit_marker,
                depth_limit_marker=depth_limit_marker,
                decode_base64_artifacts=decode_base64_artifacts,
            )
            for child in items
        ]
        if len(value) > len(items):
            result.append("[truncated]")
        return result
    return f"[{type(value).__name__} omitted]"


def activity_preview(value: Any, *, limit: int = 4000) -> str:
    """Preview JSON-like tool data, never arbitrary object reprs or predictor internals.

    This is not a DLP boundary: unlabelled secrets in free text cannot be reliably
    detected. Limit depth, collection sizes, and the entire serialized preview.
    """
    cleaned = _sanitize_tree(
        value,
        nodes=[0],
        depth=0,
        max_nodes=81,
        max_depth=4,
        max_dict_items=20,
        max_seq_items=20,
        string_limit=limit,
        key_pattern=_PRIVATE_KEY,
        node_limit_marker="[preview limit]",
        depth_limit_marker="[depth limit]",
    )
    text = json.dumps(cleaned, ensure_ascii=False)
    return text[:limit] + ("… [truncated]" if len(text) > limit else "")


def trace_value(value: Any, *, nodes: list[int], depth: int = 0) -> Any:
    """Bound and redact framework-captured trace data before MLflow exports it."""
    return _sanitize_tree(
        value,
        nodes=nodes,
        depth=depth,
        max_nodes=_TRACE_NODE_LIMIT,
        max_depth=8,
        max_dict_items=_TRACE_NODE_LIMIT,
        max_seq_items=50,
        string_limit=_TRACE_STRING_LIMIT,
        key_pattern=_TRACE_PRIVATE_KEY,
        node_limit_marker="[trace size limit]",
        depth_limit_marker="[trace depth limit]",
        decode_base64_artifacts=True,
    )


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


def set_mlflow_trace_experiment(
    experiment_name: str,
    *,
    databricks: bool = False,
    trace_catalog: str = "",
    trace_schema: str = "",
    trace_table_prefix: str = "",
    tracing_sql_warehouse_id: str = "",
) -> Any:
    """Activate an MLflow experiment, binding Unity Catalog trace storage when targeting Databricks."""
    if not databricks:
        return mlflow.set_experiment(experiment_name)
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
    return mlflow.set_experiment(experiment_name, trace_location=trace_location)


def configure_mlflow_observability(
    experiment_name: str = "fleet-rlm-production",
    tracking_uri: str = "sqlite:///mlflow.db",
    *,
    trace_catalog: str = "",
    trace_schema: str = "",
    trace_table_prefix: str = "",
    tracing_sql_warehouse_id: str = "",
) -> str | None:
    """Configure the MLflow destination and DSPy tracing, returning the initialized experiment ID."""
    if tracking_uri:
        mlflow.set_tracking_uri(tracking_uri)
    # DSPy autolog captures nested prompts and tool payloads; sanitize those spans before export.
    mlflow.tracing.configure(span_processors=[sanitize_trace_span])
    set_mlflow_trace_experiment(
        experiment_name,
        databricks=tracking_uri.startswith("databricks"),
        trace_catalog=trace_catalog,
        trace_schema=trace_schema,
        trace_table_prefix=trace_table_prefix,
        tracing_sql_warehouse_id=tracing_sql_warehouse_id,
    )
    mlflow.dspy.autolog(log_traces=True, log_traces_from_compile=False, log_traces_from_eval=False)
    experiment = mlflow.get_experiment_by_name(experiment_name)
    return experiment.experiment_id if experiment is not None else None
