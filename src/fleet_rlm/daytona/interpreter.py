"""Minimal Daytona-backed code interpreter for dspy.RLM wiring.

Uses ``sandbox.code_interpreter.run_code`` (stateful REPL context), not
``process.code_run`` (stateless per Daytona docs).

Host-tool / SUBMIT mediation (B1):
- In-process backends bind host callables directly (offline seam).
- Daytona sandbox backends use an HTTP-in-sandbox broker + host poll
  (Daytona-appropriate channel; mirrors DSPy host-tool + FinalOutput outcomes).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any, Protocol

import dspy
from dspy.primitives.code_interpreter import FinalOutput

from fleet_rlm.daytona.errors import (
    DaytonaAdapterError,
    map_provider_error,
    sanitize_provider_message,
)
from fleet_rlm.daytona.http_broker import DaytonaHttpToolBroker
from fleet_rlm.daytona.in_process import BackendExecutionResult, InProcessInterpreterBackend
from fleet_rlm.daytona.submit import extract_final_payload
from fleet_rlm.rlm.events import ObservationObserver, RLMCode, RLMOutput, StepFinished, StepStarted
from fleet_rlm.rlm.sanitize import truncate_public_text
from fleet_rlm.rlm.tool_observer import ToolEventView, observe_tool


class InterpreterBackend(Protocol):
    """Narrow execute/close surface; SDK details stay behind this protocol."""

    def run(self, code: str, variables: dict[str, object] | None = None) -> str | BackendExecutionResult: ...

    def close(self) -> None: ...


class _RepairFeedback(str):
    """Detailed interpreter feedback returned to RLM but not public projection."""


def _assignments_preamble(variables: dict[str, object] | None) -> str:
    if not variables:
        return ""
    return "\n".join(f"{key} = {value!r}" for key, value in variables.items()) + "\n"


class _SandboxCodeInterpreterBackend:
    """Adapter over Daytona ``sandbox.code_interpreter`` (persistent context)."""

    def __init__(self, sandbox: Any) -> None:
        self._sandbox = sandbox
        self._context: Any | None = None

    @property
    def sandbox(self) -> Any:
        return self._sandbox

    def _ensure_context(self) -> Any:
        if self._context is None:
            self._context = self._sandbox.code_interpreter.create_context()
        return self._context

    def run(self, code: str, variables: dict[str, object] | None = None) -> BackendExecutionResult:
        context = self._ensure_context()
        try:
            result = self._sandbox.code_interpreter.run_code(
                _assignments_preamble(variables) + code,
                context=context,
            )
        except Exception as exc:  # noqa: BLE001 - normalize at the SDK boundary
            raise map_provider_error(exc) from exc

        stdout = str(getattr(result, "stdout", None) or "")
        final = extract_final_payload(stdout)
        error = getattr(result, "error", None)
        if error is not None:
            error_name = str(getattr(error, "name", "") or "")
            if error_name in {"FleetFinalOutput", "_FleetFinalOutput"} and final is not None:
                return BackendExecutionResult(stdout=stdout, final=final)
            raw = f"{getattr(error, 'name', 'Error')}: {getattr(error, 'value', error)}"
            # User-generated Python errors are part of the RLM feedback loop:
            # return them to DSPy so the next iteration can repair the code.
            # Provider/transport failures still raise above from run_code().
            return BackendExecutionResult(stdout=stdout, error=sanitize_provider_message(raw))
        if final is not None:
            return BackendExecutionResult(stdout=stdout, final=final)
        return BackendExecutionResult(stdout=stdout)

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
    """CodeInterpreter-compatible adapter with host-tool / SUBMIT mediation."""

    def __init__(
        self,
        *,
        backend: InterpreterBackend | None = None,
        tools: Mapping[str, Callable[..., Any]] | None = None,
        output_fields: list[dict[str, Any]] | None = None,
    ) -> None:
        self._backend = backend
        self._tools: dict[str, Callable[..., Any]] = dict(tools or {})
        self._bound_tools: dict[str, Callable[..., Any]] = {}
        self.output_fields: list[dict[str, Any]] | None = list(output_fields) if output_fields is not None else None
        self._tools_registered = False
        self._started = False
        self._shutdown = False
        self._http_broker: DaytonaHttpToolBroker | None = None
        self._observer: ObservationObserver | None = None
        self._observation_max_chars = 10_000
        self._observation_step = 0

    @property
    def tools(self) -> dict[str, Callable[..., Any]]:
        return self._tools

    def start(self) -> None:
        if self._shutdown:
            msg = "interpreter already shut down"
            raise DaytonaAdapterError(message=msg, cause_type="InterpreterLifecycleError")
        self._started = True

    def bind_observer(self, observer: ObservationObserver | None, *, max_chars: int = 10_000) -> None:
        """Bind one run-local observer without changing interpreter execution semantics."""
        self._observer = observer
        self._observation_max_chars = max(1, int(max_chars))
        self._observation_step = 0

    def _observe(self, detail: StepStarted | RLMCode | RLMOutput | StepFinished) -> None:
        if self._observer is None:
            return
        try:
            self._observer(detail)
        except Exception:  # noqa: BLE001 - observation must never alter execution
            return

    def _public_output(self, result: Any) -> str:
        if isinstance(result, FinalOutput):
            return "FINAL submitted"
        if isinstance(result, _RepairFeedback):
            return "Execution error"
        return truncate_public_text(str(result or ""), max_len=self._observation_max_chars)

    def _execution_tools(self) -> dict[str, Callable[..., Any]]:
        tools = dict(self._tools)
        if self._observer is None:
            return tools

        def single_input(arguments: Mapping[str, Any]) -> Any:
            prompt = arguments.get("prompt")
            return {"prompt_count": 1, "prompt_chars": len(str(prompt or ""))}

        def batch_input(arguments: Mapping[str, Any]) -> Any:
            raw = arguments.get("prompts")
            prompts = list(raw) if isinstance(raw, (list, tuple)) else []
            return {
                "prompt_count": len(prompts),
                "prompt_chars": sum(len(str(prompt)) for prompt in prompts),
            }

        views = {
            "llm_query": ToolEventView(input_projection=single_input),
            "llm_query_batched": ToolEventView(input_projection=batch_input),
        }
        for name, view in views.items():
            fn = tools.get(name)
            if fn is not None:
                tools[name] = observe_tool(dspy.Tool(fn, name=name), self._observer, view).func
        return tools

    def execute(self, code: str, variables: dict[str, Any] | None = None) -> Any:
        if self._shutdown:
            msg = "interpreter already shut down"
            raise DaytonaAdapterError(message=msg, cause_type="InterpreterLifecycleError")
        if not self._started:
            self.start()
        if self._backend is None:
            msg = "interpreter backend is not configured"
            raise DaytonaAdapterError(message=msg, cause_type="InterpreterConfigurationError")
        self._observation_step += 1
        step = self._observation_step
        step_started = time.perf_counter()
        self._observe(StepStarted(step))
        self._observe(RLMCode(truncate_public_text(code, max_len=self._observation_max_chars), step))
        try:
            self._ensure_bindings()
            if self._http_broker is not None:
                result = self._execute_with_http_broker(code, variables)
            else:
                raw = self._backend.run(code, variables)
                result = self._finalize(raw)
            self._observe(RLMOutput(self._public_output(result), step))
            return result
        except DaytonaAdapterError:
            self._observe(RLMOutput("Execution failed", step))
            raise
        except Exception as exc:  # noqa: BLE001 - map all provider failures
            mapped = map_provider_error(exc)
            self._observe(RLMOutput("Execution failed", step))
            raise mapped from exc
        finally:
            duration_ms = int((time.perf_counter() - step_started) * 1_000)
            self._observe(StepFinished(step, duration_ms))

    def shutdown(self) -> None:
        if self._shutdown:
            return
        self._shutdown = True
        if self._http_broker is not None:
            try:
                self._http_broker.stop()
            except Exception:  # noqa: BLE001 - shutdown must stay idempotent
                pass
            self._http_broker = None
        if self._backend is not None:
            self._backend.close()

    def _ensure_bindings(self) -> None:
        backend = self._backend
        if backend is None:
            return
        tools = self._execution_tools()
        self._bound_tools = tools
        if isinstance(backend, InProcessInterpreterBackend):
            backend.bind_host_tools(tools)
            backend.ensure_submit(self.output_fields)
            self._tools_registered = True
            return
        if not isinstance(backend, _SandboxCodeInterpreterBackend):
            self._tools_registered = True
            return
        # dspy.RLM sets `_tools_registered = False` before each inject so fresh
        # llm_query callables bind; skip only when already registered this cycle.
        if self._tools_registered and self._http_broker is not None:
            return
        if self._http_broker is None:
            self._http_broker = DaytonaHttpToolBroker(sandbox=backend.sandbox)
            self._http_broker.ensure_started()
        self._http_broker.register_tools(tools)
        backend.run(self._http_broker.submit_setup_code(self.output_fields))
        self._tools_registered = True

    def _execute_with_http_broker(
        self,
        code: str,
        variables: dict[str, Any] | None,
    ) -> Any:
        broker = self._http_broker
        backend = self._backend
        if broker is None or backend is None:
            msg = "http broker is not configured"
            raise DaytonaAdapterError(message=msg, cause_type="InterpreterConfigurationError")

        def tool_executor(name: str, args: list[Any], kwargs: dict[str, Any]) -> Any:
            fn = self._bound_tools.get(name)
            if fn is None:
                msg = f"unknown tool: {name}"
                raise DaytonaAdapterError(message=msg, cause_type="UnknownToolError")
            try:
                return fn(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 - sanitize tool failures
                raise DaytonaAdapterError(
                    message=sanitize_provider_message(str(exc)),
                    cause_type=type(exc).__name__,
                ) from exc

        raw = broker.execute_with_callbacks(
            run_code=lambda: backend.run(code, variables),
            tool_executor=tool_executor,
        )
        return self._finalize(raw)

    def _finalize(self, raw: str | BackendExecutionResult) -> Any:
        if isinstance(raw, BackendExecutionResult):
            if raw.error:
                return _RepairFeedback(f"[Error] {sanitize_provider_message(raw.error)}")
            if raw.final is not None:
                return FinalOutput(raw.final)
            return raw.stdout
        final = extract_final_payload(str(raw))
        if final is not None:
            return FinalOutput(final)
        return raw


def sandbox_backend(sandbox: Any) -> InterpreterBackend:
    """Build a stateful backend from a live Daytona sandbox (daytona package only)."""
    return _SandboxCodeInterpreterBackend(sandbox)
