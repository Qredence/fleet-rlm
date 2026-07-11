"""Fleet-facing Daytona error types for the clean-backend adapter."""

from __future__ import annotations

import re
from dataclasses import dataclass

_SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|token|secret|password|authorization)\s*[:=]\s*\S+"),
    re.compile(r"(?i)bearer\s+\S+"),
    re.compile(r"/[^\s]*secret[^\s]*", re.IGNORECASE),
    re.compile(r"/tmp/[^\s]+"),
    re.compile(r"/home/[^\s]+"),
    re.compile(r"sk-[A-Za-z0-9_-]+"),
)


def sanitize_provider_message(raw: str) -> str:
    """Strip credentials and private paths from provider error text."""
    message = raw.strip() or "Daytona provider error"
    for pattern in _SECRET_PATTERNS:
        message = pattern.sub("[redacted]", message)
    return message


@dataclass(slots=True)
class DaytonaAdapterError(Exception):
    """Normalized Daytona failure safe for Fleet callers."""

    message: str
    cause_type: str | None = None

    def __str__(self) -> str:
        return self.message


def map_provider_error(exc: BaseException) -> DaytonaAdapterError:
    """Map a provider/SDK exception into a sanitized Fleet error."""
    return DaytonaAdapterError(
        message=sanitize_provider_message(str(exc)),
        cause_type=type(exc).__name__,
    )
