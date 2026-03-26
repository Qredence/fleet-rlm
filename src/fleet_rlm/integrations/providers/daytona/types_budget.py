"""Budget and LM bootstrap types for Daytona rollouts."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .types_serialization import (
    _coerce_nonnegative_int,
    _coerce_positive_int,
    _normalize_optional_text,
)


class DaytonaRunCancelled(RuntimeError):
    """Raised when a live Daytona rollout is cancelled by the caller."""


@dataclass(slots=True)
class RolloutBudget:
    """Global budget for one Daytona RLM rollout."""

    max_sandboxes: int = 50
    max_depth: int = 2
    max_iterations: int = 50
    global_timeout: int = 3600
    result_truncation_limit: int = 10_000
    batch_concurrency: int = 4

    @classmethod
    def from_raw(cls, raw: Any) -> RolloutBudget:
        if not isinstance(raw, dict):
            return cls()
        return cls(
            max_sandboxes=_coerce_positive_int(raw.get("max_sandboxes")) or 50,
            max_depth=_coerce_nonnegative_int(raw.get("max_depth")) or 2,
            max_iterations=_coerce_positive_int(raw.get("max_iterations")) or 50,
            global_timeout=_coerce_positive_int(raw.get("global_timeout")) or 3600,
            result_truncation_limit=(
                _coerce_positive_int(raw.get("result_truncation_limit")) or 10_000
            ),
            batch_concurrency=_coerce_positive_int(raw.get("batch_concurrency")) or 4,
        )


@dataclass(slots=True)
class SandboxLmRuntimeConfig:
    """Serializable LM bootstrap config passed into sandbox-local runtimes."""

    model: str
    api_key: str
    api_base: str | None = None
    max_tokens: int = 64_000
    delegate_model: str | None = None
    delegate_api_key: str | None = None
    delegate_api_base: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_raw(cls, raw: Any) -> SandboxLmRuntimeConfig:
        if not isinstance(raw, dict):
            raise ValueError("Sandbox LM config must be a dict.")
        model = _normalize_optional_text(raw.get("model"))
        api_key = _normalize_optional_text(raw.get("api_key"))
        if model is None or api_key is None:
            raise ValueError("Sandbox LM config requires model and api_key.")
        return cls(
            model=model,
            api_key=api_key,
            api_base=_normalize_optional_text(raw.get("api_base")),
            max_tokens=_coerce_positive_int(raw.get("max_tokens")) or 64_000,
            delegate_model=_normalize_optional_text(raw.get("delegate_model")),
            delegate_api_key=_normalize_optional_text(raw.get("delegate_api_key")),
            delegate_api_base=_normalize_optional_text(raw.get("delegate_api_base")),
        )


__all__ = [
    "DaytonaRunCancelled",
    "RolloutBudget",
    "SandboxLmRuntimeConfig",
]
