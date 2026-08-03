"""Public-safe error redaction and declared-output safety validation."""

from __future__ import annotations

import re
from collections.abc import Mapping
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
    }
)


def _is_sensitive_key(key: object) -> bool:
    """Recognize exact fields plus common namespaced credential fields."""
    normalized = str(key).strip().lower().replace("-", "_")
    return normalized in _SENSITIVE_KEYS or normalized.endswith(
        ("_api_key", "_authorization", "_credential", "_password", "_private_key", "_secret", "_token")
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

# Declared model outputs are never rewritten. These patterns therefore live apart
# from the error/tool-detail redactors above and only identify concrete disclosure
# shapes. Bare credential names and security terminology are deliberately safe.
_DECLARED_SECRET_ASSIGNMENT = re.compile(
    r"(?i)(?<![a-z0-9])(?:[a-z0-9]+[_-])*"
    r"(?:api[_-]?key|authorization|password|secret|token|credential|private[_-]?key)\b"
    r"\s*(?:=|:)\s*(?P<value>\"[^\"\r\n]+\"|'[^'\r\n]+'|[^\s,;}\]]+)"
)
_DECLARED_BEARER = re.compile(r"(?i)\bbearer\s+(?P<value>[a-z0-9._~+/=-]+)")
_DECLARED_PROVIDER_TOKEN = re.compile(
    r"(?i)\b(?:"
    r"sk-(?:ant-)?[a-z0-9_-]{6,}|"
    r"AIza[a-z0-9_-]{20,}|"
    r"gh[pousr]_[a-z0-9]{20,}|"
    r"xox[baprs]-[a-z0-9-]{10,}"
    r")\b"
)
_DECLARED_PRIVATE_PATH = re.compile(
    r"(?i)(?:"
    r"/(?:Users|home|root|var|tmp|private)/[^\s\"'`<>]+|"
    r"[A-Za-z]:\\[^\s\"'`<>]+"
    r")"
)
_DECLARED_STACK_DUMP = re.compile(
    r"(?i)(?:"
    r"traceback \(most recent call last\)|"
    r"(?:^|\n)\s*File \"[^\"]+\", line \d+|"
    r"(?:^|\n)\s*at\s+\S+\s+\([^\n]+:\d+(?::\d+)?\)"
    r")"
)
_DECLARED_PROMPT_DUMP = re.compile(
    r"(?is)(?:"
    r"<<<instructions>>>|BEGIN SYSTEM|<\|im_start\|>\s*system|"
    r"^\s*#{1,6}\s*system prompt\s*$|"
    r"\bsystem prompt\s*:\s*(?:\r?\n|.{0,20}\byou\s+are\b)"
    r")",
    re.MULTILINE,
)

_DECLARED_SAFE_PLACEHOLDERS = frozenset(
    {
        "",
        "***",
        "[redacted]",
        "<redacted>",
        "redacted",
        "placeholder",
        "example",
        "unset",
        "not-set",
        "your-api-key",
        "your_api_key",
        "token",
    }
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


def truncate_public_text(text: str, *, max_len: int = 10_000) -> str:
    """Bound explicit semantic product text without content-dependent rewriting."""
    limit = max(1, int(max_len))
    if len(text) <= limit:
        return text
    if limit <= 3:
        return "." * limit
    return text[: limit - 3] + "..."


def truncate_head_tail(text: str, *, max_chars: int = 4_000) -> str:
    """Bound large sandbox execution output, keeping head and tail with an omission marker.

    Mirrors DSPy's ``REPLHistory`` truncation semantics so the model sees that
    output was cut and how much. Deliberately does not redact: this text feeds
    the RLM code-repair loop and must stay semantically intact.
    """
    limit = max(1, int(max_chars))
    raw_len = len(text)
    if raw_len <= limit:
        return text
    half = limit // 2
    omitted = raw_len - limit
    return text[:half] + f"\n\n... ({omitted:,} characters omitted) ...\n\n" + text[-half:]


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
                if _is_sensitive_key(key)
                else sanitize_public_value(item, max_len=max_len, depth=depth + 1)
            )
            for key, item in list(value.items())[:50]
        }
    if isinstance(value, (list, tuple)):
        return [sanitize_public_value(item, max_len=max_len, depth=depth + 1) for item in list(value)[:50]]
    return sanitize_public_text(str(value), max_len=max_len)


def _is_safe_placeholder(value: str) -> bool:
    raw_candidate = value.strip().strip("\"'").strip()
    candidate = raw_candidate.lower()
    if candidate in _DECLARED_SAFE_PLACEHOLDERS:
        return True
    if re.fullmatch(r"[A-Z][A-Z0-9_]*", raw_candidate):
        return True
    return bool(
        re.fullmatch(
            r"(?:\$\{?[a-z_][a-z0-9_]*\}?|<[a-z_][a-z0-9_-]*>)",
            candidate,
            flags=re.IGNORECASE,
        )
    )


def _contains_sensitive_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return not _is_safe_placeholder(value)
    if isinstance(value, (list, tuple, dict)):
        return bool(value)
    return True


def _validate_declared_text(text: str) -> None:
    for match in _DECLARED_SECRET_ASSIGNMENT.finditer(text):
        if not _is_safe_placeholder(match.group("value")):
            raise ValueError("declared output contains a sensitive value")
    for match in _DECLARED_BEARER.finditer(text):
        if not _is_safe_placeholder(match.group("value")):
            raise ValueError("declared output contains a bearer credential")
    if _DECLARED_PROVIDER_TOKEN.search(text):
        raise ValueError("declared output contains a provider credential")
    if _DSNISH.search(text):
        raise ValueError("declared output contains a connection string")
    for match in _DECLARED_PRIVATE_PATH.finditer(text):
        path = match.group(0).rstrip(".,;:)]}")
        if path == "/home/daytona/fleet" or path.startswith("/home/daytona/fleet/"):
            continue
        raise ValueError("declared output contains a private host path")
    if _DECLARED_STACK_DUMP.search(text):
        raise ValueError("declared output contains a stack dump")
    if _DECLARED_PROMPT_DUMP.search(text):
        raise ValueError("declared output contains a system-prompt dump")


def validate_declared_public_value(value: Any, *, depth: int = 0) -> None:
    """Fail closed when an original declared output contains private material.

    This validator intentionally does not return a transformed value. Callers
    either preserve the accepted semantic output exactly or reject the Turn.
    """
    if depth >= 16:
        raise ValueError("declared output nesting is too deep")
    if value is None or isinstance(value, (bool, int, float)):
        return
    if isinstance(value, str):
        _validate_declared_text(value)
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if _is_sensitive_key(key) and _contains_sensitive_value(item):
                raise ValueError("declared output contains a sensitive structured field")
            validate_declared_public_value(item, depth=depth + 1)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            validate_declared_public_value(item, depth=depth + 1)
        return
    raise ValueError("declared output contains a non-JSON value")
