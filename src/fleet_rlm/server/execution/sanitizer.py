"""Payload sanitization helpers for execution event streaming."""

from __future__ import annotations

import hashlib
from typing import Any

_SENSITIVE_KEYWORDS = (
    "token",
    "secret",
    "api_key",
    "password",
    "authorization",
)
_MAX_TEXT_CHARS = 2048
_MAX_COLLECTION_ITEMS = 50
_MAX_RECURSION_DEPTH = 6


def _truncate_text(text: str, *, max_chars: int = _MAX_TEXT_CHARS) -> str:
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}...[truncated]"


def _looks_sensitive_key(key: str) -> bool:
    lowered = key.strip().lower()
    return any(token in lowered for token in _SENSITIVE_KEYWORDS)


def sanitize_event_payload(value: Any, *, depth: int = 0) -> Any:
    """Truncate large payloads and redact sensitive values for websocket emission."""
    if depth >= _MAX_RECURSION_DEPTH:
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
        for index, (key, raw) in enumerate(items):
            if index >= _MAX_COLLECTION_ITEMS:
                sanitized["__truncated__"] = len(items) - _MAX_COLLECTION_ITEMS
                break
            key_str = str(key)
            if _looks_sensitive_key(key_str):
                sanitized[key_str] = "<redacted>"
            else:
                sanitized[key_str] = sanitize_event_payload(raw, depth=depth + 1)
        return sanitized
    if isinstance(value, (list, tuple, set)):
        sequence = list(value)
        limited = [
            sanitize_event_payload(item, depth=depth + 1)
            for item in sequence[:_MAX_COLLECTION_ITEMS]
        ]
        if len(sequence) > _MAX_COLLECTION_ITEMS:
            limited.append(f"<truncated:{len(sequence) - _MAX_COLLECTION_ITEMS}>")
        return limited
    return _truncate_text(str(value))


def summarize_code_for_event(code: str) -> dict[str, str]:
    """Build stable code metadata for REPL execution events."""
    normalized = code or ""
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
    compact = " ".join(normalized.split())
    return {"code_hash": digest, "code_preview": _truncate_text(compact, max_chars=240)}
