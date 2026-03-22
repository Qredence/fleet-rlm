"""Shared diagnostics helpers for the experimental Daytona pilot."""

from __future__ import annotations

from typing import Final

SMOKE_PHASES: Final[tuple[str, ...]] = (
    "config",
    "sandbox_create",
    "repo_clone",
    "driver_start",
    "exec_step_1",
    "exec_step_2",
    "cleanup",
)

PHASE_TO_ERROR_CATEGORY: Final[dict[str, str]] = {
    "config": "config_error",
    "sandbox_create": "sandbox_create_clone_error",
    "repo_clone": "sandbox_create_clone_error",
    "driver_start": "driver_handshake_error",
    "exec_step_1": "driver_execution_error",
    "exec_step_2": "driver_execution_error",
    "cleanup": "cleanup_error",
}


class DaytonaDiagnosticError(RuntimeError):
    """Structured Daytona pilot runtime error with a stable category and phase."""

    def __init__(self, message: str, *, category: str, phase: str) -> None:
        super().__init__(message)
        self.category = category
        self.phase = phase


def category_for_phase(phase: str) -> str:
    """Return the stable error category for a smoke/runtime phase."""

    return PHASE_TO_ERROR_CATEGORY.get(phase, "driver_execution_error")


def as_diagnostic_error(exc: BaseException, *, phase: str) -> DaytonaDiagnosticError:
    """Coerce arbitrary exceptions into a structured Daytona diagnostic error."""

    if isinstance(exc, DaytonaDiagnosticError):
        return exc
    return DaytonaDiagnosticError(
        str(exc),
        category=category_for_phase(phase),
        phase=phase,
    )
