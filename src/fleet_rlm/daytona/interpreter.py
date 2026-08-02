"""Minimal Daytona-backed code interpreter for dspy.RLM wiring.

Uses ``sandbox.code_interpreter.run_code`` (stateful REPL context), not
``process.code_run`` (stateless per Daytona docs).

Host-tool / SUBMIT mediation (B1):
- In-process backends bind host callables directly (offline seam).
- Daytona sandbox backends use an HTTP-in-sandbox broker + host poll
  (Daytona-appropriate channel; mirrors DSPy host-tool + FinalOutput outcomes).
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, cast

import dspy

from fleet_rlm.daytona.errors import (
    DaytonaAdapterError,
    map_provider_error,
    sanitize_provider_message,
)
from fleet_rlm.daytona.http_broker import (
    DEFAULT_BROKER_PORT,
    FleetFinalOutputError,
    build_submit_setup_code,
    extract_final_payload,
)
from fleet_rlm.files.workspace_tools import WorkspaceToolError
from fleet_rlm.observability.turn_tracing import turn_phase_span
from fleet_rlm.rlm.dspy_interpreter_contract import (
    PUBLIC_FINAL_OUTPUT_LABEL,
    copy_output_fields,
    initial_tools_registered,
    is_final_output,
    mark_tools_registered,
    needs_tool_reinjection,
    wrap_final_output,
)
from fleet_rlm.rlm.errors import TurnNoProgressError, TurnTerminalError
from fleet_rlm.rlm.events import ObservationObserver, RLMCode, RLMOutput, StepFinished, StepStarted
from fleet_rlm.rlm.sanitize import sanitize_public_text, truncate_head_tail, truncate_public_text
from fleet_rlm.rlm.tool_observer import ToolEventView, ToolObserver, observe_tool

if TYPE_CHECKING:
    from fleet_rlm.daytona.http_broker import DaytonaHttpToolBroker

DEFAULT_EXECUTION_OUTPUT_CHARS = 4_000
DEFAULT_EXECUTION_TIMEOUT_S = 120
DEFAULT_INTERMEDIATE_CODE_CHARS = 12_000


@dataclass(frozen=True, slots=True)
class BackendExecutionResult:
    """Normalized backend outcome for interpreter finalization."""

    stdout: str = ""
    final: dict[str, Any] | None = None
    error: str | None = None
    stderr: str = ""
    error_category: str | None = None


class InProcessInterpreterBackend:
    """Shared-namespace offline backend for host-tool and SUBMIT contracts."""

    def __init__(self) -> None:
        self.namespace: dict[str, object] = {"_out": ""}
        self.closed = False
        self._host_tools: dict[str, Callable[..., Any]] = {}
        self._submit_key: tuple[tuple[str, str], ...] | None = None

    def bind_host_tools(self, tools: Mapping[str, Callable[..., Any]]) -> None:
        self._host_tools = dict(tools)
        for name, fn in self._host_tools.items():
            self.namespace[name] = self._wrap_host_tool(name, fn)

    def ensure_submit(self, output_fields: list[dict[str, Any]] | None) -> None:
        key = _submit_signature_key(output_fields)
        if key == self._submit_key:
            return
        self.namespace["FleetFinalOutputError"] = FleetFinalOutputError
        self.namespace["_FINAL_OUTPUT_MARKER"] = "__FLEET_FINAL_OUTPUT__"
        self.namespace["_json"] = __import__("json")
        exec(build_submit_setup_code(output_fields), self.namespace, self.namespace)
        self._submit_key = key

    def run(self, code: str, variables: dict[str, object] | None = None) -> BackendExecutionResult:
        if self.closed:
            raise DaytonaAdapterError(message="backend already closed", cause_type="InterpreterLifecycleError")
        if variables:
            self.namespace.update(variables)
        try:
            exec(code, self.namespace, self.namespace)
        except FleetFinalOutputError as final:
            return BackendExecutionResult(stdout=str(self.namespace.get("_out", "")), final=dict(final.value))
        except Exception as exc:
            value = getattr(exc, "value", None)
            if type(exc).__name__ == "FleetFinalOutputError" and isinstance(value, dict):
                return BackendExecutionResult(stdout=str(self.namespace.get("_out", "")), final=dict(value))
            return BackendExecutionResult(
                stdout=str(self.namespace.get("_out", "")),
                error=sanitize_provider_message(str(exc)),
                error_category=type(exc).__name__,
            )
        return BackendExecutionResult(stdout=str(self.namespace.get("_out", "")))

    def close(self) -> None:
        self.closed = True
        self._host_tools.clear()

    @staticmethod
    def _wrap_host_tool(name: str, fn: Callable[..., Any]) -> Callable[..., Any]:
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return fn(*args, **kwargs)
            except Exception as exc:
                raise DaytonaAdapterError(
                    message=sanitize_provider_message(str(exc)),
                    cause_type=type(exc).__name__,
                ) from exc

        wrapper.__name__ = name
        return wrapper


def _submit_signature_key(
    output_fields: list[dict[str, Any]] | None,
) -> tuple[tuple[str, str], ...] | None:
    if not output_fields:
        return None
    normalized = [
        (str(field.get("name") or "").strip(), str(field.get("type") or "").strip())
        for field in output_fields
        if str(field.get("name") or "").strip()
    ]
    return tuple(normalized) or None


class InterpreterBackend(Protocol):
    """Narrow execute/close surface; SDK details stay behind this protocol."""

    def run(self, code: str, variables: dict[str, object] | None = None) -> str | BackendExecutionResult: ...

    def close(self) -> None: ...


class _RepairFeedback(str):
    """Detailed interpreter feedback returned to RLM but not public projection."""

    category: str

    def __new__(cls, value: str, *, category: str = "execution_error") -> _RepairFeedback:
        result = super().__new__(cls, value)
        result.category = category
        return result


def _result_kind(result: Any) -> str:
    """Bounded outcome classification for span metadata (never content)."""
    if is_final_output(result):
        return "final"
    if isinstance(result, _RepairFeedback):
        return "repair_error"
    return "output"


def _repair_category(error: str) -> str:
    """Return a bounded runtime-error category without retaining generated details."""
    prefix = error.split(":", 1)[0].strip()
    allowed = {
        "AttributeError",
        "ImportError",
        "IndexError",
        "KeyError",
        "ModuleNotFoundError",
        "NameError",
        "SyntaxError",
        "TypeError",
        "ValueError",
        "ZeroDivisionError",
    }
    return prefix if prefix in allowed else "execution_error"


def _sync_await(awaitable: Any, loop: asyncio.AbstractEventLoop) -> Any:
    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None
    if current_loop is loop:
        if inspect.iscoroutine(awaitable):
            awaitable.close()
        raise DaytonaAdapterError(
            message="synchronous Daytona bridge called from its owning event loop",
            cause_type="InterpreterThreadError",
        )
    if not inspect.isawaitable(awaitable):
        raise DaytonaAdapterError(
            message="synchronous Daytona bridge requires an async SDK operation",
            cause_type="InterpreterBridgeContractError",
        )
    return asyncio.run_coroutine_threadsafe(awaitable, loop).result()


class _SyncCodeInterpreter:
    def __init__(self, service: Any, loop: asyncio.AbstractEventLoop) -> None:
        self._service = service
        self._loop = loop

    def create_context(self, **kwargs: Any) -> Any:
        return _sync_await(self._service.create_context(**kwargs), self._loop)

    def run_code(self, code: str, **kwargs: Any) -> Any:
        return _sync_await(self._service.run_code(code, **kwargs), self._loop)

    def delete_context(self, context: Any, **kwargs: Any) -> None:
        _sync_await(self._service.delete_context(context, **kwargs), self._loop)


class _SyncProcess:
    def __init__(self, service: Any, loop: asyncio.AbstractEventLoop) -> None:
        self._service = service
        self._loop = loop

    def code_run(self, code: str, **kwargs: Any) -> Any:
        return _sync_await(self._service.code_run(code, **kwargs), self._loop)

    def create_session(self, session_id: str, **kwargs: Any) -> Any:
        return _sync_await(self._service.create_session(session_id, **kwargs), self._loop)

    def execute_session_command(self, session_id: str, request: Any, **kwargs: Any) -> Any:
        return _sync_await(self._service.execute_session_command(session_id, request, **kwargs), self._loop)

    def delete_session(self, session_id: str, **kwargs: Any) -> Any:
        return _sync_await(self._service.delete_session(session_id, **kwargs), self._loop)


class _SyncFileSystem:
    def __init__(self, service: Any, loop: asyncio.AbstractEventLoop) -> None:
        self._service = service
        self._loop = loop

    def upload_file(self, content: bytes, path: str, **kwargs: Any) -> Any:
        return _sync_await(self._service.upload_file(content, path, **kwargs), self._loop)

    def download_file(self, path: str, **kwargs: Any) -> bytes:
        return _sync_await(self._service.download_file(path, **kwargs), self._loop)

    def delete_file(self, path: str, **kwargs: Any) -> Any:
        return _sync_await(self._service.delete_file(path, **kwargs), self._loop)

    def list_files(self, path: str, **kwargs: Any) -> Any:
        return _sync_await(self._service.list_files(path, **kwargs), self._loop)


class _SyncDaytonaSandbox:
    """Explicit synchronous Daytona view used only by DSPy worker execution."""

    def __init__(self, sandbox: Any, loop: asyncio.AbstractEventLoop) -> None:
        if hasattr(sandbox, "code_interpreter"):
            self.code_interpreter = _SyncCodeInterpreter(sandbox.code_interpreter, loop)
        if hasattr(sandbox, "process"):
            self.process = _SyncProcess(sandbox.process, loop)
        if hasattr(sandbox, "fs"):
            self.fs = _SyncFileSystem(sandbox.fs, loop)
        self._sandbox = sandbox
        self._loop = loop

    def get_preview_link(self, port: int, **kwargs: Any) -> Any:
        return _sync_await(self._sandbox.get_preview_link(port, **kwargs), self._loop)


def sync_sandbox(sandbox: Any, loop: asyncio.AbstractEventLoop) -> Any:
    """Return the private synchronous bridge required by DSPy's interpreter port."""
    if isinstance(sandbox, _SyncDaytonaSandbox):
        return sandbox
    return _SyncDaytonaSandbox(sandbox, loop)


def _assignments_preamble(variables: dict[str, object] | None) -> str:
    if not variables:
        return ""
    return "\n".join(f"{key} = {value!r}" for key, value in variables.items()) + "\n"


class _SandboxCodeInterpreterBackend:
    """Adapter over Daytona ``sandbox.code_interpreter`` (persistent context)."""

    def __init__(self, sandbox: Any, *, timeout_s: int | None = None) -> None:
        self._sandbox = sandbox
        self._context: Any | None = None
        if timeout_s is not None and int(timeout_s) <= 0:
            raise DaytonaAdapterError(
                message="execution timeout must be positive",
                cause_type="InterpreterConfigurationError",
            )
        self._timeout_s: int | None = int(timeout_s) if timeout_s is not None else None

    @property
    def sandbox(self) -> Any:
        return self._sandbox

    def _ensure_context(self) -> Any:
        if self._context is None:
            try:
                self._context = self._sandbox.code_interpreter.create_context()
            except Exception as exc:
                raise map_provider_error(exc) from exc
        return self._context

    def run(self, code: str, variables: dict[str, object] | None = None) -> BackendExecutionResult:
        context = self._ensure_context()
        run_kwargs: dict[str, Any] = {"context": context}
        if self._timeout_s is not None:
            run_kwargs["timeout"] = self._timeout_s
        try:
            result = self._sandbox.code_interpreter.run_code(
                _assignments_preamble(variables) + code,
                **run_kwargs,
            )
        except Exception as exc:
            raise map_provider_error(exc) from exc

        stdout = str(getattr(result, "stdout", None) or "")
        stderr = str(getattr(result, "stderr", None) or "")
        final = extract_final_payload(stdout)
        error = getattr(result, "error", None)
        if error is not None:
            error_name = str(getattr(error, "name", "") or "")
            if error_name in {"FleetFinalOutputError", "_FleetFinalOutput"} and final is not None:
                return BackendExecutionResult(stdout=stdout, final=final, stderr=stderr)
            raw = f"{getattr(error, 'name', 'Error')}: {getattr(error, 'value', error)}"
            # User-generated Python errors are part of the RLM feedback loop:
            # return them to DSPy so the next iteration can repair the code.
            # Provider/transport failures still raise above from run_code().
            return BackendExecutionResult(
                stdout=stdout,
                error=sanitize_provider_message(raw),
                stderr=stderr,
                error_category=error_name or None,
            )
        if final is not None:
            return BackendExecutionResult(stdout=stdout, final=final, stderr=stderr)
        return BackendExecutionResult(stdout=stdout, stderr=stderr)

    def close(self) -> None:
        # Delete only the lease-owned context. Never delete the Sandbox here.
        if self._context is None:
            return
        context = self._context
        self._context = None
        try:
            self._sandbox.code_interpreter.delete_context(context)
        except Exception as exc:
            raise map_provider_error(exc) from exc


class DaytonaCodeInterpreter:
    """CodeInterpreter-compatible adapter with host-tool / SUBMIT mediation."""

    def __init__(
        self,
        *,
        backend: InterpreterBackend | None = None,
        tools: Mapping[str, Callable[..., Any]] | None = None,
        output_fields: list[dict[str, Any]] | None = None,
        broker_port: int = DEFAULT_BROKER_PORT,
        execution_output_cap: int = DEFAULT_EXECUTION_OUTPUT_CHARS,
        max_code_chars: int = DEFAULT_INTERMEDIATE_CODE_CHARS,
    ) -> None:
        self._backend = backend
        self._tools: dict[str, Callable[..., Any]] = dict(tools or {})
        self._bound_tools: dict[str, Callable[..., Any]] = {}
        self.output_fields: list[dict[str, Any]] | None = copy_output_fields(output_fields)
        self._tools_registered = initial_tools_registered()
        self._started = False
        self._shutdown = False
        self._broker_port = broker_port
        self._http_broker: DaytonaHttpToolBroker | None = None
        self._observer: ObservationObserver | None = None
        self._observation_max_chars = 10_000
        self._execution_output_cap = max(1, int(execution_output_cap))
        self._max_code_chars = max(1, int(max_code_chars))
        self._observation_step = 0
        self._last_execution: tuple[str, str] | None = None

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
        self._last_execution = None

    def _observe(self, detail: StepStarted | RLMCode | RLMOutput | StepFinished) -> None:
        if self._observer is None:
            return
        try:
            self._observer(detail)
        except Exception:
            return

    def _public_output(self, result: Any) -> str:
        if is_final_output(result):
            return PUBLIC_FINAL_OUTPUT_LABEL
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
                tools[name] = observe_tool(dspy.Tool(fn, name=name), cast(ToolObserver, self._observer), view).func
        return tools

    def execute(self, code: str, variables: dict[str, Any] | None = None) -> Any:
        """
        Execute Python code in the configured interpreter and process its result.

        Parameters:
            code (str): Python code to execute.
            variables (dict[str, Any] | None): Variables made available to the execution.

        Returns:
            Any: Repair feedback for execution problems, a final submission result, or truncated execution output.

        Raises:
            DaytonaAdapterError: If the interpreter is shut down, misconfigured, or the execution provider fails.
            TurnTerminalError: If execution repeats without making progress.
        """
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
        with turn_phase_span(
            "sandbox.execute",
            inputs={
                "iteration": step,
                "code_chars": len(code or ""),
                "variable_count": len(variables or {}),
                "code_preview": sanitize_public_text(
                    truncate_head_tail(code or "", max_chars=900),
                    max_len=900,
                ),
            },
        ) as phase:
            try:
                normalized_code = self._normalize_code(code)
                ensure_bindings_ms = 0
                execute_ms = 0
                bindings_started = time.perf_counter()
                execute_started = time.perf_counter()
                if not normalized_code:
                    result = _RepairFeedback(
                        "[Error] No executable code was provided; execute useful Python or call SUBMIT.",
                        category="empty_code",
                    )
                elif len(normalized_code) > self._max_code_chars:
                    result = _RepairFeedback(
                        f"[Error] Intermediate code is too large ({len(normalized_code)} chars); "
                        f"keep one action under {self._max_code_chars} chars, use variables, and submit promptly.",
                        category="code_too_large",
                    )
                elif self._http_broker is not None:
                    self._ensure_bindings()
                    ensure_bindings_ms = int((time.perf_counter() - bindings_started) * 1_000)
                    result = self._execute_with_http_broker(code, variables)
                else:
                    self._ensure_bindings()
                    ensure_bindings_ms = int((time.perf_counter() - bindings_started) * 1_000)
                    raw = self._backend.run(code, variables)
                    result = self._finalize(raw)
                execute_ms = int((time.perf_counter() - execute_started) * 1_000)
                self._reject_repeated_no_progress(normalized_code, result)
                self._observe(RLMOutput(self._public_output(result), step))
                outputs: dict[str, Any] = {
                    "path": "http_broker" if self._http_broker is not None else type(self._backend).__name__,
                    "result_kind": _result_kind(result),
                    "stdout_chars": len(str(result)),
                    "output_preview": sanitize_public_text(str(result), max_len=900),
                }
                if self._http_broker is not None:
                    outputs["ensure_bindings_ms"] = ensure_bindings_ms
                    outputs["execute_ms"] = execute_ms
                    outputs.update(self._http_broker.last_execution_stats)
                if isinstance(result, _RepairFeedback):
                    outputs["repair_category"] = result.category
                phase.set_outputs(outputs)
                return result
            except TurnTerminalError:
                self._observe(RLMOutput("Execution failed", step))
                raise
            except DaytonaAdapterError:
                self._observe(RLMOutput("Execution failed", step))
                raise
            except Exception as exc:
                mapped = map_provider_error(exc)
                self._observe(RLMOutput("Execution failed", step))
                raise mapped from exc
            finally:
                duration_ms = int((time.perf_counter() - step_started) * 1_000)
                self._observe(StepFinished(step, duration_ms))

    def shutdown(self, *, strict_broker_cleanup: bool = False) -> None:
        """
        Shut down the interpreter and release its broker and backend resources.

        Parameters:
            strict_broker_cleanup (bool): Whether broker cleanup errors should be
                propagated. When false, broker cleanup errors are suppressed.
        """
        if self._shutdown:
            return
        self._shutdown = True
        first_error: BaseException | None = None
        try:
            if self._http_broker is not None:
                broker = self._http_broker
                self._http_broker = None
                if strict_broker_cleanup:
                    try:
                        broker.stop(strict=True)
                    except BaseException as exc:
                        first_error = exc
                else:
                    with contextlib.suppress(Exception):
                        broker.stop()
        finally:
            if self._backend is not None:
                try:
                    self._backend.close()
                except BaseException as exc:
                    first_error = first_error or exc
        if first_error is not None:
            raise first_error

    def _ensure_bindings(self) -> None:
        """
        Ensure execution tools and submission support are available for the configured backend.
        """
        backend = self._backend
        if backend is None:
            return
        tools = self._execution_tools()
        self._bound_tools = tools
        if isinstance(backend, InProcessInterpreterBackend):
            backend.bind_host_tools(tools)
            backend.ensure_submit(self.output_fields)
            self._tools_registered = mark_tools_registered()
            return
        if not isinstance(backend, _SandboxCodeInterpreterBackend):
            self._tools_registered = mark_tools_registered()
            return
        if not needs_tool_reinjection(
            tools_registered=self._tools_registered,
            http_broker_ready=self._http_broker is not None,
        ):
            return
        if self._http_broker is None:
            from fleet_rlm.daytona.http_broker import DaytonaHttpToolBroker

            self._http_broker = DaytonaHttpToolBroker(sandbox=backend.sandbox, broker_port=self._broker_port)
            self._http_broker.ensure_started()
        self._http_broker.register_tools(tools)
        backend.run(self._http_broker.submit_setup_code(self.output_fields))
        self._tools_registered = mark_tools_registered()

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
            except WorkspaceToolError as exc:
                return {
                    "ok": False,
                    "error": exc.code,
                    "message": exc.public_message,
                }
            except Exception as exc:
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
                error = sanitize_provider_message(raw.error)
                if "f-string expression part cannot include a backslash" in error:
                    error = (
                        f"{error}. Build the escaped fragment before the f-string expression, "
                        "then interpolate the variable."
                    )
                feedback = f"[Error] {error}"
                stderr = truncate_head_tail(raw.stderr, max_chars=self._execution_output_cap).strip()
                if stderr:
                    feedback = f"{feedback}\nstderr: {stderr}"
                return _RepairFeedback(feedback, category=raw.error_category or _repair_category(error))
            if raw.final is not None:
                return wrap_final_output(raw.final)
            return truncate_head_tail(raw.stdout, max_chars=self._execution_output_cap)
        final = extract_final_payload(str(raw))
        if final is not None:
            return wrap_final_output(final)
        return truncate_head_tail(str(raw), max_chars=self._execution_output_cap)

    @staticmethod
    def _normalize_code(code: str) -> str:
        return "\n".join(line.rstrip() for line in code.splitlines()).strip()

    def _reject_repeated_no_progress(self, normalized_code: str, result: Any) -> None:
        if is_final_output(result):
            self._last_execution = None
            return
        current = (normalized_code, str(result))
        if current == self._last_execution:
            raise TurnNoProgressError
        self._last_execution = current


def sandbox_backend(
    sandbox: Any,
    *,
    loop: asyncio.AbstractEventLoop | None = None,
    timeout_s: int | None = DEFAULT_EXECUTION_TIMEOUT_S,
) -> InterpreterBackend:
    """Build a stateful backend from a live Daytona sandbox (daytona package only).

    ``timeout_s`` bounds each ``run_code`` call (Daytona's SDK default is ten
    minutes when unset); pass ``None`` to keep the SDK default.
    """
    if loop is not None:
        sandbox = sync_sandbox(sandbox, loop)
    return _SandboxCodeInterpreterBackend(sandbox, timeout_s=timeout_s)
