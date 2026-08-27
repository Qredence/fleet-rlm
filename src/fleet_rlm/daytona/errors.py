"""Fleet-facing Daytona error types for the Fleet RLM adapter."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

ProviderFailureKind = Literal[
    "auth",
    "quota",
    "network",
    "timeout",
    "provider_5xx",
    "request_validation",
    "mount_mismatch",
    "interpreter",
    "unknown",
]

DEFAULT_SANITIZED_FAILURE_MAX_CHARS = 200

_SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|token|secret|password|authorization)\s*[:=]\s*\S+"),
    re.compile(r"(?i)bearer\s+\S+"),
    re.compile(r"/[^\s]*secret[^\s]*", re.IGNORECASE),
    re.compile(r"/tmp/[^\s]+"),
    re.compile(r"/home/[^\s]+"),
    re.compile(r"/(?:Users|Volumes|private|var|etc|opt|root|mnt|srv)/[^\s]+"),
    re.compile(r"sk-[A-Za-z0-9_-]+"),
)
_URL_PATTERN = re.compile(r"(?i)\bhttps?://[^\s\"'<>]+")
_ANSI_PATTERN = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\)|.)")
_CONTROL_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")

# Bounded HTTP status classes that may appear in already-sanitized provider
# text when the exception carries no structured ``status_code`` metadata (for
# example a gateway error wrapped by litellm/dspy). These ground classification
# for statuses such as 404 that would otherwise fall through to ``unknown``.
_BARE_STATUS_TEXT = re.compile(r"\b(4\d{2}|5\d{2})\b")


def _sanitized_status(exc: object) -> int | None:
    """Extract a provider HTTP status even when it is only present in the text."""
    status = provider_status_code(exc)
    if isinstance(status, int):
        return status
    message = sanitize_provider_message(str(getattr(exc, "message", str(exc)) or ""))
    match = _BARE_STATUS_TEXT.search(message)
    return int(match.group(1)) if match is not None else None


def sanitize_provider_message(raw: str) -> str:
    """Strip credentials, URLs, and control sequences from provider error text."""
    message = raw.strip() or "Daytona provider error"
    for pattern in _SECRET_PATTERNS:
        message = pattern.sub("[redacted]", message)
    message = _URL_PATTERN.sub("[redacted-url]", message)
    message = _ANSI_PATTERN.sub("", message)
    message = _CONTROL_PATTERN.sub(" ", message)
    return message


def sanitize_failure_text(exc: BaseException, *, max_chars: int = DEFAULT_SANITIZED_FAILURE_MAX_CHARS) -> str:
    """Credential-free ``TypeName: message`` failure description for receipts.

    Single owner for lease receipts and deletion-probe error strings; text is
    exactly :func:`sanitize_provider_message` output, so redaction policy
    cannot drift between lifecycle lanes.
    """
    return f"{type(exc).__name__}: {sanitize_provider_message(str(exc))}"[:max_chars]


@dataclass(slots=True)
class DaytonaAdapterError(Exception):
    """Normalized Daytona failure safe for Fleet callers."""

    message: str
    cause_type: str | None = None
    status_code: int | None = None

    def __str__(self) -> str:
        return self.message


@dataclass(slots=True)
class ProviderRequestError(DaytonaAdapterError):
    """Provider call failed for a reason other than explicit not-found.

    Auth, network, rate-limit, 5xx, and timeouts map here — never treated as missing.
    """


def provider_status_code(exc: object) -> int | None:
    """Extract provider HTTP status metadata without inspecting exception text."""
    direct = getattr(exc, "status_code", None)
    if isinstance(direct, int):
        return direct
    response = getattr(exc, "response", None)
    nested = getattr(response, "status_code", None)
    return nested if isinstance(nested, int) else None


def provider_status_category(status_code: int | None) -> str:
    """Return a bounded status class suitable for structured diagnostics."""
    if status_code is None or status_code < 100 or status_code > 599:
        return "none"
    return f"{status_code // 100}xx"


def classify_provider_error(exc: object) -> ProviderFailureKind:
    """Classify a provider failure using metadata and already-sanitized text."""
    cause_type = getattr(exc, "cause_type", None)
    type_name = cause_type if isinstance(cause_type, str) and cause_type else type(exc).__name__
    normalized = type_name.replace("-", "_").lower()
    status = _sanitized_status(exc)
    message_value = getattr(exc, "message", "")
    safe_message = sanitize_provider_message(str(message_value or "")).lower()

    if "mount" in normalized and ("mismatch" in normalized or "workspace" in normalized):
        return "mount_mismatch"
    if "interpreter" in normalized:
        return "interpreter"
    if status in {401, 403} or any(part in normalized for part in ("auth", "unauthorized", "forbidden")):
        return "auth"
    if (
        status == 429
        or any(part in normalized for part in ("quota", "ratelimit", "rate_limit"))
        or any(
            marker in safe_message
            for marker in ("quota", "limit exceeded", "capacity limit", "upgrade your organization")
        )
    ):
        return "quota"
    if isinstance(exc, TimeoutError) or "timeout" in normalized or "timedout" in normalized:
        return "timeout"
    if isinstance(exc, OSError) or any(
        part in normalized for part in ("connection", "network", "connecterror", "transporterror")
    ):
        return "network"
    if status is not None and 500 <= status <= 599:
        return "provider_5xx"
    if status in {400, 404, 409, 422} or any(
        part in normalized for part in ("validation", "badrequest", "invalidrequest", "valueerror")
    ):
        return "request_validation"
    return "unknown"


def is_safe_pre_creation_retry(exc: object) -> bool:
    """True only for transient failures before sandbox creation is attempted."""
    return classify_provider_error(exc) in {"network", "timeout", "provider_5xx"}


_TRANSIENT_STATUS_TEXT = re.compile(r"\b5\d{2}\b")


def is_transient_provider_failure(exc: object) -> bool:
    """True for transient provider failures: network, timeout, or 5xx.

    The 5xx text-marker leg covers provider failures that arrive without
    structured status metadata (e.g. preview-link resolution errors).
    """
    if is_safe_pre_creation_retry(exc):
        return True
    return _TRANSIENT_STATUS_TEXT.search(sanitize_provider_message(str(exc))) is not None


def is_sandbox_not_found(exc: BaseException) -> bool:
    """True only for explicit provider not-found (404 / DaytonaNotFoundError)."""
    names = {type(exc).__name__}
    cause_type = getattr(exc, "cause_type", None)
    if isinstance(cause_type, str):
        names.add(cause_type)
    if names & {"DaytonaNotFoundError", "NotFoundError", "NotFound"}:
        return True
    status = getattr(exc, "status_code", None)
    if status == 404:
        return True
    # Nested HTTP responses (httpx / requests style).
    response = getattr(exc, "response", None)
    return bool(response is not None and getattr(response, "status_code", None) == 404)


def map_provider_error(exc: BaseException) -> DaytonaAdapterError:
    """Map a provider/SDK exception into a sanitized Fleet error."""
    if isinstance(exc, DaytonaAdapterError):
        return exc
    message = sanitize_provider_message(str(exc))
    cause = type(exc).__name__
    status = provider_status_code(exc)
    if is_sandbox_not_found(exc):
        return DaytonaAdapterError(message=message, cause_type=cause, status_code=status)
    return ProviderRequestError(message=message, cause_type=cause, status_code=status)
