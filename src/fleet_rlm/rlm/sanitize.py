"""Public-safe error messages for RuntimeEvent payloads and HTTP details."""

from __future__ import annotations

import re
from typing import Any

# Secrets / credentials
_SECRETISH = re.compile(
    r"(?i)("
    r"api[_-]?key|authorization|bearer\s+\S+|sk-[a-z0-9_-]+|"
    r"password|secret|token|credential|private[_-]?key"
    r")[=:\s]+\S+"
)
_TOKENISH = re.compile(r"(?i)\b(?:bearer\s+[a-z0-9._~+/=-]+|sk-[a-z0-9_-]{6,})")
_SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "credential",
        "credentials",
        "password",
        "private_key",
        "secret",
        "token",
    }
)
# Connection strings / DSNs
_DSNISH = re.compile(
    r"(?i)("
    r"(?:postgres|postgresql|mysql|mongodb|redis|amqp)(?:\+\w+)?://"
    r"[^\s\"']+|"
    r"jdbc:[^\s\"']+"
    r")"
)
# Host paths
_PATHISH = re.compile(
    r"(?i)("
    r"/home/\S+|/Users/\S+|/var/\S+|/tmp/\S+|"
    r"[A-Za-z]:\\[^\s]+|"
    r"/home/daytona/\S+"
    r")"
)
# Stack / exception noise
_STACKISH = re.compile(r"(?i)(traceback \(most recent call last\)|File \"[^\"]+\", line \d+)")
# Prompt-ish dumps
_PROMPTISH = re.compile(r"(?i)(system prompt|you are a helpful|<<<instructions>>>|BEGIN SYSTEM)")


def sanitize_public_error(exc: BaseException | str) -> str:
    """Return a concise client-safe message; never rethrow raw provider text."""
    if isinstance(exc, BaseException):
        # Prefer typed public messages when available
        public = getattr(exc, "public_message", None)
        status = getattr(exc, "status", None)
        if isinstance(public, str) and public.strip() and status:
            return public.strip()
        raw = str(exc).strip() or type(exc).__name__
    else:
        raw = str(exc).strip() or "Turn failed"

    cleaned = _TOKENISH.sub("[redacted]", raw)
    cleaned = _SECRETISH.sub("[redacted]", cleaned)
    cleaned = _DSNISH.sub("[redacted-dsn]", cleaned)
    cleaned = _PATHISH.sub("[path]", cleaned)
    cleaned = _PROMPTISH.sub("[redacted-prompt]", cleaned)
    if _STACKISH.search(cleaned) or "Traceback" in cleaned or "\n  File " in cleaned:
        return "Turn failed"
    # Cap length so stack-like dumps never fill SSE frames.
    if len(cleaned) > 240:
        cleaned = cleaned[:237] + "..."
    # Provider-ish type names alone are ok; long internal dumps already capped
    return cleaned or "Turn failed"


def sanitize_public_text(text: str, *, max_len: int = 10_000) -> str:
    """Bound and redact model-authored text intended for public detail or answers."""
    cleaned = _TOKENISH.sub("[redacted]", text)
    cleaned = _SECRETISH.sub("[redacted]", cleaned)
    cleaned = _DSNISH.sub("[redacted-dsn]", cleaned)
    cleaned = _PATHISH.sub("[path]", cleaned)
    cleaned = _PROMPTISH.sub("[redacted-prompt]", cleaned)
    if len(cleaned) > max_len:
        cleaned = cleaned[: max_len - 3] + "..."
    return cleaned


def sanitize_public_value(value: Any, *, max_len: int = 2_000, depth: int = 0) -> Any:
    """Recursively bound and redact JSON-like public detail values."""
    if depth >= 8:
        return "[truncated]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return sanitize_public_text(value, max_len=max_len)
    if isinstance(value, dict):
        return {
            str(key)[:128]: (
                "[redacted]"
                if str(key).strip().lower().replace("-", "_") in _SENSITIVE_KEYS
                else sanitize_public_value(item, max_len=max_len, depth=depth + 1)
            )
            for key, item in list(value.items())[:50]
        }
    if isinstance(value, (list, tuple)):
        return [sanitize_public_value(item, max_len=max_len, depth=depth + 1) for item in list(value)[:50]]
    return sanitize_public_text(str(value), max_len=max_len)
