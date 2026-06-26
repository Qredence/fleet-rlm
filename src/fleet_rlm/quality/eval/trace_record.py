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
            trace_dict: Dictionary representation of an MLflow trace with keys
                like trace_id, spans, inputs, outputs, etc.

        Returns:
            A normalized TraceRecord with all fields populated.

        Raises:
            KeyError: If required fields are missing from the trace.
            TypeError: If trace_dict is not a dictionary.
        """
        if not isinstance(trace_dict, dict):
            msg = f"trace_dict must be a dict, got {type(trace_dict).__name__}"
            raise TypeError(msg)

        # Extract trace_id
        trace_id = str(trace_dict.get("trace_id", trace_dict.get("traceId", "")))

        # Extract spans
        spans_data = trace_dict.get("spans", [])
        trajectory_spans = []
        for span in spans_data:
            if isinstance(span, dict):
                trajectory_spans.append(
                    TrajectorySpan(
                        name=str(span.get("name", "")),
                        kind=str(span.get("kind", span.get("spanKind", "UNKNOWN"))),
                        start=float(span.get("start_time", span.get("startTime", 0.0))),
                        end=float(span.get("end_time", span.get("endTime", 0.0))),
                        tool_name=span.get("tool_name", span.get("attributes", {}).get("gen_ai.tool.name")),
                        tool_input=span.get("tool_input", span.get("inputs")),
                        tool_output=span.get("tool_output", span.get("outputs")),
                        attributes=span.get("attributes", {}),
                    )
                )

        # Extract inputs (user_request, core_memory, history, active_skills, context)
        inputs = trace_dict.get("inputs", {})
        if not isinstance(inputs, dict):
            inputs = {}

        user_request = str(inputs.get("user_request", inputs.get("question", "")))
        core_memory = str(inputs.get("core_memory", inputs.get("memory", "")))
        history = inputs.get("history", [])
        if not isinstance(history, list):
            history = []
        active_skills = inputs.get("active_skills", inputs.get("skills", []))
        if not isinstance(active_skills, list):
            active_skills = []
        context = str(inputs.get("context", ""))

        # Extract outputs (final_answer)
        outputs = trace_dict.get("outputs", {})
        if isinstance(outputs, dict):
            final_answer = str(outputs.get("final_answer", outputs.get("answer", outputs.get("response", ""))))
        else:
            final_answer = str(outputs) if outputs else ""

        # Extract route
        route = str(trace_dict.get("route", trace_dict.get("execution_mode", "")))

        # Extract timeouts
        timeouts = trace_dict.get("timeouts", {})
        if not isinstance(timeouts, dict):
            timeouts = {}

        # Extract trace_outputs
        trace_outputs = trace_dict.get("trace_outputs", trace_dict.get("traceOutputs", {}))
        if not isinstance(trace_outputs, dict):
            trace_outputs = {}

        # Extract metadata
        metadata = trace_dict.get("metadata", {})
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

        # Calculate latency_s from trace duration or span durations
        latency_s = 0.0
        if trajectory_spans:
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
