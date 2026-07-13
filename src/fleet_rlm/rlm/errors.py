"""Typed errors for RLM construction, budgets, and turn termination."""

from __future__ import annotations


class RLMConfigError(ValueError):
    """Base class for Fleet RLM RLM configuration failures."""


class RunBudgetError(RLMConfigError):
    """Raised when immutable Run budget limits are invalid."""


class RLMModelBundleError(RLMConfigError):
    """Raised when required model roles are missing or invalid."""


class TurnTerminalError(RuntimeError):
    """Base for clean turn termination with a stable public status."""

    status: str = "failed"
    public_message: str = "Turn failed"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.public_message)
        if message:
            self.public_message = message


class TurnCancelled(TurnTerminalError):
    status = "cancelled"
    public_message = "Turn cancelled"


class TurnTimeout(TurnTerminalError):
    status = "timeout"
    public_message = "Turn timed out"


class TurnBudgetExhausted(TurnTerminalError):
    status = "budget_exhausted"
    public_message = "Turn budget exhausted"
