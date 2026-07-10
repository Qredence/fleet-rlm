"""Normalize external MLflow payloads before trace/debug client projection."""

from __future__ import annotations

from typing import Any

from fleet_rlm.observability.redaction import redact_value


def sanitize_trace_info(info: dict[str, Any]) -> dict[str, Any]:
    """Return a safe copy of trace metadata from an external provider."""
    return redact_value(info)


def sanitize_trace_span(span: dict[str, Any]) -> dict[str, Any]:
    """Return a safe copy of one provider span for debug classification."""
    return redact_value(span)


def sanitize_trace_spans(spans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [sanitize_trace_span(span) for span in spans]


__all__ = ["sanitize_trace_info", "sanitize_trace_span", "sanitize_trace_spans"]
