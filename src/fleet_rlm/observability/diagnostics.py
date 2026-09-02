"""Safe structured diagnostics for failures at public transport boundaries."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Iterator
from dataclasses import dataclass

from dspy.utils.exceptions import AdapterParseError

from fleet_rlm.daytona.errors import (
    DaytonaAdapterError,
    classify_provider_error,
    provider_status_category,
    provider_status_code,
    sanitize_provider_message,
)


@dataclass(frozen=True, slots=True)
class FailureDiagnostic:
    cause_type: str
    provider_status_category: str
    message: str


_HTTP_STATUS_TEXT = re.compile(r"\b(4\d{2}|5\d{2})\b")
_HTTP_STATUS_VALUE = re.compile(r"^(4\d{2}|5\d{2})$")
_PROVIDER_EXCEPTION_TEXT = re.compile(r"(?i)(litellm|dspy|lmunsupported|provider|endpoint|chat[ -]?completion|openai/)")


def _safe_provider_status_code(exc: object) -> int | None:
    """Read provider status metadata without allowing a hostile property to escape."""
    try:
        return provider_status_code(exc)
    except Exception:
        return None


def _status_from_metadata(exc: object) -> int | None:
    """Read bounded status metadata exposed by LiteLLM/DSPy wrappers."""
    for name in ("status_code", "status", "provider_code", "code"):
        try:
            value = getattr(exc, name, None)
        except Exception:
            continue
        if isinstance(value, int) and not isinstance(value, bool) and 400 <= value <= 599:
            return value
        if isinstance(value, str):
            match = _HTTP_STATUS_VALUE.fullmatch(value.strip())
            if match is not None:
                return int(match.group(1))
    return None


def _status_from_text(exc: object) -> int | None:
    """Extract one bounded HTTP status from already-sanitized exception text."""
    try:
        raw = getattr(exc, "message", None)
        text = str(raw if raw not in (None, "") else exc)
    except Exception:
        return None
    match = _HTTP_STATUS_TEXT.search(sanitize_provider_message(text))
    return int(match.group(1)) if match is not None else None


def _is_provider_shaped(exc: object) -> bool:
    """Return whether an exception resembles a provider/LLM failure."""
    try:
        exception_type = type(exc)
        type_name = exception_type.__name__.lower()
        module_name = exception_type.__module__.lower()
        raw_message = getattr(exc, "message", None)
        message = str(raw_message if raw_message not in (None, "") else exc)
    except Exception:
        return False
    return (
        type_name.startswith("lm")
        or "litellm" in module_name
        or "dspy" in module_name
        or _PROVIDER_EXCEPTION_TEXT.search(type_name) is not None
        or _PROVIDER_EXCEPTION_TEXT.search(message) is not None
    )


def _failure_status(exc: BaseException) -> int | None:
    """Find structured or text-only provider status across an exception chain."""
    first_status: int | None = None
    items = tuple(walk_cause_chain(exc))
    if not any(_is_provider_shaped(item) for item in items):
        return None
    for item in items:
        status = _status_from_metadata(item) or _safe_provider_status_code(item)
        if status is None:
            status = _status_from_text(item)
        if status is None:
            continue
        # A nested upstream 404 is the actionable diagnosis even when an
        # outer DSPy/LiteLLM wrapper exposes a less-specific 4xx status.
        if status == 404:
            return status
        if first_status is None:
            first_status = status
    return first_status


def normalize_turn_failure(exc: BaseException) -> FailureDiagnostic:
    """Describe a Turn preparation failure without exposing exception text."""
    cause = _diagnostic_cause(exc)
    if isinstance(cause, AdapterParseError):
        adapter = str(getattr(cause, "adapter_name", "") or "adapter")
        return FailureDiagnostic("adapter_parse_error", "none", f"LM response unparseable by {adapter}")
    if not isinstance(cause, DaytonaAdapterError):
        status = _failure_status(exc)
        if status == 404:
            return FailureDiagnostic("provider_not_found", "4xx", "provider endpoint not found")
        return FailureDiagnostic("unknown", provider_status_category(status), type(cause).__name__)
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
