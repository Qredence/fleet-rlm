"""Annotate persisted ``fleet_turn`` traces with derived aggregate attributes.

Engineering-observability companion to ``fleet_rlm.observability.turn_tracing``.
Persisted traces carry rich span structure (DSPy autolog LLM/tool spans) but
only a small fixed set of tags. This script walks persisted traces and stamps
derived, non-content ``fleet.*`` trace tags so the MLflow UI and
``search_traces`` ``filter_string`` queries can select by model, provider,
tool, latency, and token usage.

It never mutates the live Turn path and never exports prompt, response, or
other content-bearing span payloads: only bounded aggregates become tags.
All commands require ``FLEET_LIVE=1`` and write a bounded JSON receipt.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

RECEIPT_SCHEMA = "fleet.trace-annotation/v1"
DEFAULT_MLFLOW_URL = "databricks"
DEFAULT_TAG_PREFIX = "fleet."
_MAX_TAG_VALUE_CHARS = 256
_MAX_LIMIT = 500
_LIVE_VALUES = frozenset({"1", "true", "yes"})


class AnnotationError(RuntimeError):
    """An annotation precondition or MLflow contract failed."""


def _require_live() -> None:
    """
    Enforce the explicit live opt-in for credentialed MLflow access.

    Raises:
        AnnotationError: If ``FLEET_LIVE`` is not enabled.
    """
    if os.environ.get("FLEET_LIVE", "").lower() not in _LIVE_VALUES:
        raise AnnotationError("FLEET_LIVE=1 is required for trace annotation")


def _experiment_name_default() -> str:
    return os.environ.get("FLEET_MLFLOW_EXPERIMENT_NAME", "fleet-rlm")


def _resolve_experiment_id(args: argparse.Namespace) -> str:
    """
    Resolve the target experiment id from an explicit id or the experiment name.

    Parameters:
        args (argparse.Namespace): Parsed CLI arguments.

    Returns:
        str: The resolved experiment id.

    Raises:
        AnnotationError: If the experiment name does not resolve.
    """
    import mlflow

    mlflow.set_tracking_uri(args.mlflow_url)
    if args.experiment_id:
        return str(args.experiment_id)
    experiment = mlflow.get_experiment_by_name(args.experiment_name)
    if experiment is None:
        raise AnnotationError(f"MLflow experiment not found: {args.experiment_name!r}")
    return str(experiment.experiment_id)


def _span_attribute(span: Any, key: str) -> Any:
    """Read one span attribute defensively across MLflow span implementations."""
    getter = getattr(span, "get_attribute", None)
    if callable(getter):
        try:
            value = getter(key)
            if value is not None:
                return value
        except Exception:
            pass
    attributes = getattr(span, "attributes", None)
    if isinstance(attributes, Mapping):
        return attributes.get(key)
    return None


def _bounded_tag(value: object) -> str:
    """Bound a derived tag value; never export span content."""
    text = str(value)
    if len(text) > _MAX_TAG_VALUE_CHARS:
        text = text[:_MAX_TAG_VALUE_CHARS]
    return text


def _join_distinct(values: Sequence[str]) -> str:
    """Join distinct non-empty values deterministically."""
    return ",".join(sorted({value for value in values if value}))


def _token_count(value: Any) -> int:
    """Coerce a token count to a bounded non-negative int."""
    try:
        count = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, count)


def derive_attributes(trace: Any) -> dict[str, str]:
    """
    Derive bounded aggregate attributes from one persisted trace.

    Parameters:
        trace (Any): MLflow Trace with ``info`` and ``data.spans``.

    Returns:
        dict[str, str]: Non-content ``fleet.*`` tag values for the trace.
    """
    info = getattr(trace, "info", None)
    data = getattr(trace, "data", None)
    spans = list(getattr(data, "spans", None) or [])
    state = getattr(info, "state", None)
    state_text = str(state).lower() if state is not None else "unknown"

    models: list[str] = []
    providers: list[str] = []
    tools: list[str] = []
    span_type_counts: Counter[str] = Counter()
    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0

    for span in spans:
        span_type = str(getattr(span, "span_type", "") or "").upper()
        name = str(getattr(span, "name", "") or "")
        span_type_counts[span_type or "UNKNOWN"] += 1
        if span_type == "LLM":
            model = _span_attribute(span, "model_name") or _span_attribute(span, "model")
            if model:
                models.append(str(model))
            provider = _span_attribute(span, "provider")
            if provider:
                providers.append(str(provider))
            usage = _span_attribute(span, "usage") or _span_attribute(span, "token_usage")
            if isinstance(usage, Mapping):
                prompt_tokens += _token_count(usage.get("prompt_tokens"))
                completion_tokens += _token_count(usage.get("completion_tokens"))
                total_tokens += _token_count(usage.get("total_tokens"))
            elif usage is not None:
                total_tokens += _token_count(usage)
        if span_type == "TOOL" and name:
            tools.append(name)

    execution_duration = getattr(info, "execution_duration", None)
    latency_ms = int(execution_duration) if execution_duration is not None else None
    if latency_ms is None and spans:
        root = min(spans, key=lambda span: getattr(span, "start_time_ns", 0) or 0)
        start_ns = getattr(root, "start_time_ns", None)
        end_ns = getattr(root, "end_time_ns", None)
        if start_ns is not None and end_ns is not None:
            latency_ms = max(0, int((int(end_ns) - int(start_ns)) // 1_000_000))

    attributes: dict[str, str] = {
        f"{DEFAULT_TAG_PREFIX}turn_status": _bounded_tag(state_text),
    }
    if latency_ms is not None:
        attributes[f"{DEFAULT_TAG_PREFIX}latency_ms"] = str(latency_ms)
    if models:
        attributes[f"{DEFAULT_TAG_PREFIX}models"] = _bounded_tag(_join_distinct(models))
    if providers:
        attributes[f"{DEFAULT_TAG_PREFIX}providers"] = _bounded_tag(_join_distinct(providers))
    if tools:
        attributes[f"{DEFAULT_TAG_PREFIX}tools"] = _bounded_tag(_join_distinct(tools))
    if total_tokens:
        attributes[f"{DEFAULT_TAG_PREFIX}total_tokens"] = str(total_tokens)
    if prompt_tokens:
        attributes[f"{DEFAULT_TAG_PREFIX}prompt_tokens"] = str(prompt_tokens)
    if completion_tokens:
        attributes[f"{DEFAULT_TAG_PREFIX}completion_tokens"] = str(completion_tokens)
    if span_type_counts:
        attributes[f"{DEFAULT_TAG_PREFIX}span_types"] = _bounded_tag(
            ",".join(f"{span_type.lower()}:{count}" for span_type, count in sorted(span_type_counts.items()))
        )
    return attributes


def annotate(args: argparse.Namespace) -> dict[str, Any]:
    """
    Walk persisted traces and stamp derived ``fleet.*`` trace tags.

    Parameters:
        args (argparse.Namespace): Connection, selection, and limit options.

    Returns:
        dict[str, Any]: Annotation receipt with bounded aggregates.
    """
    _require_live()
    experiment_id = _resolve_experiment_id(args)
    import mlflow
    from mlflow.tracking.client import MlflowClient

    mlflow.set_tracking_uri(args.mlflow_url)
    filter_string = f"tag.{args.tag} = 'true'" if args.tag else None
    traces = mlflow.search_traces(
        locations=[experiment_id],
        filter_string=filter_string,
        max_results=args.limit,
        return_type="list",
    )
    client = MlflowClient()
    annotated = 0
    skipped = 0
    tag_counts: Counter[str] = Counter()
    for trace in traces:
        info = getattr(trace, "info", None)
        trace_id = getattr(info, "trace_id", None)
        if not trace_id:
            skipped += 1
            continue
        attributes = derive_attributes(trace)
        if not attributes:
            skipped += 1
            continue
        for key, value in attributes.items():
            client.set_trace_tag(str(trace_id), key, value)
            tag_counts[key] += 1
        annotated += 1
    return {
        "command": "annotate",
        "experiment_id": experiment_id,
        "tag": args.tag,
        "limit": args.limit,
        "traces_seen": len(traces),
        "traces_annotated": annotated,
        "traces_skipped": skipped,
        "tags_written": sum(tag_counts.values()),
        "tag_counts": dict(sorted(tag_counts.items())),
    }


def build_parser() -> argparse.ArgumentParser:
    """
    Create the command-line argument parser for trace annotation.

    Returns:
        argparse.ArgumentParser: Parser configured with connection and
        selection options.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("annotate",))
    parser.add_argument("--mlflow-url", default=DEFAULT_MLFLOW_URL)
    parser.add_argument("--experiment-id", default="")
    parser.add_argument("--experiment-name", default=_experiment_name_default())
    parser.add_argument(
        "--tag",
        default="",
        help="Only annotate traces carrying tag.<tag>='true' (e.g. fleet_eval_candidate)",
    )
    parser.add_argument("--limit", type=int, default=100, help="Maximum traces to annotate (default: 100)")
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """
    Run the annotation command and write its result as a JSON receipt.

    Parameters:
        argv (Sequence[str] | None): Optional command-line arguments; uses the
            process arguments when omitted.

    Returns:
        int: `0` when the command succeeds, `1` when it fails.
    """
    load_dotenv(_REPO_ROOT / ".env", override=False)
    args = build_parser().parse_args(argv)
    try:
        if not 1 <= args.limit <= _MAX_LIMIT:
            raise AnnotationError(f"--limit must be in [1, {_MAX_LIMIT}]")
        receipt = annotate(args)
    except Exception as exc:
        receipt = {
            "schema": RECEIPT_SCHEMA,
            "generated_at": datetime.now(UTC).isoformat(),
            "command": args.command,
            "status": "failed",
            "error_category": type(exc).__name__,
        }
        exit_code = 1
    else:
        receipt = {
            "schema": RECEIPT_SCHEMA,
            "generated_at": datetime.now(UTC).isoformat(),
            "status": "ok",
            **receipt,
        }
        exit_code = 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
