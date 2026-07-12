"""Public-safe error messages for RuntimeEvent payloads and HTTP details."""

from __future__ import annotations

import re

# Secrets / credentials
_SECRETISH = re.compile(
    r"(?i)("
    r"api[_-]?key|authorization|bearer\s+\S+|sk-[a-z0-9_-]+|"
    r"password|secret|token|credential|private[_-]?key"
    r")[=:\s]+\S+"
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
_PROMPTISH = re.compile(
    r"(?i)(system prompt|you are a helpful|<<<instructions>>>|BEGIN SYSTEM)"
)


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

    cleaned = _SECRETISH.sub("[redacted]", raw)
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
    """Light redaction for optional public text fields (not full assistant answers)."""
    cleaned = _SECRETISH.sub("[redacted]", text)
    cleaned = _DSNISH.sub("[redacted-dsn]", cleaned)
    if len(cleaned) > max_len:
        cleaned = cleaned[: max_len - 3] + "..."
    return cleaned
