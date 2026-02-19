"""Sanitization helpers for analytics payloads."""

from __future__ import annotations

import re
from typing import Any


_SENSITIVE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"sk-[A-Za-z0-9_-]{8,}"), "sk-***REDACTED***"),
    (
        re.compile(r"(Authorization\s*:\s*Bearer\s+)[^\s]+", re.IGNORECASE),
        r"\1***REDACTED***",
    ),
    (
        re.compile(
            r"((?:api[_-]?key|token|secret|password)\s*[=:]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,}\]]+)",
            re.IGNORECASE,
        ),
)


def redact_sensitive(text: str) -> str:
    """Replace API keys and other sensitive tokens with redaction markers."""
    redacted = text
    for pattern, replacement in _SENSITIVE_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def truncate_text(text: str, max_chars: int) -> str:
    """Truncate text with a deterministic suffix describing omitted length."""
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    omitted = len(text) - max_chars
    return f"{text[:max_chars]}...[truncated, {omitted} more chars]"


def to_safe_text(value: Any) -> str:
    """Coerce arbitrary values to a stable string for analytics emission."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return str(value)
    except Exception:
        return "<unprintable>"


def sanitize_text(text: str, *, redact: bool, truncation_chars: int) -> str:
    """Apply redaction and truncation in the correct order."""
    candidate = redact_sensitive(text) if redact else text
    return truncate_text(candidate, truncation_chars)
