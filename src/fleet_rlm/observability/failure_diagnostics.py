"""Safe structured diagnostics for failures at public transport boundaries."""

from __future__ import annotations

from dataclasses import dataclass

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
    if not isinstance(cause, DaytonaAdapterError):
        return FailureDiagnostic("unknown", "none", type(cause).__name__)
    cause_type = classify_provider_error(cause)
    message = (
        sanitize_provider_message(cause.message)
        if cause_type != "unknown"
        else (cause.cause_type or type(cause).__name__)
    )
    return FailureDiagnostic(cause_type, provider_status_category(cause.status_code), message)


def _diagnostic_cause(exc: BaseException) -> BaseException:
    current = exc
    seen: set[int] = set()
    while id(current) not in seen:
        seen.add(id(current))
        nested = current.__cause__ or current.__context__
        if nested is None:
            break
        if isinstance(nested, DaytonaAdapterError):
            return nested
        current = nested
    return current
