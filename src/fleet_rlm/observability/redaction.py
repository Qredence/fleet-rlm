"""Client-safe redaction for runtime and trace projections."""

from __future__ import annotations

import re
from typing import Any

from fleet_rlm.runtime.events import RuntimeEvent, RuntimeEventContext, RuntimeEventKind, RuntimeToolInfo

REDACTED_VALUE = "[REDACTED]"
SAFE_RUNTIME_ERROR = "Runtime operation failed."

_SENSITIVE_KEY_PARTS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "credential",
        "password",
        "private_key",
        "secret",
        "token",
    }
)
_ERROR_KEY_PARTS = frozenset({"error", "exception", "stack", "stderr", "traceback"})
_PATH_KEY_PARTS = frozenset({"path", "workspace_path", "volume_path", "host_path"})
_BEARER_PATTERN = re.compile(r"(?i)bearer\s+[a-z0-9._~+\-/=]+")
_API_KEY_PATTERN = re.compile(r"\b(?:sk|pk|rk)-[a-zA-Z0-9_-]{8,}\b")
_INLINE_SECRET_PATTERN = re.compile(r"(?i)\b(?:access[_-]?token|client[_-]?secret|api[_-]?key)\s*[:=]\s*[^\s,;]+")
_ABSOLUTE_PATH_PATTERN = re.compile(r"(?<![\w:/.-])/(?:[^\s)\]}>,;]+)")
_SAFE_ERROR_TEXTS = frozenset(
    {
        SAFE_RUNTIME_ERROR,
        "Adapter parse failed while reading the model response.",
        "Model response could not be rendered safely.",
    }
)


def _key_parts(key: str | None) -> set[str]:
    if not key:
        return set()
    normalized = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", key.strip())
    normalized = normalized.lower().replace("-", "_").replace(".", "_")
    return {part for part in normalized.split("_") if part}


def _normalized_key(key: str | None) -> str:
    if not key:
        return ""
    normalized = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", key.strip())
    return normalized.lower().replace("-", "_").replace(".", "_")


def _is_secret_key(key: str | None) -> bool:
    parts = _key_parts(key)
    if {"token", "usage"}.issubset(parts) or parts in ({"input", "tokens"}, {"output", "tokens"}, {"total", "tokens"}):
        return False
    normalized = _normalized_key(key)
    return (
        normalized in _SENSITIVE_KEY_PARTS
        or bool(parts & _SENSITIVE_KEY_PARTS)
        or {"api", "key"}.issubset(parts)
        or {"private", "key"}.issubset(parts)
    )


def _is_error_detail(key: str | None, value: Any) -> bool:
    parts = _key_parts(key)
    if not parts & _ERROR_KEY_PARTS or isinstance(value, bool):
        return False
    normalized = _normalized_key(key)
    return not normalized.endswith(("_code", "_category"))


def _is_path_key(key: str | None) -> bool:
    if not key:
        return False
    normalized = key.strip().lower().replace("-", "_")
    return normalized in _PATH_KEY_PARTS or normalized.endswith("_path")


def redact_text(value: str) -> str:
    """Redact common credential and local-path forms from free-form text."""
    safe = _BEARER_PATTERN.sub("Bearer " + REDACTED_VALUE, value)
    safe = _API_KEY_PATTERN.sub(REDACTED_VALUE, safe)
    safe = _INLINE_SECRET_PATTERN.sub(REDACTED_VALUE, safe)
    return _ABSOLUTE_PATH_PATTERN.sub(REDACTED_VALUE, safe)


def redact_value(value: Any, *, key: str | None = None) -> Any:
    """Recursively make an arbitrary trace value safe for a client projection."""
    if _is_secret_key(key) or _is_path_key(key):
        return REDACTED_VALUE
    if _is_error_detail(key, value):
        return SAFE_RUNTIME_ERROR
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {
            str(child_key): redact_value(child_value, key=str(child_key)) for child_key, child_value in value.items()
        }
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_value(item) for item in value)
    return value


def sanitize_runtime_event(event: RuntimeEvent) -> RuntimeEvent:
    """Return a copy of one event whose client-observable values are redacted."""
    tool: RuntimeToolInfo | None = event.tool
    if tool is not None:
        tool = tool.model_copy(
            update={
                "tool_args": redact_value(tool.tool_args),
                "tool_input": redact_value(tool.tool_input, key="tool_input"),
                "tool_output": redact_value(tool.tool_output, key="tool_output"),
            }
        )

    context: RuntimeEventContext | None = event.context
    if context is not None:
        context = context.model_copy(
            update={
                name: redact_value(value, key=name) for name, value in context.model_dump().items() if value is not None
            }
        )

    text = redact_text(event.text)
    if event.kind is RuntimeEventKind.ERROR and text not in _SAFE_ERROR_TEXTS:
        text = SAFE_RUNTIME_ERROR

    return event.model_copy(
        update={
            "text": text,
            "payload": redact_value(event.payload),
            "tool": tool,
            "context": context,
        }
    )


__all__ = [
    "REDACTED_VALUE",
    "SAFE_RUNTIME_ERROR",
    "redact_text",
    "redact_value",
    "sanitize_runtime_event",
]
