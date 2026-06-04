"""Stream failure classification for websocket runtime services."""

from __future__ import annotations


class PersistenceRequiredError(RuntimeError):
    """Raised when durable writes fail in strict-persistence mode."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def classify_stream_failure(exc: Exception) -> str:
    """Map runtime failures to stable websocket-facing error codes."""
    if isinstance(exc, PersistenceRequiredError):
        return exc.code

    lowered = str(exc).lower()
    if "planner lm not configured" in lowered:
        return "planner_missing"
    if "llm call timed out" in lowered or "timed out" in lowered and "llm" in lowered:
        return "llm_timeout"
    if "rate limit" in lowered or "429" in lowered:
        return "llm_rate_limited"
    if "sandbox" in lowered or "daytona" in lowered:
        return "sandbox_unavailable"
    return "internal_error"


__all__ = ["PersistenceRequiredError", "classify_stream_failure"]
