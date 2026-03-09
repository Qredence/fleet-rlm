"""CLI-first Daytona smoke validation for the experimental pilot."""

from __future__ import annotations

import time
from typing import Any, Callable, TypeVar

from .diagnostics import SMOKE_PHASES, as_diagnostic_error
from .sandbox import DaytonaSandboxRuntime
from .types import DaytonaSmokeResult

_T = TypeVar("_T")


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
    runtime: DaytonaSandboxRuntime | Any | None = None,
    timeout: float = 60.0,
) -> DaytonaSmokeResult:
    """Run a small real Daytona smoke validation without invoking an LM."""

    def _unexpected_callback(request: Any) -> Any:
        raise RuntimeError(
            f"Smoke runner does not expect host callbacks: {request.name}"
        )

    phase_timings_ms = _new_phase_timings()
    termination_phase = "config"
    error_category: str | None = None
    error_message: str | None = None
    session: Any | None = None
    sandbox_id: str | None = None
    repo_path = ""
    driver_started = False
    persisted_state_value: Any = None
    finalization_mode = "unknown"
    prompt_handle: Any = None

    try:
        runtime_instance = runtime
        if runtime_instance is None:
            runtime_instance = _run_timed(
                phase_timings_ms,
                "config",
                lambda: DaytonaSandboxRuntime(),
            )

        termination_phase = "sandbox_create"
        if hasattr(runtime_instance, "create_repo_session_with_diagnostics"):
            session, bootstrap_timings = (
                runtime_instance.create_repo_session_with_diagnostics(
                    repo_url=repo,
                    ref=ref,
                )
            )
            for phase_name, duration_ms in bootstrap_timings.items():
                phase_timings_ms[phase_name] = int(duration_ms)
        else:
            session = _run_timed(
                phase_timings_ms,
                "sandbox_create",
                lambda: runtime_instance.create_repo_session(
                    repo_url=repo,
                    ref=ref,
                ),
            )

        sandbox_id = getattr(session, "sandbox_id", None)
        repo_path = str(getattr(session, "repo_path", "") or "")

        termination_phase = "driver_start"
        _run_timed(
            phase_timings_ms,
            "driver_start",
            lambda: session.start_driver(timeout=timeout),
        )
        driver_started = True

        termination_phase = "exec_step_1"

        def _step_one() -> Any:
            nonlocal prompt_handle
            prompt_handle = session.store_prompt(
                text="Smoke prompt persistence check.\nThis prompt must survive.",
                kind="smoke",
                label="smoke-prompt",
                timeout=timeout,
            )
            return session.execute_code(
                code=(
                    'readme_preview = read_file("README.md")[:120]\n'
                    "if not readme_preview:\n"
                    '    raise RuntimeError("README.md is empty or unreadable")\n'
                    "counter = 2\n"
                ),
                callback_handler=_unexpected_callback,
                timeout=timeout,
            )

        first = _run_timed(
            phase_timings_ms,
            "exec_step_1",
            _step_one,
        )
        if first.final_artifact is not None:
            raise RuntimeError(
                "Smoke validation finalized too early on the first driver step."
            )

        termination_phase = "exec_step_2"

        def _step_two() -> Any:
            if prompt_handle is None:
                raise RuntimeError("Smoke validation did not create a prompt handle.")
            prompt_slice, prompt_text = session.read_prompt_slice(
                handle_id=prompt_handle.handle_id,
                start_line=1,
                num_lines=2,
                timeout=timeout,
            )
            if "Smoke prompt persistence check." not in prompt_text:
                raise RuntimeError("Prompt handle did not survive across driver turns.")
            if prompt_slice.handle_id != prompt_handle.handle_id:
                raise RuntimeError("Prompt slice returned the wrong handle.")
            return session.execute_code(
                code="counter += 3\nSUBMIT(output=counter)",
                callback_handler=_unexpected_callback,
                timeout=timeout,
            )

        second = _run_timed(
            phase_timings_ms,
            "exec_step_2",
            _step_two,
        )

        artifact = second.final_artifact
        if artifact is None:
            raise RuntimeError("Smoke validation did not produce a final artifact.")

        artifact_value = artifact.get("value")
        if isinstance(artifact_value, dict) and "output" in artifact_value:
            persisted_state_value = artifact_value.get("output")
        else:
            persisted_state_value = artifact_value
        finalization_mode = str(artifact.get("finalization_mode", "unknown"))
        termination_phase = "completed"
    except Exception as exc:
        diagnostic = as_diagnostic_error(exc, phase=termination_phase)
        error_category = diagnostic.category
        error_message = str(diagnostic)
        termination_phase = diagnostic.phase
    finally:
        if session is not None:
            try:
                _run_timed(
                    phase_timings_ms,
                    "cleanup",
                    lambda: session.delete(),
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
        repo_path=repo_path,
        persisted_state_value=persisted_state_value,
        driver_started=driver_started,
        finalization_mode=finalization_mode,
        termination_phase=termination_phase,
        error_category=error_category,
        phase_timings_ms=phase_timings_ms,
        error_message=error_message,
    )
