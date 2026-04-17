"""Admin code execution helpers for Daytona sandboxes."""

from __future__ import annotations

from typing import Any

from .async_compat import _await_if_needed
from .diagnostics import DaytonaDiagnosticError


async def _arun_admin_code(
    *,
    sandbox: Any,
    code: str,
    phase: str,
    error_prefix: str,
    category: str = "sandbox_create_clone_error",
) -> str:
    """Run *code* inside the sandbox via ``sandbox.process.code_run``.

    This is the preferred path for Daytona SDK v0.167.0+ because code
    execution is handled server-side by the daemon.
    """
    try:
        from daytona.common.process import CodeRunParams

        result = await _await_if_needed(
            sandbox.process.code_run(code, params=CodeRunParams())
        )
    except Exception as exc:
        raise DaytonaDiagnosticError(
            f"{error_prefix}: {exc}",
            category=category,
            phase=phase,
        ) from exc

    exit_code = getattr(result, "exit_code", 0)
    if exit_code:
        detail = str(
            getattr(result, "stderr", "")
            or getattr(result, "result", "")
            or getattr(getattr(result, "artifacts", None), "stdout", "")
            or getattr(result, "output", "")
            or f"process exited with status {exit_code}"
        )
        raise DaytonaDiagnosticError(
            f"{error_prefix}: {detail}",
            category=category,
            phase=phase,
        )
    return str(
        getattr(result, "stdout", "")
        or getattr(result, "result", "")
        or getattr(getattr(result, "artifacts", None), "stdout", "")
        or getattr(result, "output", "")
        or ""
    )
