"""Pure duration, output-size, token, and fallback aggregation for spans."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

from fleet_rlm.observability.token_usage import TokenUsage, int_or_none, token_usage_from_mapping

from .classifier import fallback_reason, optional_string, span_attributes, span_type


@dataclass(frozen=True, slots=True)
class PerformanceSpanSummary:
    span_id: str
    name: str
    duration_ms: int | None
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    output_chars: int | None


@dataclass(frozen=True, slots=True)
class PerformanceSummary:
    total_duration_ms: int | None
    llm_duration_ms: int
    repl_duration_ms: int
    tool_duration_ms: int
    root_overhead_ms: int | None
    input_tokens: int
    output_tokens: int
    total_tokens: int
    token_total_mismatch: bool
    adapter_fallback_count: int
    parse_error_count: int
    selected_skills: list[str]
    rlm_action_max_tokens: int | None
    rlm_max_output_chars: int | None
    slowest_llm_span: PerformanceSpanSummary | None
    largest_output_span: PerformanceSpanSummary | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _jsonish_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text or text[0] not in '[{"':
        return value
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return value


def span_duration_ms(span: dict[str, Any]) -> int | None:
    start = int_or_none(span.get("start_time_unix_nano"))
    end = int_or_none(span.get("end_time_unix_nano"))
    if start is None or end is None or end <= start:
        return None
    return int((end - start) / 1_000_000)


def span_output(span: dict[str, Any]) -> Any:
    if "outputs" in span:
        return _jsonish_value(span.get("outputs"))
    return _jsonish_value(span_attributes(span).get("mlflow.spanOutputs"))


def output_chars(span: dict[str, Any]) -> int | None:
    output = span_output(span)
    if output is None:
        return None
    if isinstance(output, str):
        return len(output)
    try:
        return len(json.dumps(output, ensure_ascii=True, sort_keys=True))
    except (TypeError, ValueError):
        return len(str(output))


def span_token_usage(span: dict[str, Any]) -> TokenUsage:
    attributes = span_attributes(span)
    payload = dict(attributes)
    if "mlflow.chat.tokenUsage" in attributes:
        payload["mlflow.chat.tokenUsage"] = _jsonish_value(attributes["mlflow.chat.tokenUsage"])
    if "mlflow.chat.tokenUsageJson" in attributes:
        payload["mlflow.chat.tokenUsageJson"] = _jsonish_value(attributes["mlflow.chat.tokenUsageJson"])
    return token_usage_from_mapping(payload)


def _span_summary(span: dict[str, Any]) -> PerformanceSpanSummary:
    usage = span_token_usage(span)
    return PerformanceSpanSummary(
        span_id=str(span.get("span_id") or ""),
        name=str(span.get("name") or "unknown"),
        duration_ms=span_duration_ms(span),
        input_tokens=usage.input_tokens or None,
        output_tokens=usage.output_tokens or None,
        total_tokens=usage.total_tokens or None,
        output_chars=output_chars(span),
    )


def _csv_values(value: Any) -> list[str]:
    text = optional_string(value)
    if text is None:
        return []
    return [item.strip() for item in text.split(",") if item.strip()]


def summarize_spans(spans: list[dict[str, Any]]) -> PerformanceSummary:
    """Return a deterministic aggregate for a normalized list of spans."""
    llm_duration = repl_duration = tool_duration = 0
    usage = TokenUsage()
    parse_error_count = fallback_count = 0
    selected_skills: list[str] = []
    action_max_tokens: int | None = None
    max_output_chars: int | None = None
    root_duration: int | None = None
    slowest_llm: dict[str, Any] | None = None
    largest_output: dict[str, Any] | None = None

    for span in spans:
        duration = span_duration_ms(span) or 0
        resolved_type = (span_type(span) or "").upper()
        name = str(span.get("name") or "")
        attributes = span_attributes(span)

        if span.get("parent_span_id") is None and duration:
            root_duration = duration if root_duration is None else max(root_duration, duration)
        if resolved_type in {"LLM", "CHAT_MODEL"} or name == "LM.__call__":
            llm_duration += duration
            if slowest_llm is None or duration > (span_duration_ms(slowest_llm) or 0):
                slowest_llm = span
        elif resolved_type == "TOOL" and "repl" in name.lower():
            repl_duration += duration
        elif resolved_type == "TOOL":
            tool_duration += duration

        usage = usage.add(span_token_usage(span))
        if largest_output is None or (output_chars(span) or 0) > (output_chars(largest_output) or 0):
            largest_output = span

        reason = fallback_reason(span)
        if reason is not None:
            if "parse" in reason:
                parse_error_count += 1
            if "fallback" in reason or "retry" in reason or reason == "adapter_parse_error":
                fallback_count += 1

        for skill in _csv_values(attributes.get("fleet_rlm.selected_skills")):
            if skill not in selected_skills:
                selected_skills.append(skill)
        action_max_tokens = action_max_tokens or int_or_none(attributes.get("fleet_rlm.rlm_action_max_tokens"))
        max_output_chars = max_output_chars or int_or_none(attributes.get("fleet_rlm.rlm_max_output_chars"))

    known_duration = llm_duration + repl_duration + tool_duration
    root_overhead = max(0, root_duration - known_duration) if root_duration is not None else None
    expected_total = usage.input_tokens + usage.output_tokens
    return PerformanceSummary(
        total_duration_ms=root_duration,
        llm_duration_ms=llm_duration,
        repl_duration_ms=repl_duration,
        tool_duration_ms=tool_duration,
        root_overhead_ms=root_overhead,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        total_tokens=usage.total_tokens or expected_total,
        token_total_mismatch=bool(usage.total_tokens and usage.total_tokens != expected_total),
        adapter_fallback_count=fallback_count,
        parse_error_count=parse_error_count,
        selected_skills=selected_skills,
        rlm_action_max_tokens=action_max_tokens,
        rlm_max_output_chars=max_output_chars,
        slowest_llm_span=_span_summary(slowest_llm) if slowest_llm is not None else None,
        largest_output_span=_span_summary(largest_output) if largest_output is not None else None,
    )


__all__ = [
    "PerformanceSpanSummary",
    "PerformanceSummary",
    "output_chars",
    "span_duration_ms",
    "span_output",
    "span_token_usage",
    "summarize_spans",
]
