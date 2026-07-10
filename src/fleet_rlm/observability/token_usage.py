"""Small, provider-neutral token-usage helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """Normalized token counters, preserving unknown values as zero."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0

    def add(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
        )


def int_or_none(value: Any) -> int | None:
    """Coerce ordinary numeric telemetry values without accepting booleans."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.strip():
        try:
            return int(float(value.strip()))
        except ValueError:
            return None
    return None


def _mapping_usage(payload: dict[str, Any]) -> dict[str, Any]:
    for key in ("token_usage", "usage", "mlflow.chat.tokenUsage", "mlflow.chat.tokenUsageJson"):
        candidate = payload.get(key)
        if isinstance(candidate, dict):
            return candidate
    return payload


def token_usage_from_mapping(payload: dict[str, Any] | None) -> TokenUsage:
    """Extract common provider token fields from an event or span mapping."""
    if not isinstance(payload, dict):
        return TokenUsage()

    usage = _mapping_usage(payload)
    input_tokens = int_or_none(
        usage.get("input_tokens")
        or usage.get("prompt_tokens")
        or usage.get("inputTokens")
        or payload.get("mlflow.chat.inputTokens")
    )
    output_tokens = int_or_none(
        usage.get("output_tokens")
        or usage.get("completion_tokens")
        or usage.get("outputTokens")
        or payload.get("mlflow.chat.outputTokens")
    )
    total_tokens = int_or_none(
        usage.get("total_tokens") or usage.get("totalTokens") or payload.get("mlflow.chat.totalTokens")
    )
    if total_tokens is None:
        total_tokens = int(input_tokens or 0) + int(output_tokens or 0)

    return TokenUsage(
        input_tokens=int(input_tokens or 0),
        output_tokens=int(output_tokens or 0),
        total_tokens=int(total_tokens or 0),
    )


__all__ = ["TokenUsage", "int_or_none", "token_usage_from_mapping"]
