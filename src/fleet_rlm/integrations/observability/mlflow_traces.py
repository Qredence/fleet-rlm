"""MLflow trace lookup, feedback, and dataset export helpers."""

from __future__ import annotations

import inspect
import json
from typing import TYPE_CHECKING, Any

from . import mlflow_runtime as runtime
from .config import MlflowConfig

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


def _search_traces(
    mlflow: Any,
    *,
    experiment_ids: list[str],
    max_results: int,
    return_type: str,
    include_spans: bool,
    filter_string: str | None = None,
) -> Any:
    search_traces = getattr(mlflow, "search_traces")
    parameters = inspect.signature(search_traces).parameters
    kwargs: dict[str, Any] = {
        "filter_string": filter_string,
        "max_results": max_results,
        "return_type": return_type,
        "include_spans": include_spans,
    }
    if "locations" in parameters:
        kwargs["locations"] = experiment_ids
    else:
        kwargs["experiment_ids"] = experiment_ids
    return search_traces(**kwargs)


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
        traces = _search_traces(
            mlflow,
            experiment_ids=experiment_ids,
            filter_string=(f"trace.client_request_id = '{runtime._mlflow_string_literal(client_request_id)}'"),
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
        if getattr(getattr(trace, "info", None), "client_request_id", None) == client_request_id
    ]
    if not matches:
        return None
    matches.sort(
        key=lambda trace: int(getattr(getattr(trace, "info", None), "timestamp_ms", 0) or 0),
        reverse=True,
    )
    return matches[0]


def _trace_session_id(trace: Trace) -> str | None:
    payload = trace.to_dict() if hasattr(trace, "to_dict") else {}
    info_payload = payload.get("info") if isinstance(payload, dict) else None
    info_dict = info_payload if isinstance(info_payload, dict) else _object_to_dict(getattr(trace, "info", None)) or {}
    candidates = [
        info_dict.get("session_id"),
        info_dict.get("trace_session_id"),
        info_dict.get("mlflow.trace.session_id"),
        getattr(getattr(trace, "info", None), "session_id", None),
    ]
    trace_metadata = info_dict.get("trace_metadata")
    if isinstance(trace_metadata, dict):
        candidates.extend(
            [
                trace_metadata.get("mlflow.trace.session"),
                trace_metadata.get("mlflow.traceSessionId"),
                trace_metadata.get("mlflow.trace.session_id"),
                trace_metadata.get("fleet_rlm.session_id"),
            ]
        )
    for candidate in candidates:
        value = str(candidate or "").strip()
        if value:
            return value
    return None


def search_traces_by_session_id(
    session_id: str,
    *,
    config: MlflowConfig | None = None,
    max_results: int = 5000,
    allow_unfiltered_fallback: bool = True,
) -> list[Trace]:
    """Return MLflow traces whose trace session id matches a runtime session."""
    mlflow = runtime._import_mlflow()
    if mlflow is None:
        return []

    resolved = config or runtime.get_mlflow_config()
    experiment_ids = _trace_experiment_ids(resolved)
    if not experiment_ids:
        return []

    literal = runtime._mlflow_string_literal(session_id)
    traces: list[Trace] = []
    for filter_string in (
        f"metadata.`mlflow.trace.session` = '{literal}'",
        f"trace.session_id = '{literal}'",
        f"metadata.`mlflow.traceSessionId` = '{literal}'",
        f"metadata.`mlflow.trace.session_id` = '{literal}'",
    ):
        try:
            traces = list(
                _search_traces(
                    mlflow,
                    experiment_ids=experiment_ids,
                    filter_string=filter_string,
                    max_results=max_results,
                    return_type="list",
                    include_spans=True,
                )
            )
        except Exception:
            traces = []
        if traces:
            break
    if not traces and allow_unfiltered_fallback:
        try:
            traces = list(
                _search_traces(
                    mlflow,
                    experiment_ids=experiment_ids,
                    max_results=max_results,
                    return_type="list",
                    include_spans=True,
                )
            )
        except Exception:
            runtime.logger.warning(
                "Failed to search MLflow traces for session id '%s'.",
                runtime._sanitize_log_field(session_id),
                exc_info=True,
            )
            return []

    matches = [trace for trace in traces if _trace_session_id(trace) == session_id]
    matches.sort(
        key=lambda trace: int(getattr(getattr(trace, "info", None), "timestamp_ms", 0) or 0),
        reverse=True,
    )
    return matches


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

    source = mlflow.entities.AssessmentSource(source_type="HUMAN", source_id=source_id or "anonymous")
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


def _object_to_dict(value: Any) -> dict[str, Any] | None:
    """Best-effort conversion for MLflow trace/span objects."""
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    for method_name in ("to_dictionary", "to_dict"):
        method = getattr(value, method_name, None)
        if callable(method):
            try:
                result = method()
            except Exception:
                continue
            if isinstance(result, dict):
                return result
    data: dict[str, Any] = {}
    for attr_name in (
        "trace_id",
        "client_request_id",
        "request_id",
        "span_id",
        "parent_id",
        "name",
        "status",
        "span_type",
        "type",
        "start_time_ns",
        "end_time_ns",
        "inputs",
        "outputs",
        "attributes",
    ):
        if hasattr(value, attr_name):
            data[attr_name] = getattr(value, attr_name)
    return data or None


def _trace_span_dicts(trace: Trace) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []
    try:
        raw_spans = trace.search_spans()
    except Exception:
        raw_spans = []
    for span in raw_spans or []:
        data = _object_to_dict(span)
        if data is not None:
            spans.append(data)
    return spans


def _trace_span_types(trace: Trace) -> list[str]:
    span_types: list[str] = []
    seen: set[str] = set()
    for span in _trace_span_dicts(trace):
        candidate = str(span.get("span_type") or span.get("type") or "").strip()
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        span_types.append(candidate)
    return span_types


def trace_to_full_payload(
    trace: Trace,
    *,
    session_id: str | None = None,
    turn_id: str | None = None,
    external_trace_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Serialize a full MLflow trace for audit and offline GEPA distillation."""
    payload = trace.to_dict() if hasattr(trace, "to_dict") else {}
    if not isinstance(payload, dict):
        payload = {}

    info_payload = payload.get("info")
    if not isinstance(info_payload, dict):
        info_payload = _object_to_dict(getattr(trace, "info", None)) or {}
        payload["info"] = info_payload

    data_payload = payload.get("data")
    if not isinstance(data_payload, dict):
        data_payload = _object_to_dict(getattr(trace, "data", None)) or {}
        if data_payload:
            payload["data"] = data_payload

    metadata = dict(external_trace_metadata or {})
    trace_metadata = info_payload.get("trace_metadata")
    if isinstance(trace_metadata, dict):
        metadata.update(trace_metadata)

    payload.update(
        {
            "session_id": session_id,
            "turn_id": turn_id,
            "trace_id": info_payload.get("trace_id") or getattr(getattr(trace, "info", None), "trace_id", None),
            "client_request_id": info_payload.get("client_request_id")
            or getattr(getattr(trace, "info", None), "client_request_id", None),
            "metadata": metadata,
            "spans": _trace_span_dicts(trace),
            "assessments": _trace_assessment_dicts(trace),
        }
    )
    return payload


def _assessment_source_field(assessment: dict[str, Any], key: str) -> str:
    source = assessment.get("source")
    if not isinstance(source, dict):
        return ""
    value = source.get(key)
    return str(value or "").strip()


def _skip_external_persisted_scorer_feedback(
    assessment: dict[str, Any],
    *,
    disabled_persisted_scorers: set[str],
) -> bool:
    if not disabled_persisted_scorers:
        return False
    name = str(assessment.get("assessment_name") or "").strip()
    if name not in disabled_persisted_scorers:
        return False
    return _assessment_source_field(assessment, "source_type") == "LLM_JUDGE"


def _disabled_persisted_scorer_names(config: MlflowConfig | None) -> set[str]:
    resolved = config or runtime.get_mlflow_config()
    if resolved.enable_auto_assessment:
        return set()
    try:
        from .auto_assessment import persisted_scorer_names

        return set(persisted_scorer_names(resolved))
    except Exception:
        runtime.logger.debug("Failed to inspect persisted MLflow scorers for trace export.", exc_info=True)
        return set()


def trace_to_dataset_row(
    trace: Trace,
    *,
    config: MlflowConfig | None = None,
) -> dict[str, Any]:
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
    skipped_feedback: list[dict[str, str]] = []
    disabled_persisted_scorers = _disabled_persisted_scorer_names(config)
    for assessment in _trace_assessment_dicts(trace):
        if _skip_external_persisted_scorer_feedback(
            assessment,
            disabled_persisted_scorers=disabled_persisted_scorers,
        ):
            skipped_feedback.append(
                {
                    "assessment_name": str(assessment.get("assessment_name") or ""),
                    "source_type": _assessment_source_field(assessment, "source_type"),
                    "source_id": _assessment_source_field(assessment, "source_id"),
                    "reason": "persisted_scorer_while_fleet_auto_assessment_disabled",
                }
            )
            continue
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
    if skipped_feedback:
        row["skipped_feedback"] = skipped_feedback
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
        traces = _search_traces(
            mlflow,
            experiment_ids=experiment_ids,
            max_results=max_results,
            return_type="list",
            include_spans=False,
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
        row = trace_to_dataset_row(trace, config=resolved)
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
    "search_traces_by_session_id",
    "search_annotated_trace_rows",
    "trace_to_full_payload",
    "trace_to_dataset_row",
]
