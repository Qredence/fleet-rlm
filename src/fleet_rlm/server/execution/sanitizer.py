"""Payload sanitization helpers for execution event streaming."""

from __future__ import annotations

import hashlib
import os
from typing import Any

_SENSITIVE_KEYWORDS = (
    "token",
    "secret",
    "api_key",
    "password",
    "authorization",
)
_DEFAULT_MAX_TEXT_CHARS = 65536
_DEFAULT_MAX_COLLECTION_ITEMS = 500
_DEFAULT_MAX_RECURSION_DEPTH = 12
_MAX_ENV_LIMIT = 1_000_000


def _env_positive_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        parsed = int(raw)
    except (TypeError, ValueError):
        return default
    if parsed <= 0:
        return default
    return min(parsed, _MAX_ENV_LIMIT)


def _max_text_chars() -> int:
    return _env_positive_int("WS_EXECUTION_MAX_TEXT_CHARS", _DEFAULT_MAX_TEXT_CHARS)


def _max_collection_items() -> int:
    return _env_positive_int(
        "WS_EXECUTION_MAX_COLLECTION_ITEMS", _DEFAULT_MAX_COLLECTION_ITEMS
    )


def _max_recursion_depth() -> int:
    return _env_positive_int(
        "WS_EXECUTION_MAX_RECURSION_DEPTH", _DEFAULT_MAX_RECURSION_DEPTH
    )


def _truncate_text(text: str, *, max_chars: int | None = None) -> str:
    limit = _max_text_chars() if max_chars is None else max_chars
    if limit <= 0:
        limit = _DEFAULT_MAX_TEXT_CHARS
    if len(text) <= limit:
        return text
    return f"{text[:limit]}...[truncated]"


def _looks_sensitive_key(key: str) -> bool:
    lowered = key.strip().lower()
    return any(token in lowered for token in _SENSITIVE_KEYWORDS)


def sanitize_event_payload(value: Any, *, depth: int = 0) -> Any:
    """Truncate large payloads and redact sensitive values for websocket emission."""
    if depth >= _max_recursion_depth():
        return "<max-depth>"

    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _truncate_text(value)
    if isinstance(value, bytes):
        return f"<bytes:{len(value)}>"
    if isinstance(value, dict):
        items = list(value.items())
        sanitized: dict[str, Any] = {}
        max_items = _max_collection_items()
        for index, (key, raw) in enumerate(items):
            if index >= max_items:
                sanitized["__truncated__"] = len(items) - max_items
                break
            key_str = str(key)
            if _looks_sensitive_key(key_str):
                sanitized[key_str] = "<redacted>"
            else:
                sanitized[key_str] = sanitize_event_payload(raw, depth=depth + 1)
        return sanitized
    if isinstance(value, (list, tuple, set)):
        sequence = list(value)
        max_items = _max_collection_items()
        limited = [
            sanitize_event_payload(item, depth=depth + 1)
            for item in sequence[:max_items]
        ]
        if len(sequence) > max_items:
            limited.append(f"<truncated:{len(sequence) - max_items}>")
        return limited
    return _truncate_text(str(value))


def summarize_code_for_event(code: str) -> dict[str, str]:
    """Build stable code metadata for REPL execution events."""
    normalized = code or ""
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
    compact = " ".join(normalized.split())
    return {"code_hash": digest, "code_preview": _truncate_text(compact)}
