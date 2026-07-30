"""Typed errors for RLM construction and turn termination."""

from __future__ import annotations


class RLMConfigError(ValueError):
    """Base class for Fleet RLM RLM configuration failures."""


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


class TurnCancelledError(TurnTerminalError):
    status = "cancelled"
    public_message = "Turn cancelled"


class TurnTimeoutError(TurnTerminalError):
    status = "timeout"
    public_message = "Turn timed out"


class TurnNoProgressError(TurnTerminalError):
    public_message = "Turn stopped after repeated tool calls made no progress"


class TurnIntegrityFailureError(TurnTerminalError):
    public_message = "Turn failed because a required workspace update was not completed"


class TurnParseExhaustedError(TurnTerminalError):
    public_message = "Turn stopped after the model repeatedly produced unparseable responses"
