"""Configuration models for PostHog LLM analytics."""

from __future__ import annotations

import os

from pydantic import BaseModel, Field


def _env_bool(value: str | None, *, default: bool) -> bool:
    """Parse booleans from environment-friendly strings."""
    if value is None:
        return default
    candidate = value.strip().lower()
    if candidate in {"1", "true", "yes", "on"}:
        return True
    if candidate in {"0", "false", "no", "off"}:
        return False
    return default


class PostHogConfig(BaseModel):
    """PostHog LLM analytics configuration."""

    enabled: bool = Field(default=False)
    api_key: str | None = Field(default=None)
    host: str = Field(default="https://us.i.posthog.com")
    flush_interval: float = Field(default=10.0)
    flush_at: int = Field(default=10)
    enable_dspy_optimization: bool = Field(default=False)
    input_truncation_chars: int = Field(default=10000)
    output_truncation_chars: int = Field(default=5000)
    redact_sensitive: bool = Field(default=True)

    @classmethod
    def from_env(cls) -> "PostHogConfig":
        """Load analytics configuration from environment variables."""
        return cls(
            enabled=_env_bool(os.getenv("POSTHOG_ENABLED"), default=False),
            api_key=os.getenv("POSTHOG_API_KEY") or None,
            host=os.getenv("POSTHOG_HOST") or "https://us.i.posthog.com",
            flush_interval=float(os.getenv("POSTHOG_FLUSH_INTERVAL", "10.0")),
            flush_at=max(1, int(os.getenv("POSTHOG_FLUSH_AT", "10"))),
            enable_dspy_optimization=_env_bool(
                os.getenv("POSTHOG_ENABLE_DSPY_OPTIMIZATION"), default=False
            ),
            input_truncation_chars=max(
                1, int(os.getenv("POSTHOG_INPUT_TRUNCATION", "10000"))
            ),
            output_truncation_chars=max(
                1, int(os.getenv("POSTHOG_OUTPUT_TRUNCATION", "5000"))
            ),
            redact_sensitive=_env_bool(
                os.getenv("POSTHOG_REDACT_SENSITIVE"), default=True
            ),
        )
