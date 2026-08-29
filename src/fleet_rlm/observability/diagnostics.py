"""Safe structured diagnostics for failures at public transport boundaries."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from dataclasses import dataclass

from dspy.utils.exceptions import AdapterParseError

from fleet_rlm.daytona.errors import (
    DaytonaAdapterError,
    classify_provider_error,
    provider_status_category,
    sanitize_provider_message,
)


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
    from fleet_rlm.rlm.recursion import ChildRuntimeAuthorizationError, ChildRuntimeCleanupError

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


def walk_cause_chain(exc: BaseException) -> Iterator[BaseException]:
    """Yield ``exc`` then each nested ``__cause__``/``__context__`` with a cycle guard.

    Attribute access stays guarded so failure classification can never raise
    while inspecting another failure.
    """
    current: BaseException | None = exc
    seen: set[int] = set()
    try:
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            yield current
            current = current.__cause__ or current.__context__
    except Exception:
        return


def _diagnostic_cause(exc: BaseException) -> BaseException:
    current = exc
    for item in walk_cause_chain(exc):
        if item is not current and isinstance(item, (DaytonaAdapterError, AdapterParseError)):
            return item
        current = item
    return current
