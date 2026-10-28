"""Native usage receipts, callback scopes, redacted activity previews, and MLflow instrumentation."""

from __future__ import annotations

import json
import math
import os
import re
import threading
from collections.abc import Generator
from contextlib import contextmanager, nullcontext, suppress
from itertools import islice
from typing import Any

import dspy
import mlflow
from dspy import track_usage
from dspy.utils import BaseCallback
from dspy.utils.usage_tracker import UsageTracker
from mlflow.entities import SpanType


def _usage_summary(entry: dict[str, Any], *, derive_total: bool = True) -> dict[str, int | float]:
    details = entry.get("completion_tokens_details")
    found = {
        "prompt_tokens": entry.get("prompt_tokens"),
        "completion_tokens": entry.get("completion_tokens"),
        "total_tokens": entry.get("total_tokens"),
        "reasoning_tokens": details.get("reasoning_tokens") if isinstance(details, dict) else None,
    }
    summary = {
        key: value
        for key, value in found.items()
        if isinstance(value, (int, float))
        and not isinstance(value, bool)
        and value >= 0
        and (not isinstance(value, float) or math.isfinite(value))
    }
    if derive_total and "total_tokens" not in summary and {"prompt_tokens", "completion_tokens"} <= summary.keys():
        summary["total_tokens"] = summary["prompt_tokens"] + summary["completion_tokens"]
    return summary


class _DecisionWorkerUsage(BaseCallback):
    """Bridge usage from native DSPy batch workers, whose trackers are deep copies."""

    def __init__(self, target: UsageTracker) -> None:
        self.target = target
        self.scopes: dict[str, tuple[Any, UsageTracker, Any]] = {}
        self.lock = threading.Lock()

    def on_lm_start(self, call_id: str, instance: Any, inputs: dict[str, Any]) -> None:  # noqa: ARG002
        if not getattr(instance, "supports_decision_requests", False):
            return
        parent = dspy.settings.usage_tracker
        scope = track_usage()
        tracker = scope.__enter__()
        with self.lock:
            self.scopes[call_id] = (scope, tracker, parent)

    def on_lm_end(self, call_id: str, outputs: Any, exception: BaseException | None = None) -> None:  # noqa: ARG002
        with self.lock:
            state = self.scopes.pop(call_id, None)
        if state is None:
            return
        scope, tracker, parent = state
        try:
            if parent is not self.target:
                with self.lock:
                    for model, entries in tracker.usage_data.items():
                        for entry in entries:
                            self.target.add_usage(model, entry)
        finally:
            scope.__exit__(None, None, None)


_TOKEN_KEY_MAP = (
    ("prompt_tokens", "input_tokens"),
    ("completion_tokens", "output_tokens"),
    ("total_tokens", "total_tokens"),
)


def _to_mlflow_token_usage(usage: dict[str, int | float]) -> dict[str, int | float]:
    return {target: usage[source] for source, target in _TOKEN_KEY_MAP if source in usage}


@contextmanager
def decision_usage(span: Any) -> Generator[None, None, None]:
    """Receipt TypeSafe usage on its manual span and roll it into DSPy exactly once.

    DSPy 3.4.1 TypeSafe reports prompt/completion counts without total_tokens.
    A detached native tracker also collects batched worker usage without allowing
    the generation collector to attribute these calls to the surrounding module.
    """
    parent = dspy.settings.usage_tracker
    tracker = UsageTracker()
    collector = _DecisionWorkerUsage(tracker)
    try:
        with dspy.context(usage_tracker=tracker, callbacks=[*dspy.settings.callbacks, collector]):
            yield
    finally:
        totals: dict[str, int | float] = {}
        for model, entries in tracker.usage_data.items():
            for entry in entries:
                usage = _usage_summary(entry)
                for target, count in _to_mlflow_token_usage(usage).items():
                    totals[target] = totals.get(target, 0) + count
                if parent is not None:
                    parent.add_usage(model, {**entry, **usage})
        if totals:
            span.set_attribute("mlflow.chat.tokenUsage", totals)


class LMUsageCallback(BaseCallback):
    """Isolate each generative call using native nested trackers, which roll up on exit.

    This callback must precede MLflow's DSPy callback: its end handler annotates the
    still-active LM span before MLflow closes it. Contexts are entered/exited in the
    calling thread/task, never from a stream consumer or cleanup worker.
    """

    def __init__(self) -> None:
        self._usage_scopes: dict[str, tuple[Any, Any, str]] = {}
        self._usage_lock = threading.Lock()

    def on_lm_start(self, call_id: str, instance: Any, inputs: dict[str, Any]) -> None:  # noqa: ARG002
        """Capture numeric generation usage, excluding TypeSafe decision calls."""
        if getattr(instance, "supports_decision_requests", False):
            return
        scope = track_usage()
        tracker = scope.__enter__()
        with self._usage_lock:
            self._usage_scopes[call_id] = (scope, tracker, str(getattr(instance, "model", "unknown")))

    def finish_usage(self, call_id: str) -> dict[str, int | float]:
        """Annotate one call and restore its parent tracker even on a failed call."""
        with self._usage_lock:
            state = self._usage_scopes.pop(call_id, None)
        if state is None:
            return {}
        scope, tracker, model = state
        try:
            for entries in tracker.usage_data.values():
                for entry in entries:
                    normalized = _usage_summary(entry)
                    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                        if key in normalized:
                            entry[key] = normalized[key]
                        else:
                            entry.pop(key, None)
            # Partial receipts must not manufacture a complete total when combined.
            usage = _usage_summary(tracker.get_total_tokens().get(model, {}), derive_total=False)
            span = mlflow.get_current_active_span()
            if usage and span is not None and span.span_type in (SpanType.LLM, SpanType.CHAT_MODEL):
                tokens = _to_mlflow_token_usage(usage)
                if tokens:
                    span.set_attribute("mlflow.chat.tokenUsage", tokens)
            return usage
        finally:
            scope.__exit__(None, None, None)

    def on_lm_end(self, call_id: str, outputs: Any, exception: BaseException | None = None) -> None:  # noqa: ARG002
        """Retain any reported failed-call usage; never manufacture missing usage."""
        self.finish_usage(call_id)


def usage_callbacks(callbacks: list[Any]) -> list[Any]:
    """Reuse the stream collector when present, otherwise collect for non-streaming evaluation."""
    collectors = [callback for callback in callbacks if isinstance(callback, LMUsageCallback)]
    collector = collectors[0] if collectors else LMUsageCallback()
    return [collector, *(callback for callback in callbacks if not isinstance(callback, LMUsageCallback))]


def ambient_callbacks() -> list[Any]:
    """The ambient callback list; `dspy.context` replaces callbacks, so a scope must re-spread them."""
    return list(dspy.settings.callbacks)


def turn_callbacks(*extra: Any) -> list[Any]:
    """Turn callbacks with the numeric usage collector, reused when the caller already supplies one."""
    return usage_callbacks([*ambient_callbacks(), *extra])


def stream_callbacks(*extra: Any) -> list[Any]:
    """Ambient callbacks with a caller's collector appended, keeping the caller's order.

    `dspy.context` replaces the list, so any scope that needs the ambient callbacks must re-spread
    them; this is the one place that does it for a streaming collector that is not a usage collector.
    """
    return [*ambient_callbacks(), *extra]


def typesafe_callbacks() -> list[Any]:
    """Ambient callbacks without MLflow's DSPy hook, for typed decisions that emit their own spans."""
    return without_mlflow_dspy_callback(ambient_callbacks())


@contextmanager
def typed_decision_scope(*, lm: Any, span: Any, typesafe: bool) -> Generator[None, None, None]:
    """One typed decision's scope: no LM history, the right callbacks, and usage on its own span.

    TypeSafe clients emit their own usage, so only they are metered here; a generative backend
    already reports usage through the LM callback and must keep the MLflow hook off.
    """
    with (
        dspy.context(lm=lm, disable_history=True, callbacks=typesafe_callbacks() if typesafe else ambient_callbacks()),
        decision_usage(span) if typesafe else nullcontext(),
    ):
        yield


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
