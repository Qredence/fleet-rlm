"""Shared diagnostics and smoke-validation helpers for the Daytona pilot."""

from __future__ import annotations

import time
from typing import Any, Callable, Final, TypeVar

from dspy.primitives import FinalOutput

from .types import DaytonaSmokeResult

_T = TypeVar("_T")

# ---------------------------------------------------------------------------
# Diagnostic constants and error types
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Smoke validation (formerly smoke.py)
# ---------------------------------------------------------------------------


def _new_phase_timings() -> dict[str, int]:
    return {phase: 0 for phase in SMOKE_PHASES}


def _run_timed(
    phase_timings_ms: dict[str, int],
    phase: str,
    action: Callable[[], _T],
) -> _T:
    started = time.perf_counter()
    try:
        return action()
    finally:
        phase_timings_ms[phase] = int((time.perf_counter() - started) * 1000)


def run_daytona_smoke(
    *,
    repo: str,
    ref: str | None = None,
    runtime: Any | None = None,
    timeout: float = 60.0,
) -> DaytonaSmokeResult:
    """Run a small real Daytona smoke validation without invoking an LM."""

    from .interpreter import DaytonaInterpreter
    from .runtime import DaytonaSandboxRuntime

    phase_timings_ms = _new_phase_timings()
    termination_phase = "config"
    error_category: str | None = None
    error_message: str | None = None
    interpreter: DaytonaInterpreter | None = None
    sandbox_id: str | None = None
    workspace_path = ""
    driver_started = False
    persisted_state_value: Any = None
    finalization_mode = "unknown"
    owns_runtime = runtime is None
    runtime_instance = runtime

    try:
        if runtime_instance is None:
            runtime_instance = _run_timed(
                phase_timings_ms,
                "config",
                DaytonaSandboxRuntime,
            )

        interpreter = DaytonaInterpreter(
            runtime=runtime_instance,
            owns_runtime=owns_runtime,
            timeout=int(timeout),
            execute_timeout=int(timeout),
            repo_url=repo,
            repo_ref=ref,
        )

        termination_phase = "sandbox_create"

        def _ensure_session() -> Any:
            return interpreter._ensure_session_sync()

        session = _run_timed(phase_timings_ms, "sandbox_create", _ensure_session)
        for phase_name, duration_ms in getattr(session, "phase_timings_ms", {}).items():
            phase_timings_ms[phase_name] = int(duration_ms)
        sandbox_id = getattr(session, "sandbox_id", None)
        workspace_path = str(getattr(session, "workspace_path", "") or "")

        termination_phase = "driver_start"
        _run_timed(
            phase_timings_ms,
            "driver_start",
            interpreter.start,
        )
        driver_started = True

        termination_phase = "exec_step_1"
        first = _run_timed(
            phase_timings_ms,
            "exec_step_1",
            lambda: interpreter.execute(
                (
                    'readme_preview = read_file("README.md")[:120]\n'
                    "if not readme_preview:\n"
                    '    raise RuntimeError("README.md is empty or unreadable")\n'
                    "counter = 2\n"
                )
            ),
        )
        if isinstance(first, FinalOutput):
            raise RuntimeError(
                "Smoke validation finalized too early on the first execution step."
            )

        termination_phase = "exec_step_2"
        second = _run_timed(
            phase_timings_ms,
            "exec_step_2",
            lambda: interpreter.execute("counter += 3\nSUBMIT(output=counter)"),
        )
        if not isinstance(second, FinalOutput):
            raise RuntimeError("Smoke validation did not produce a final artifact.")

        output = getattr(second, "output", None)
        output = output if isinstance(output, dict) else {}
        persisted_state_value = output.get("output")
        finalization_mode = "SUBMIT"
        termination_phase = "completed"
    except Exception as exc:
        diagnostic = as_diagnostic_error(exc, phase=termination_phase)
        error_category = diagnostic.category
        error_message = str(diagnostic)
        termination_phase = diagnostic.phase
    finally:
        if interpreter is not None:
            try:
                _run_timed(
                    phase_timings_ms,
                    "cleanup",
                    interpreter.shutdown,
                )
            except Exception as cleanup_exc:
                if error_category is None:
                    cleanup_error = as_diagnostic_error(cleanup_exc, phase="cleanup")
                    error_category = cleanup_error.category
                    error_message = str(cleanup_error)
                    termination_phase = cleanup_error.phase
                elif error_message is not None:
                    error_message = (
                        f"{error_message} Cleanup also failed: {cleanup_exc}"
                    )
        elif owns_runtime and runtime_instance is not None:
            try:
                _run_timed(
                    phase_timings_ms,
                    "cleanup",
                    runtime_instance.close,
                )
            except Exception as cleanup_exc:
                if error_category is None:
                    cleanup_error = as_diagnostic_error(cleanup_exc, phase="cleanup")
                    error_category = cleanup_error.category
                    error_message = str(cleanup_error)
                    termination_phase = cleanup_error.phase
                elif error_message is not None:
                    error_message = (
                        f"{error_message} Cleanup also failed: {cleanup_exc}"
                    )

    return DaytonaSmokeResult(
        repo=repo,
        ref=ref,
        sandbox_id=sandbox_id,
        workspace_path=workspace_path,
        persisted_state_value=persisted_state_value,
        driver_started=driver_started,
        finalization_mode=finalization_mode,
        termination_phase=termination_phase,
        error_category=error_category,
        phase_timings_ms=phase_timings_ms,
        error_message=error_message,
    )
