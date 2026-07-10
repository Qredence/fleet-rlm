"""Stable public details for trace-feedback failures."""

from __future__ import annotations


def trace_feedback_error_detail(operation: str) -> str:
    """Return a client-safe error that never includes provider exception text."""
    messages = {
        "resolve": "Unable to resolve the requested MLflow trace.",
        "log": "Unable to record MLflow trace feedback.",
        "persist": "Unable to persist trace feedback.",
    }
    return messages.get(operation, "Trace feedback is temporarily unavailable.")


__all__ = ["trace_feedback_error_detail"]
