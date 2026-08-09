"""Typed errors for RLM construction and Run termination."""

from __future__ import annotations


class RLMConfigError(ValueError):
    """Base class for Fleet RLM RLM configuration failures."""


class RLMModelBundleError(RLMConfigError):
    """Raised when required model roles are missing or invalid."""


class RunTerminalError(RuntimeError):
    """Base for clean Run termination with a stable public status."""

    status: str = "failed"
    public_message: str = "Turn failed"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.public_message)
        if message:
            self.public_message = message


class RunCancelledError(RunTerminalError):
    status = "cancelled"
    public_message = "Turn cancelled"


class RunTimeoutError(RunTerminalError):
    status = "timeout"
    public_message = "Turn timed out"


class RunNoProgressError(RunTerminalError):
    public_message = "Turn stopped after repeated tool calls made no progress"


class RunIntegrityFailureError(RunTerminalError):
    public_message = "Turn failed because a required workspace update was not completed"
