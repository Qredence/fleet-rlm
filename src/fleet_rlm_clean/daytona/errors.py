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


@dataclass(slots=True)
class ProviderRequestError(DaytonaAdapterError):
    """Provider call failed for a reason other than explicit not-found.

    Auth, network, rate-limit, 5xx, and timeouts map here — never treated as missing.
    """


def is_sandbox_not_found(exc: BaseException) -> bool:
    """True only for explicit provider not-found (404 / DaytonaNotFoundError)."""
    name = type(exc).__name__
    if name in {"DaytonaNotFoundError", "NotFoundError", "NotFound"}:
        return True
    status = getattr(exc, "status_code", None)
    if status == 404:
        return True
    # Nested HTTP responses (httpx / requests style).
    response = getattr(exc, "response", None)
    if response is not None and getattr(response, "status_code", None) == 404:
        return True
    return False


def map_provider_error(exc: BaseException) -> DaytonaAdapterError:
    """Map a provider/SDK exception into a sanitized Fleet error."""
    if isinstance(exc, DaytonaAdapterError):
        return exc
    message = sanitize_provider_message(str(exc))
    cause = type(exc).__name__
    if is_sandbox_not_found(exc):
        return DaytonaAdapterError(message=message, cause_type=cause)
    return ProviderRequestError(message=message, cause_type=cause)
