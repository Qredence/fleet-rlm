"""TraceRecord dataclass for normalizing MLflow trace data."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TrajectorySpan:
    """Represents a single span in the execution trajectory."""

    name: str
    kind: str
    start: float
    end: float
    tool_name: str | None = None
    tool_input: str | None = None
    tool_output: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass
class TraceRecord:
    """Normalized representation of an MLflow trace for evaluation.

    This dataclass extracts and normalizes all relevant fields from an MLflow
    trace dictionary into a structured format suitable for scoring.
    """

    trace_id: str
    route: str
    user_request: str
    core_memory: str
    history: list[dict[str, Any]]
    active_skills: list[str]
    context: str
    trajectory_spans: list[TrajectorySpan]
    final_answer: str
    timeouts: dict[str, Any]
    trace_outputs: dict[str, Any]
    metadata: dict[str, Any]
    token_cost: int
    latency_s: float
    parent_span_id: str | None = None

    @classmethod
    def from_mlflow_trace(cls, trace_dict: dict[str, Any]) -> TraceRecord:
        """Create a TraceRecord from an MLflow trace dictionary.

        Args:
            trace_dict: Dictionary representation of an MLflow trace. MLflow traces
                have a nested structure with 'info' and 'data' at the top level.
                The 'info' section contains metadata like trace_id, request_time, etc.
                The 'data' section contains the actual trace data (spans, etc.).

        Returns:
            A normalized TraceRecord with all fields populated.

        Raises:
            KeyError: If required fields are missing from the trace.
            TypeError: If trace_dict is not a dictionary.
        """
        import json

        if not isinstance(trace_dict, dict):
            msg = f"trace_dict must be a dict, got {type(trace_dict).__name__}"
            raise TypeError(msg)

        # MLflow traces have nested structure: {info: {...}, data: {...}}
        info = trace_dict.get("info", {})
        data = trace_dict.get("data", {})

        # Also support flat structure for backwards compatibility
        if not info and not data:
            info = trace_dict
            data = trace_dict

        # Extract trace_id from info section
        trace_id = str(info.get("trace_id", info.get("traceId", "")))

        # Extract spans from data section
        spans_data = data.get("spans", data.get("span_data", []))
        if not isinstance(spans_data, list):
            spans_data = []

        trajectory_spans = []
        for span in spans_data:
            if isinstance(span, dict):
                # Extract span attributes
                attributes = span.get("attributes", {})
                if isinstance(attributes, str):
                    try:
                        attributes = json.loads(attributes)
                    except json.JSONDecodeError:
                        attributes = {}

                # Extract timestamps - MLflow uses start_time_unix_nano (nanoseconds)
                # Convert to seconds by dividing by 1e9 if value is in nanoseconds (> 1e15)
                start_nano = float(span.get("start_time_unix_nano", span.get("start_time", span.get("startTime", 0))))
                end_nano = float(span.get("end_time_unix_nano", span.get("end_time", span.get("endTime", 0))))
                start_s = start_nano / 1e9 if start_nano > 1e15 else start_nano
                end_s = end_nano / 1e9 if end_nano > 1e15 else end_nano

                trajectory_spans.append(
                    TrajectorySpan(
                        name=str(span.get("name", "")),
                        kind=str(span.get("kind", span.get("spanKind", "UNKNOWN"))),
                        start=start_s,
                        end=end_s,
                        tool_name=span.get("tool_name", attributes.get("gen_ai.tool.name")),
                        tool_input=span.get("tool_input", span.get("inputs")),
                        tool_output=span.get("tool_output", span.get("outputs")),
                        attributes=attributes,
                    )
                )

        # Extract metadata from info section
        trace_metadata = info.get("trace_metadata", {})
        if isinstance(trace_metadata, str):
            try:
                trace_metadata = json.loads(trace_metadata)
            except json.JSONDecodeError:
                trace_metadata = {}

        # Extract inputs - check flat structure first, then trace_metadata, then root span attributes
        inputs = trace_dict.get("inputs", {})
        if not inputs:
            # Try to get from trace_metadata (nested MLflow structure)
            inputs_str = trace_metadata.get("mlflow.traceInputs", "{}")
            try:
                inputs = json.loads(inputs_str) if isinstance(inputs_str, str) else inputs_str
            except json.JSONDecodeError:
                inputs = {}

        if not inputs:
            # Fallback: try root span's mlflow.spanInputs attribute
            for span in spans_data:
                if isinstance(span, dict):
                    span_attrs = span.get("attributes", {})
                    if isinstance(span_attrs, str):
                        try:
                            span_attrs = json.loads(span_attrs)
                        except json.JSONDecodeError:
                            span_attrs = {}
                    span_inputs_str = span_attrs.get("mlflow.spanInputs")
                    if span_inputs_str:
                        try:
                            inputs = (
                                json.loads(span_inputs_str) if isinstance(span_inputs_str, str) else span_inputs_str
                            )
                            break
                        except json.JSONDecodeError:
                            pass

        if not isinstance(inputs, dict):
            inputs = {"message": str(inputs)} if inputs else {}

        user_request = str(inputs.get("user_request", inputs.get("question", inputs.get("message", ""))))
        core_memory = str(inputs.get("core_memory", inputs.get("memory", "")))
        history = inputs.get("history", [])
        if not isinstance(history, list):
            history = []
        active_skills = inputs.get("active_skills", inputs.get("skills", []))
        if not isinstance(active_skills, list):
            # Try to parse from comma-separated string
            if isinstance(active_skills, str):
                active_skills = [s.strip() for s in active_skills.split(",") if s.strip()]
            else:
                active_skills = []

        # Fallback: populate active_skills from selected_skills in metadata
        if not active_skills:
            selected_skills_str = trace_metadata.get("fleet_rlm.selected_skills", "")
            if isinstance(selected_skills_str, str) and selected_skills_str:
                active_skills = [s.strip() for s in selected_skills_str.split(",") if s.strip()]
        context = str(inputs.get("context", ""))

        # Extract outputs - check flat structure first, then trace_metadata, then root span attributes
        outputs = trace_dict.get("outputs", {})
        if not outputs:
            # Try to get from trace_metadata (nested MLflow structure)
            outputs_str = trace_metadata.get("mlflow.traceOutputs", "{}")
            try:
                outputs = json.loads(outputs_str) if isinstance(outputs_str, str) else outputs_str
            except json.JSONDecodeError:
                outputs = {}

        if not outputs:
            # Fallback: try root span's mlflow.spanOutputs attribute
            for span in spans_data:
                if isinstance(span, dict):
                    span_attrs = span.get("attributes", {})
                    if isinstance(span_attrs, str):
                        try:
                            span_attrs = json.loads(span_attrs)
                        except json.JSONDecodeError:
                            span_attrs = {}
                    span_outputs_str = span_attrs.get("mlflow.spanOutputs")
                    if span_outputs_str:
                        try:
                            outputs = (
                                json.loads(span_outputs_str) if isinstance(span_outputs_str, str) else span_outputs_str
                            )
                            break
                        except json.JSONDecodeError:
                            pass

        if isinstance(outputs, dict):
            final_answer = str(outputs.get("final_answer", outputs.get("answer", outputs.get("response", ""))))
        else:
            final_answer = str(outputs) if outputs else ""

        # Fallback to response_preview if no final_answer
        if not final_answer:
            final_answer = str(info.get("response_preview", ""))

        # Extract route from trace_metadata or tags
        # Priority: routing_decision > execution_mode > selected_skills
        routing_decision = str(
            trace_metadata.get(
                "fleet_rlm.routing_decision",
                info.get("tags", {}).get("fleet_rlm.routing_decision", ""),
            )
        )
        execution_mode = str(
            trace_metadata.get(
                "fleet_rlm.execution_mode",
                info.get("tags", {}).get("fleet_rlm.execution_mode", trace_dict.get("route", "")),
            )
        )

        # Use routing_decision if available (e.g., "large_context_rlm", "cot", "react")
        if routing_decision and routing_decision != "auto":
            route = routing_decision
        elif execution_mode and execution_mode != "auto":
            route = execution_mode
        else:
            # Fall back to selected_skills when both are "auto" or empty
            selected_skills = str(
                trace_metadata.get(
                    "fleet_rlm.selected_skills",
                    info.get("tags", {}).get("fleet_rlm.selected_skills", ""),
                )
            )
            route = selected_skills if selected_skills else execution_mode

        # Extract timeouts
        timeouts = trace_dict.get("timeouts", {})
        if not isinstance(timeouts, dict):
            timeouts = {}

        # Extract trace_outputs - prefer the explicit trace_outputs key, fall
        # back to the parsed outputs dict (which may contain final_answer etc.)
        explicit_trace_outputs = trace_dict.get("trace_outputs", trace_dict.get("traceOutputs", {}))
        if isinstance(explicit_trace_outputs, dict) and explicit_trace_outputs:
            trace_outputs = explicit_trace_outputs
        elif isinstance(outputs, dict) and outputs:
            trace_outputs = outputs
        else:
            trace_outputs = {}
            if not isinstance(trace_outputs, dict):
                trace_outputs = {}

        # Use trace_metadata as the main metadata source
        metadata = trace_metadata if trace_metadata else trace_dict.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}

        # Calculate token_cost from spans
        token_cost = 0
        for span in trajectory_spans:
            prompt_tokens = span.attributes.get("gen_ai.usage.prompt_tokens", 0)
            completion_tokens = span.attributes.get("gen_ai.usage.completion_tokens", 0)
            if isinstance(prompt_tokens, (int, float)):
                token_cost += int(prompt_tokens)
            if isinstance(completion_tokens, (int, float)):
                token_cost += int(completion_tokens)

        # Calculate latency_s from execution_duration_ms in info or span durations
        latency_s = 0.0
        execution_duration_ms = info.get("execution_duration_ms", 0)
        if isinstance(execution_duration_ms, (int, float)) and execution_duration_ms > 0:
            latency_s = float(execution_duration_ms) / 1000.0
        elif trajectory_spans:
            starts = [s.start for s in trajectory_spans if s.start >= 0]
            ends = [s.end for s in trajectory_spans if s.end >= 0]
            if starts and ends:
                min_start = min(starts)
                max_end = max(ends)
                if max_end > min_start:
                    latency_s = max_end - min_start

        if latency_s == 0.0:
            # Fallback to trace-level duration if available
            duration = trace_dict.get("duration", trace_dict.get("duration_ms", 0))
            if isinstance(duration, (int, float)):
                latency_s = float(duration) / 1000.0 if duration > 1000 else float(duration)

        # Extract parent_span_id
        parent_span_id = trace_dict.get("parent_span_id", trace_dict.get("parentSpanId"))

        return cls(
            trace_id=trace_id,
            route=route,
            user_request=user_request,
            core_memory=core_memory,
            history=history,
            active_skills=active_skills,
            context=context,
            trajectory_spans=trajectory_spans,
            final_answer=final_answer,
            timeouts=timeouts,
            trace_outputs=trace_outputs,
            metadata=metadata,
            token_cost=token_cost,
            latency_s=latency_s,
            parent_span_id=parent_span_id,
        )
