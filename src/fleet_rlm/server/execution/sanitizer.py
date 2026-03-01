"""Payload sanitization helpers for execution event streaming."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from fleet_rlm.execution_limits import (
    DEFAULT_MAX_TEXT_CHARS,
    execution_max_collection_items,
    execution_max_recursion_depth,
    execution_max_text_chars as _execution_max_text_chars,
)

_SENSITIVE_KEYWORDS = (
    "token",
    "secret",
    "api_key",
    "password",
    "authorization",
)


@dataclass(frozen=True, slots=True)
class _SanitizeLimits:
    max_text_chars: int
    max_collection_items: int
    max_recursion_depth: int


def _max_text_chars() -> int:
    return _execution_max_text_chars()


def _max_collection_items() -> int:
    return execution_max_collection_items()


def _max_recursion_depth() -> int:
    return execution_max_recursion_depth()


def _sanitize_limits() -> _SanitizeLimits:
    return _SanitizeLimits(
        max_text_chars=_max_text_chars(),
        max_collection_items=_max_collection_items(),
        max_recursion_depth=_max_recursion_depth(),
    )


def _truncate_text(text: str, *, max_chars: int | None = None) -> str:
    limit = _max_text_chars() if max_chars is None else max_chars
    if limit <= 0:
        limit = DEFAULT_MAX_TEXT_CHARS
    if len(text) <= limit:
        return text
    return f"{text[:limit]}...[truncated]"


def _looks_sensitive_key(key: str) -> bool:
    lowered = key.strip().lower()
    return any(token in lowered for token in _SENSITIVE_KEYWORDS)


def sanitize_event_payload(value: Any, *, depth: int = 0) -> Any:
    """Truncate large payloads and redact sensitive values for websocket emission."""
    return _sanitize_event_payload(value, depth=depth, limits=_sanitize_limits())


def _sanitize_event_payload(value: Any, *, depth: int, limits: _SanitizeLimits) -> Any:
    if depth >= limits.max_recursion_depth:
        return "<max-depth>"

    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _truncate_text(value, max_chars=limits.max_text_chars)
    if isinstance(value, bytes):
        return f"<bytes:{len(value)}>"
    if isinstance(value, dict):
        items = list(value.items())
        sanitized: dict[str, Any] = {}
        max_items = limits.max_collection_items
        for index, (key, raw) in enumerate(items):
            if index >= max_items:
                sanitized["__truncated__"] = len(items) - max_items
                break
            key_str = str(key)
            if _looks_sensitive_key(key_str):
                sanitized[key_str] = "<redacted>"
            else:
                sanitized[key_str] = _sanitize_event_payload(
                    raw, depth=depth + 1, limits=limits
                )
        return sanitized
    if isinstance(value, (list, tuple, set)):
        sequence = list(value)
        max_items = limits.max_collection_items
        limited = [
            _sanitize_event_payload(item, depth=depth + 1, limits=limits)
            for item in sequence[:max_items]
        ]
        if len(sequence) > max_items:
            limited.append(f"<truncated:{len(sequence) - max_items}>")
        return limited
    return _truncate_text(str(value), max_chars=limits.max_text_chars)


def summarize_code_for_event(code: str) -> dict[str, str]:
    """Build stable code metadata for REPL execution events."""
    normalized = code or ""
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
    compact = " ".join(normalized.split())
    return {"code_hash": digest, "code_preview": _truncate_text(compact)}
