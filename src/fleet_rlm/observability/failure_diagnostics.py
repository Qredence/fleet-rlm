"""Safe structured diagnostics for failures at public transport boundaries."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from dspy.utils.exceptions import AdapterParseError

from fleet_rlm.daytona.errors import (
    DaytonaAdapterError,
    classify_provider_error,
    provider_status_category,
    sanitize_provider_message,
)
from fleet_rlm.rlm.child_runtime import ChildRuntimeAuthorizationError, ChildRuntimeCleanupError


@dataclass(frozen=True, slots=True)
class FailureDiagnostic:
    cause_type: str
    provider_status_category: str
    message: str


def normalize_turn_failure(exc: BaseException) -> FailureDiagnostic:
    """Describe a Turn preparation failure without exposing exception text."""
    cause = _diagnostic_cause(exc)
    if isinstance(cause, AdapterParseError):
        adapter = str(getattr(cause, "adapter_name", "") or "adapter")
        return FailureDiagnostic("adapter_parse_error", "none", f"LM response unparseable by {adapter}")
    if not isinstance(cause, DaytonaAdapterError):
        return FailureDiagnostic("unknown", "none", type(cause).__name__)
    cause_type = classify_provider_error(cause)
    message = (
        sanitize_provider_message(cause.message)
        if cause_type != "unknown"
        else (cause.cause_type or type(cause).__name__)
    )
    return FailureDiagnostic(cause_type, provider_status_category(cause.status_code), message)


def trace_failure_category(exc: BaseException) -> str:
    """
    Classify an exception into a bounded failure category for internal MLflow metadata.

    Parameters:
        exc (BaseException): The failure to classify.

    Returns:
        str: A failure category such as ``unauthorized``, ``cleanup_failed``, ``timeout``,
            ``cancelled``, or the normalized diagnostic cause type.
    """
    if isinstance(exc, ChildRuntimeAuthorizationError):
        return "unauthorized"
    if isinstance(exc, ChildRuntimeCleanupError):
        return "cleanup_failed"
    status = getattr(exc, "status", None)
    if status in {"timeout", "cancelled"}:
        return str(status)
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return "timeout"
    if isinstance(exc, asyncio.CancelledError):
        return "cancelled"
    return normalize_turn_failure(exc).cause_type


def _diagnostic_cause(exc: BaseException) -> BaseException:
    current = exc
    seen: set[int] = set()
    while id(current) not in seen:
        seen.add(id(current))
        nested = current.__cause__ or current.__context__
        if nested is None:
            break
        if isinstance(nested, (DaytonaAdapterError, AdapterParseError)):
            return nested
        current = nested
    return current
