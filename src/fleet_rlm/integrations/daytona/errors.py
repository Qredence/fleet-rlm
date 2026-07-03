"""Centralized error types for the Daytona integration."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any


class DaytonaDiagnosticError(RuntimeError):
    """Structured Daytona pilot runtime error with a stable category and phase."""

    def __init__(self, message: str, *, category: str, phase: str) -> None:
        super().__init__(message)
        self.category = category
        self.phase = phase


class DaytonaConfigError(DaytonaDiagnosticError):
    """Raised when Daytona runtime configuration is incomplete or invalid."""

    def __init__(self, message: str) -> None:
        super().__init__(message, category="config_error", phase="config")


class VolumeNotReadyError(DaytonaDiagnosticError):
    """Raised when a Daytona volume does not reach ``ready`` state in time."""

    def __init__(
        self,
        *,
        volume_name: str,
        volume_state: str,
        timeout_seconds: float,
        raw_volume_state: str | None = None,
    ) -> None:
        self.volume_name = volume_name
        self.volume_state = volume_state
        self.raw_volume_state = raw_volume_state or volume_state
        self.timeout_seconds = timeout_seconds
        state_description = f"'{volume_state}'"
        if self.raw_volume_state and self.raw_volume_state.strip() and self.raw_volume_state != volume_state:
            state_description = f"normalized='{volume_state}' (raw='{self.raw_volume_state}')"
        super().__init__(
            f"Volume '{volume_name}' is in state {state_description} "
            f"after {timeout_seconds}s. Check Daytona dashboard.",
            category="sandbox_create_clone_error",
            phase="sandbox_create",
        )


class DaytonaRunCancelled(RuntimeError):
    """Raised when a live Daytona rollout is cancelled by the caller."""


# ---------------------------------------------------------------------------
# Sandbox-boundary error redaction
# ---------------------------------------------------------------------------
#
# Any error string that crosses the host→sandbox boundary (tool callback
# results, broker failure stubs, evidence bridge errors) must flow through
# ``sandbox_safe_error`` so that DATABASE_URL, API keys, JWTs, and other
# credentials leaked by upstream exception messages are scrubbed before the
# sandbox LLM sees them.

# Regex pattern to detect potential credential strings in error messages.
# Ordered from most-specific to least-specific to avoid partial matches.
_CREDENTIAL_PATTERN = re.compile(
    r"("
    # Full database/service URLs (match scheme + everything to next whitespace or comma)
    r"(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|neon(?:db)?|redis|amqp)://[^\s,;'\"]*"
    r"|password=[^\s,;'\"&]*"
    r"|sslpassword=[^\s,;'\"&]*"
    r"|host=[^\s,;'\"&]+"
    # Named environment variable references, optionally with an inline value
    # (``API_KEY=sk-...``) so the secret after ``=`` is scrubbed too, not just
    # the variable name.
    r"|(?:DAYTONA_API_KEY|DATABASE(?:_ADMIN)?_URL|(?:LLM_|OPENAI_|ANTHROPIC_|AZURE_)?API_KEY|SECRET_KEY|ACCESS_TOKEN)(?:=[^\s,;'\"]*)?"
    # JWT-like base64 tokens (at least 20 chars in the header section)
    r"|eyJ[A-Za-z0-9+/\-_]{20,}"
    r")",
    re.IGNORECASE,
)


def redact_error_message(message: str) -> str:
    """Replace potential credential-bearing patterns with a safe placeholder."""
    return _CREDENTIAL_PATTERN.sub("[REDACTED]", message)


def sandbox_safe_error(exc: BaseException) -> str:
    """Return a redacted, sandbox-safe error string from an exception.

    Use this at every boundary where an exception message would otherwise be
    returned into sandbox code (tool callback results, broker stubs, evidence
    bridge errors).
    """
    return redact_error_message(str(exc))


@dataclass(slots=True)
class DaytonaSmokeResult:
    """Result of a Daytona live/runtime smoke check."""

    repo: str
    ref: str | None
    sandbox_id: str | None
    workspace_path: str = ""
    persisted_state_value: Any = None
    driver_started: bool = False
    finalization_mode: str = "unknown"
    termination_phase: str = "config"
    error_category: str | None = None
    phase_timings_ms: dict[str, int] = field(default_factory=dict)
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


__all__ = [
    "DaytonaConfigError",
    "DaytonaDiagnosticError",
    "DaytonaRunCancelled",
    "DaytonaSmokeResult",
    "VolumeNotReadyError",
    "redact_error_message",
    "sandbox_safe_error",
]
