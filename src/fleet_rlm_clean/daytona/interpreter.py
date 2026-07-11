"""Minimal Daytona-backed code interpreter for dspy.RLM wiring.

Uses ``sandbox.code_interpreter.run_code`` (stateful REPL context), not
``process.code_run`` (stateless per Daytona docs).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Protocol

from fleet_rlm_clean.daytona.errors import (
    DaytonaAdapterError,
    map_provider_error,
    sanitize_provider_message,
)


class InterpreterBackend(Protocol):
    """Narrow execute/close surface; SDK details stay behind this protocol."""

    def run(self, code: str, variables: dict[str, object] | None = None) -> str: ...

    def close(self) -> None: ...


def _assignments_preamble(variables: dict[str, object] | None) -> str:
    if not variables:
        return ""
    return "\n".join(f"{key} = {value!r}" for key, value in variables.items()) + "\n"


class _SandboxCodeInterpreterBackend:
    """Adapter over Daytona ``sandbox.code_interpreter`` (persistent context)."""

    def __init__(self, sandbox: Any) -> None:
        self._sandbox = sandbox
        self._context: Any | None = None

    def _ensure_context(self) -> Any:
        if self._context is None:
            self._context = self._sandbox.code_interpreter.create_context()
        return self._context

    def run(self, code: str, variables: dict[str, object] | None = None) -> str:
        context = self._ensure_context()
        try:
            result = self._sandbox.code_interpreter.run_code(
                _assignments_preamble(variables) + code,
                context=context,
            )
        except Exception as exc:  # noqa: BLE001 - normalize at the SDK boundary
            raise map_provider_error(exc) from exc

        error = getattr(result, "error", None)
        if error is not None:
            raw = f"{getattr(error, 'name', 'Error')}: {getattr(error, 'value', error)}"
            raise DaytonaAdapterError(
                message=sanitize_provider_message(raw),
                cause_type="SandboxCodeInterpreterError",
            )
        stdout = getattr(result, "stdout", None) or ""
        return str(stdout)

    def close(self) -> None:
        # Delete only the lease-owned context. Never delete the Sandbox here.
        if self._context is None:
            return
        context = self._context
        self._context = None
        try:
            self._sandbox.code_interpreter.delete_context(context)
        except Exception as exc:  # noqa: BLE001 - shutdown must stay idempotent
            raise map_provider_error(exc) from exc


class DaytonaCodeInterpreter:
    """CodeInterpreter-compatible adapter with idempotent shutdown."""

    def __init__(
        self,
        *,
        backend: InterpreterBackend | None = None,
        tools: Mapping[str, Callable[..., str]] | None = None,
    ) -> None:
        self._backend = backend
        self._tools: dict[str, Callable[..., str]] = dict(tools or {})
        self._started = False
        self._shutdown = False

    @property
    def tools(self) -> dict[str, Callable[..., str]]:
        return self._tools

    def start(self) -> None:
        if self._shutdown:
            msg = "interpreter already shut down"
            raise DaytonaAdapterError(message=msg, cause_type="InterpreterLifecycleError")
        self._started = True

    def execute(self, code: str, variables: dict[str, Any] | None = None) -> Any:
        if self._shutdown:
            msg = "interpreter already shut down"
            raise DaytonaAdapterError(message=msg, cause_type="InterpreterLifecycleError")
        if not self._started:
            self.start()
        if self._backend is None:
            msg = "interpreter backend is not configured"
            raise DaytonaAdapterError(message=msg, cause_type="InterpreterConfigurationError")
        try:
            return self._backend.run(code, variables)
        except DaytonaAdapterError:
            raise
        except Exception as exc:  # noqa: BLE001 - map all provider failures
            raise map_provider_error(exc) from exc

    def shutdown(self) -> None:
        if self._shutdown:
            return
        self._shutdown = True
        if self._backend is not None:
            self._backend.close()


def sandbox_backend(sandbox: Any) -> InterpreterBackend:
    """Build a stateful backend from a live Daytona sandbox (daytona package only)."""
    return _SandboxCodeInterpreterBackend(sandbox)
