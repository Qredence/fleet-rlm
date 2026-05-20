"""Centralized error types for the Daytona integration."""

from __future__ import annotations

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
]
