"""Public-safe error messages for RuntimeEvent payloads."""

from __future__ import annotations

import re

_SECRETISH = re.compile(
    r"(?i)(api[_-]?key|authorization|bearer\s+\S+|sk-[a-z0-9]+|password|secret|token)=?\s*\S+"
)
_PATHISH = re.compile(r"(/home/\S+|/Users/\S+|[A-Za-z]:\\[^\s]+)")


def sanitize_public_error(exc: BaseException) -> str:
    """Return a concise client-safe message; never rethrow raw provider text."""
    raw = str(exc).strip() or type(exc).__name__
    cleaned = _SECRETISH.sub("[redacted]", raw)
    cleaned = _PATHISH.sub("[path]", cleaned)
    # Cap length so stack-like dumps never fill SSE frames.
    if len(cleaned) > 240:
        cleaned = cleaned[:237] + "..."
    # Prefer generic wording when the message still looks like an internal exception dump.
    if "Traceback" in cleaned or "\n  File " in cleaned:
        return "Turn failed"
    return cleaned or "Turn failed"
