"""MLflow trace lookup, feedback, and dataset export helpers."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from .config import MlflowConfig
from . import mlflow_runtime as runtime

if TYPE_CHECKING:
    from mlflow.entities.trace import Trace


def _trace_experiment_ids(config: MlflowConfig) -> list[str]:
    mlflow = runtime._import_mlflow()
    if mlflow is None:
        return []
    if not runtime.initialize_mlflow(config):
        return []
    experiment = mlflow.get_experiment_by_name(config.experiment)
    if experiment is None:
        return []
    return [experiment.experiment_id]


def resolve_trace_by_client_request_id(
    client_request_id: str,
    *,
    config: MlflowConfig | None = None,
    max_results: int = 5000,
) -> Trace | None:
    """Resolve the most recent trace for a given client request id."""
    mlflow = runtime._import_mlflow()
    if mlflow is None:
        return None

    resolved = config or runtime.get_mlflow_config()
    experiment_ids = _trace_experiment_ids(resolved)
    if not experiment_ids:
        return None

    try:
        traces = mlflow.search_traces(
            experiment_ids=experiment_ids,
            filter_string=(
                "trace.client_request_id = "
                f"'{runtime._mlflow_string_literal(client_request_id)}'"
            ),
            max_results=max_results,
            return_type="list",
            include_spans=False,
        )
    except Exception:
        runtime.logger.warning(
            "Failed to search MLflow traces for client request id '%s'.",
            runtime._sanitize_log_field(client_request_id),
            exc_info=True,
        )
        return None
    matches = [
        trace
        for trace in traces
        if getattr(getattr(trace, "info", None), "client_request_id", None)
        == client_request_id
    ]
    if not matches:
        return None
    matches.sort(
        key=lambda trace: int(
            getattr(getattr(trace, "info", None), "timestamp_ms", 0) or 0
        ),
        reverse=True,
    )
    return matches[0]


def resolve_trace(
    *,
    trace_id: str | None = None,
    client_request_id: str | None = None,
    config: MlflowConfig | None = None,
) -> Trace | None:
    """Resolve a trace by explicit trace id or fallback client request id."""
    mlflow = runtime._import_mlflow()
    if mlflow is None:
        return None

    if trace_id:
        try:
            return mlflow.get_trace(trace_id)
        except Exception:
            runtime.logger.warning(
                "Failed to load MLflow trace '%s'.",
                runtime._sanitize_log_field(trace_id),
                exc_info=True,
            )
            return None

    if client_request_id:
        return resolve_trace_by_client_request_id(
            client_request_id,
            config=config,
        )
    return None


def log_trace_feedback(
    *,
    trace_id: str,
    is_correct: bool,
    source_id: str,
    comment: str | None = None,
    expected_response: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, bool]:
    """Log human feedback and optional ground-truth expectation for a trace."""
    mlflow = runtime._import_mlflow()
    if mlflow is None:
        raise RuntimeError("MLflow is not installed.")

    source = mlflow.entities.AssessmentSource(
        source_type="HUMAN", source_id=source_id or "anonymous"
    )
    mlflow.log_feedback(
        trace_id=trace_id,
        name="response_is_correct",
        value=is_correct,
        source=source,
        rationale=(comment or None),
        metadata=metadata,
    )

    expectation_logged = False
    candidate = (expected_response or "").strip()
    if candidate:
        mlflow.log_expectation(
            trace_id=trace_id,
            name="expected_response",
            value=candidate,
            source=source,
            metadata=metadata,
        )
        expectation_logged = True

    return {
        "feedback_logged": True,
        "expectation_logged": expectation_logged,
    }


def _parse_trace_metadata_field(
    metadata: dict[str, Any],
    key: str,
) -> Any:
    raw = metadata.get(key)
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    if not isinstance(raw, str):
        return raw
    text = raw.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _trace_assessment_dicts(trace: Trace) -> list[dict[str, Any]]:
    assessments = []
    try:
        raw = trace.search_assessments()
    except Exception:
        raw = []
    for item in raw or []:
        if hasattr(item, "to_dictionary"):
            data = item.to_dictionary()
        elif hasattr(item, "to_dict"):
            data = item.to_dict()
        else:
            data = None
        if isinstance(data, dict):
            assessments.append(data)
    return assessments


def _trace_span_types(trace: Trace) -> list[str]:
    span_types: list[str] = []
    seen: set[str] = set()
    try:
        raw_spans = trace.search_spans()
    except Exception:
        raw_spans = []

    for span in raw_spans or []:
        candidate = str(
            getattr(span, "span_type", None) or getattr(span, "type", None) or ""
        ).strip()
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        span_types.append(candidate)
    return span_types


def trace_to_dataset_row(trace: Trace) -> dict[str, Any]:
    """Convert an MLflow trace into an evaluation/export dataset row."""
    payload = trace.to_dict()
    info = payload.get("info", {}) if isinstance(payload, dict) else {}
    metadata = info.get("trace_metadata", {}) if isinstance(info, dict) else {}

    inputs = _parse_trace_metadata_field(metadata, "mlflow.traceInputs")
    outputs = _parse_trace_metadata_field(metadata, "mlflow.traceOutputs")
    if inputs is None:
        inputs = info.get("request_preview")
    if outputs is None:
        outputs = info.get("response_preview")

    expectations: dict[str, Any] = {}
    feedback: dict[str, Any] = {}
    for assessment in _trace_assessment_dicts(trace):
        name = str(assessment.get("assessment_name") or "assessment")
        source = assessment.get("source") or {}
        source_id = source.get("source_id") if isinstance(source, dict) else None

        expectation = assessment.get("expectation")
        if isinstance(expectation, dict) and "value" in expectation:
            expectations[name] = expectation["value"]

        feedback_payload = assessment.get("feedback")
        if isinstance(feedback_payload, dict) and "value" in feedback_payload:
            feedback[name] = {
                "value": feedback_payload["value"],
                "rationale": assessment.get("rationale"),
                "source_id": source_id,
            }

    row: dict[str, Any] = {
        "trace_id": info.get("trace_id"),
        "client_request_id": info.get("client_request_id"),
        "inputs": inputs,
        "outputs": outputs,
        "expectations": expectations,
    }
    span_types = _trace_span_types(trace)
    if span_types:
        row["span_types"] = span_types
    if feedback:
        row["feedback"] = feedback
    return row


def search_annotated_trace_rows(
    *,
    config: MlflowConfig | None = None,
    max_results: int = 5000,
) -> list[dict[str, Any]]:
    """Search the configured experiment and return rows for annotated traces."""
    mlflow = runtime._import_mlflow()
    if mlflow is None:
        return []

    resolved = config or runtime.get_mlflow_config()
    experiment_ids = _trace_experiment_ids(resolved)
    if not experiment_ids:
        return []

    try:
        traces = mlflow.search_traces(
            experiment_ids=experiment_ids,
            max_results=max_results,
            return_type="list",
        )
    except Exception as exc:  # noqa: BLE001
        runtime.logger.warning(
            "Failed to search MLflow traces for experiments %s: %s",
            experiment_ids,
            exc,
        )
        return []
    rows: list[dict[str, Any]] = []
    for trace in traces:
        row = trace_to_dataset_row(trace)
        if row.get("expectations") or row.get("feedback"):
            rows.append(row)
    rows.sort(
        key=lambda row: str(row.get("trace_id") or ""),
    )
    return rows


__all__ = [
    "log_trace_feedback",
    "resolve_trace",
    "resolve_trace_by_client_request_id",
    "search_annotated_trace_rows",
    "trace_to_dataset_row",
]
