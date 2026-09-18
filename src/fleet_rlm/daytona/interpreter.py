"""Minimal Daytona-backed code interpreter for dspy.RLM wiring.

Executes Python actions either in-process (offline) or within an AsyncDaytona sandbox
via process execution or code interpreter capability. Handles SUBMIT final outputs,
host tools, observation events, and execution budget capping.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import inspect
import io
import json
import logging
import shlex
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Protocol, cast
from uuid import uuid4

import dspy
from dspy.utils.callback import BaseCallback, with_callbacks

from fleet_rlm.daytona.errors import (
    DaytonaAdapterError,
    map_provider_error,
    sanitize_provider_message,
)
from fleet_rlm.daytona.models import (
    FINAL_OUTPUT_MARKER,
    FleetFinalOutputError,
    build_submit_setup_code,
    extract_final_payload,
    final_output_frame,
)
from fleet_rlm.daytona.sync_bridge import (
    SyncBridgeDispatcher,
    sync_sandbox,
)
from fleet_rlm.observability.tracing import trace_preview_limit, turn_phase_span
from fleet_rlm.rlm.budget import BudgetDimension, TurnBudget, TurnBudgetExhausted
from fleet_rlm.rlm.compat_3_3_1 import (
    PUBLIC_FINAL_OUTPUT_LABEL,
    CodeExecutionError,
    CodeInterpreterError,
    is_final_output,
    needs_binding_refresh,
    wrap_final_output,
)
from fleet_rlm.rlm.events import (
    ObservationObserver,
    RLMCode,
    RLMOutput,
    StepFinished,
    StepStarted,
    ToolEventView,
    ToolObserver,
    observe_tool,
)
from fleet_rlm.rlm.output_contract import FleetOutputContract
from fleet_rlm.rlm.result import (
    RunNoProgressError,
    RunTerminalError,
    sanitize_repair_text,
    sanitize_trace_text,
    truncate_head_tail,
    truncate_public_text,
)

logger = logging.getLogger(__name__)

DEFAULT_EXECUTION_OUTPUT_CHARS = 4_000
DEFAULT_EXECUTION_TIMEOUT_S = 120
DEFAULT_INTERMEDIATE_CODE_CHARS = 12_000
DEFAULT_BROKER_PORT = 8765
_MAX_CAPTURED_OUTPUT_CHARS = 64 * 1024
_UNSET = object()
_BINDING_RESERVATION: contextvars.ContextVar[object | None] = contextvars.ContextVar(
    "fleet_interpreter_binding_reservation",
    default=None,
)


OutputCallback = Callable[[str], None]


class _PublicStdoutProjector:
    """Forward ordinary stdout while hiding the known SUBMIT stdout frame."""

    def __init__(self, emit: OutputCallback) -> None:
        self._emit = emit
        self._marker = FINAL_OUTPUT_MARKER
        self._buffer = ""

    def feed(self, value: str) -> None:
        if not value:
            return
        pending = self._buffer + value
        self._buffer = ""
        start = pending.find(self._marker)
        if start >= 0:
            if start:
                self._emit(pending[:start])
            self._buffer = pending[start:]
            return
        suffix = self._marker_prefix_suffix(pending)
        if suffix:
            self._emit(pending[: -len(suffix)])
            self._buffer = suffix
        else:
            self._emit(pending)

    def finish(self, *, expected_final: Mapping[str, Any] | None = None) -> None:
        pending = self._buffer
        self._buffer = ""
        if not pending:
            return
        if expected_final is None:
            self._emit(pending)
            return

        frame = final_output_frame(expected_final, marker=self._marker)
        offset = 0
        while True:
            start = pending.find(frame, offset)
            if start < 0:
                self._emit(pending[offset:])
                return
            self._emit(pending[offset:start])
            offset = start + len(frame)
            if pending.startswith("\r\n", offset):
                offset += 2
            elif pending.startswith(("\n", "\r"), offset):
                offset += 1

    def _marker_prefix_suffix(self, value: str) -> str:
        for length in range(min(len(value), len(self._marker) - 1), 0, -1):
            if value.endswith(self._marker[:length]):
                return value[-length:]
        return ""


@dataclass(slots=True)
class _OutputStreamState:
    """Per-step public output-stream tracking."""

    emitted_chars: int = 0
    streamed_chunks: list[str] = field(default_factory=list)
    closed: bool = False


def _emit_output_delta(
    value: str,
    *,
    step: int,
    stream_id: str,
    state: _OutputStreamState,
    max_chars: int,
    observe: Callable[[RLMOutput], None],
) -> None:
    if state.closed or not value:
        return
    remaining = max_chars - state.emitted_chars
    if remaining <= 0:
        return
    chunk = value[:remaining]
    state.emitted_chars += len(chunk)
    if chunk:
        state.streamed_chunks.append(chunk)
        observe(RLMOutput(chunk, step, stream_id, True, False))


def _close_output_stream(
    text: str,
    *,
    step: int,
    stream_id: str,
    state: _OutputStreamState,
    observe: Callable[[RLMOutput], None],
) -> None:
    state.closed = True
    observe(RLMOutput(text, step, stream_id, False, True))


def _flush_step_output(
    result: Any,
    *,
    step: int,
    stream_id: str,
    state: _OutputStreamState,
    public_output: Callable[[Any], str],
    observe: Callable[[RLMOutput], None],
) -> None:
    if state.closed:
        return
    public = public_output(result)
    if is_final_output(result):
        _close_output_stream(public, step=step, stream_id=stream_id, state=state, observe=observe)
        return
    streamed = "".join(state.streamed_chunks)
    if public == streamed:
        state.closed = True
        return
    if public.startswith(streamed):
        tail = public[len(streamed) :]
        state.closed = True
        observe(RLMOutput(tail, step, stream_id, True, True))
        return
    _close_output_stream(public, step=step, stream_id=stream_id, state=state, observe=observe)


@dataclass(frozen=True, slots=True)
class BackendExecutionResult:
    """Normalized backend outcome for interpreter finalization."""

    stdout: str = ""
    final: dict[str, Any] | None = None
    error: str | None = None
    stderr: str = ""
    error_category: str | None = None
    context_accesses: tuple[str, ...] = ()


class _StreamingTextBuffer(io.StringIO):
    """Capture interpreter text while forwarding each write to an observer."""

    def __init__(self, callback: OutputCallback | None = None) -> None:
        super().__init__()
        self._callback = callback

    def write(self, value: str) -> int:
        remaining = _MAX_CAPTURED_OUTPUT_CHARS - self.tell()
        if remaining > 0:
            super().write(value[:remaining])
        if value and self._callback is not None:
            self._callback(value)
        return len(value)


def _combine_stdout(captured: str, legacy: object) -> str:
    legacy_text = str(legacy or "")
    if captured and legacy_text and legacy_text not in captured:
        return f"{captured.rstrip()}\n{legacy_text}"
    return captured or legacy_text


def _submitted_payload(result: Any) -> Mapping[str, Any] | None:
    if is_final_output(result) and hasattr(result, "output"):
        output = result.output
        if isinstance(output, Mapping):
            return output
    return None


class _BindingTools(dict[str, Callable[..., Any]]):
    """Invocation tool map that marks binding state dirty on mutation."""

    def __init__(
        self,
        owner: DaytonaCodeInterpreter,
        initial: Mapping[str, Callable[..., Any]] | None = None,
    ) -> None:
        self._owner = owner
        super().__init__(initial or {})

    def __setitem__(self, key: str, value: Callable[..., Any]) -> None:
        self._owner._begin_binding_injection()
        self._owner._ensure_binding_mutation_allowed()
        super().__setitem__(key, value)
        self._owner._binding_generation += 1

    def __delitem__(self, key: str) -> None:
        self._owner._ensure_binding_mutation_allowed()
        super().__delitem__(key)
        self._owner._binding_generation += 1

    def clear(self) -> None:
        self._owner._ensure_binding_mutation_allowed()
        super().clear()
        self._owner._binding_generation += 1

    def update(self, *args: Any, **kwargs: Any) -> None:
        self._owner._begin_binding_injection()
        self._owner._ensure_binding_mutation_allowed()
        super().clear()
        super().update(*args, **kwargs)
        self._owner._binding_generation += 1


class InProcessInterpreterBackend:
    """Shared-namespace offline backend for host-tool and SUBMIT contracts."""

    def __init__(self) -> None:
        self.namespace: dict[str, object] = {"_out": "", "context": []}
        self.closed = False
        self._host_tools: dict[str, Callable[..., Any]] = {}
        self._bound_tool_names: set[str] = set()
        self._submit_key: object = _UNSET
        self._context_accesses: list[str] = []
        self._context_binding: tuple[str, str] | None = None

        def load_context(raw_manifest: bytes | str) -> list[dict[str, Any]]:
            from fleet_rlm.rlm.program import _materialize_context_manifest

            binding = self._context_binding
            if binding is None:
                raise ValueError("context manifest is not host bound")
            values, accesses = _materialize_context_manifest(
                raw_manifest,
                trusted_mount_root=binding[0],
                expected_manifest_sha256=binding[1],
            )
            self._context_accesses.extend(accesses)
            if len(values) == 1 and values[0]["encoding"] == "utf-8":
                self.namespace["context"] = values[0]["data"]
            else:
                self.namespace["context"] = []
            return values

        self.namespace["_fleet_load_context_manifest"] = load_context

    def bind_context_manifest(self, *, trusted_mount_root: str, expected_manifest_sha256: str) -> None:
        binding = (str(trusted_mount_root), str(expected_manifest_sha256))
        if self._context_binding is not None and self._context_binding != binding:
            raise DaytonaAdapterError(
                message="context manifest binding cannot be replaced",
                cause_type="ContextIntegrityError",
            )
        self._context_binding = binding

    def bind_host_tools(self, tools: Mapping[str, Callable[..., Any]]) -> None:
        for name in self._bound_tool_names.difference(tools):
            self.namespace.pop(name, None)
        self._host_tools = dict(tools)
        self._bound_tool_names = set(tools)
        for name, fn in self._host_tools.items():
            self.namespace[name] = self._wrap_host_tool(name, fn)

    def ensure_submit(self, output_fields: list[dict[str, Any]] | None) -> None:
        key = repr(output_fields)
        if key == self._submit_key:
            return
        self.namespace["FleetFinalOutputError"] = FleetFinalOutputError
        self.namespace["FINAL_OUTPUT_MARKER"] = FINAL_OUTPUT_MARKER
        self.namespace["json"] = __import__("json")
        self.namespace["_json"] = self.namespace["json"]
        exec(build_submit_setup_code(output_fields), self.namespace, self.namespace)
        self._submit_key = key

    def run(
        self,
        code: str,
        variables: dict[str, object] | None = None,
        *,
        on_stdout: OutputCallback | None = None,
    ) -> BackendExecutionResult:
        if self.closed:
            raise DaytonaAdapterError(message="backend already closed", cause_type="InterpreterLifecycleError")
        if variables:
            self.namespace.update(variables)
        stdout = _StreamingTextBuffer(on_stdout)

        def _make_result(
            *, final: dict[str, Any] | None = None, error: str | None = None, category: str | None = None
        ) -> BackendExecutionResult:
            return BackendExecutionResult(
                stdout=_combine_stdout(stdout.getvalue(), self.namespace.get("_out", "")),
                final=final,
                error=error,
                error_category=category,
                context_accesses=self._drain_context_accesses(),
            )

        with contextlib.redirect_stdout(stdout):
            try:
                exec(code, self.namespace, self.namespace)
            except FleetFinalOutputError as final:
                return _make_result(final=dict(final.value))
            except Exception as exc:
                value = getattr(exc, "value", None)
                if type(exc).__name__ == "FleetFinalOutputError" and isinstance(value, dict):
                    return _make_result(final=dict(value))
                return _make_result(
                    error=sanitize_provider_message(str(exc)),
                    category=type(exc).__name__,
                )
        return _make_result()

    def _drain_context_accesses(self) -> tuple[str, ...]:
        values = tuple(self._context_accesses)
        self._context_accesses.clear()
        return values

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


class InterpreterBackend(Protocol):
    """Protocol satisfied by interpreter execution backends."""

    def run(self, code: str, variables: dict[str, object] | None = None) -> str | BackendExecutionResult: ...
    def close(self) -> None: ...


def _result_kind(result: Any) -> str:
    if is_final_output(result):
        return "final_output"
    return "output"


def _repair_error(message: str, *, category: str = "execution_error") -> CodeExecutionError:
    err = CodeExecutionError(message)
    object.__setattr__(err, "category", category)
    return err


def _terminal_error(message: str, *, category: str = "CodeInterpreterError") -> CodeInterpreterError:
    err = CodeInterpreterError(message)
    object.__setattr__(err, "category", category)
    return err


@dataclass(frozen=True)
class _RepairFeedback:
    feedback: str
    category: str


def _repair_category(error: str) -> str:
    lines = [line.strip() for line in error.strip().splitlines() if line.strip()]
    if not lines:
        return "execution_error"
    for line in reversed(lines):
        if ":" in line:
            candidate = line.split(":", 1)[0].strip()
            if candidate.isidentifier():
                return candidate
        elif line.isidentifier() and line.endswith("Error"):
            return line
    return "execution_error"


def is_host_setup_action(code: str) -> bool:
    return "_fleet_load_context_manifest" in code


class _SandboxProcessBackend:
    """Execute Python code in a live Daytona sandbox via process or code interpreter."""

    def __init__(
        self,
        sandbox: Any,
        *,
        timeout_s: int | None = None,
        workdir: str = "/workspace",
    ) -> None:
        self._sandbox = sandbox
        if timeout_s is not None and int(timeout_s) <= 0:
            raise DaytonaAdapterError(
                message="execution timeout must be positive",
                cause_type="InterpreterConfigurationError",
            )
        self._timeout_s: int | None = int(timeout_s) if timeout_s is not None else None
        self._workdir = workdir
        self._output_fields: list[dict[str, Any]] | None = None
        self._bound_tools: dict[str, Callable[..., Any]] = {}
        self._context_binding: tuple[str, str] | None = None
        self._context_accesses: list[str] = []
        self._closed = False
        self._interpreter_context: Any = None

    @property
    def sandbox(self) -> Any:
        return self._sandbox

    @property
    def timeout_s(self) -> int | None:
        return self._timeout_s

    def bind_host_tools(self, tools: Mapping[str, Callable[..., Any]]) -> None:
        self._bound_tools = dict(tools)

    def ensure_submit(self, output_fields: list[dict[str, Any]] | None) -> None:
        self._output_fields = output_fields

    def bind_context_manifest(self, *, trusted_mount_root: str, expected_manifest_sha256: str) -> None:
        binding = (str(trusted_mount_root), str(expected_manifest_sha256))
        if self._context_binding is not None and self._context_binding != binding:
            raise DaytonaAdapterError(
                message="context manifest binding cannot be replaced",
                cause_type="ContextIntegrityError",
            )
        self._context_binding = binding

    def run(
        self,
        code: str,
        variables: dict[str, object] | None = None,
        *,
        on_stdout: OutputCallback | None = None,
    ) -> BackendExecutionResult:
        if self._closed:
            raise DaytonaAdapterError(message="backend already closed", cause_type="InterpreterLifecycleError")

        submit_code = build_submit_setup_code(self._output_fields)

        var_lines: list[str] = []
        if variables:
            for k, v in variables.items():
                if isinstance(v, (str, int, float, bool, list, dict)) or v is None:
                    var_lines.append(f"{k} = {json.dumps(v)}")
        var_code = ("\n".join(var_lines) + "\n") if var_lines else ""

        context_lines = ["if 'context' not in globals(): context = []"]
        if self._context_binding is not None:
            mount_root, manifest_sha = self._context_binding
            context_lines.append(f"""
import hashlib as _hashlib
import json as _json_ctx
import os as _os_ctx

_CONTEXT_MOUNT_ROOT = {mount_root!r}
_CONTEXT_MANIFEST_SHA256 = {manifest_sha!r}

def _fleet_load_context_manifest(raw_manifest):
    if isinstance(raw_manifest, str):
        raw_manifest = raw_manifest.encode("utf-8")
    if _hashlib.sha256(bytes(raw_manifest)).hexdigest() != _CONTEXT_MANIFEST_SHA256:
        raise ValueError("manifest checksum mismatch")
    manifest = _json_ctx.loads(bytes(raw_manifest).decode("utf-8"))
    mount_root = _os_ctx.path.realpath(str(_CONTEXT_MOUNT_ROOT))
    values = []
    for entry in manifest.get("entries", []):
        path = _os_ctx.path.realpath(str(entry["sandbox_path"]))
        expected_size = int(entry["byte_size"])
        expected_sha = str(entry["checksum_sha256"])
        with open(path, "rb") as f:
            data = f.read(expected_size + 1)
        if len(data) != expected_size or _hashlib.sha256(data).hexdigest() != expected_sha:
            raise ValueError("attachment checksum mismatch")
        enc = entry.get("encoding", "utf-8")
        att_id = entry.get("attachment_id")
        if enc == "utf-8":
            values.append({{"data": data.decode("utf-8"), "encoding": "utf-8", "attachment_id": att_id}})
        else:
            values.append({{"data": data, "encoding": "bytes", "attachment_id": att_id}})
    return values
""")
        context_code = "\n".join(context_lines) + "\n"
        full_code = f"{submit_code}\n\n{context_code}\n{var_code}\n{code}"

        timeout = self._timeout_s or DEFAULT_EXECUTION_TIMEOUT_S
        stdout = ""
        stderr = ""
        exit_code = 0
        exec_error: Any = None

        sandbox = self._sandbox
        try:
            if hasattr(sandbox, "code_interpreter") and hasattr(sandbox.code_interpreter, "run_code"):
                kwargs: dict[str, Any] = {"timeout": timeout}
                if self._interpreter_context is None and hasattr(sandbox.code_interpreter, "create_context"):
                    with contextlib.suppress(Exception):
                        self._interpreter_context = sandbox.code_interpreter.create_context()
                if self._interpreter_context is not None:
                    kwargs["context"] = self._interpreter_context
                if on_stdout is not None:

                    def _stream_stdout(msg: Any) -> None:
                        chunk = getattr(msg, "output", getattr(msg, "text", str(msg)))
                        if chunk:
                            on_stdout(chunk)

                    kwargs["on_stdout"] = _stream_stdout

                res = sandbox.code_interpreter.run_code(full_code, **kwargs)
                stdout = getattr(res, "stdout", "") or ""
                stderr = getattr(res, "stderr", "") or ""
                exec_error = getattr(res, "error", None)
                exit_code = 0 if not exec_error else 1

            elif hasattr(sandbox, "process"):
                process = sandbox.process
                if hasattr(process, "code_run"):
                    res = process.code_run(full_code, timeout=timeout)
                    stdout = getattr(res, "result", "") or ""
                    exit_code = getattr(res, "exit_code", 0)
                elif hasattr(process, "exec"):
                    res = process.exec(
                        f"python3 -c {shlex.quote(full_code)}",
                        timeout=timeout,
                        cwd=self._workdir,
                    )
                    stdout = getattr(res, "result", "") or ""
                    exit_code = getattr(res, "exit_code", 0)
                else:
                    raise DaytonaAdapterError(
                        message="Sandbox process has no code_run or exec capability",
                        cause_type="InterpreterConfigurationError",
                    )
            else:
                raise DaytonaAdapterError(
                    message="Sandbox has neither code_interpreter nor process execution capability",
                    cause_type="InterpreterConfigurationError",
                )
        except Exception as exc:
            if isinstance(exc, DaytonaAdapterError):
                raise
            mapped = map_provider_error(exc)
            raise mapped from exc

        if on_stdout is not None and stdout:
            on_stdout(stdout)

        final = extract_final_payload(stdout)
        err_msg: str | None = None
        category: str | None = None
        if final is None and (exit_code != 0 or exec_error):
            err_msg = str(exec_error) if exec_error else (stderr or stdout or "Execution failed")
            category = _repair_category(err_msg)
        return BackendExecutionResult(
            stdout=stdout,
            stderr=stderr,
            final=final,
            error=err_msg,
            error_category=category,
            context_accesses=self._drain_context_accesses(),
        )

    def _drain_context_accesses(self) -> tuple[str, ...]:
        values = tuple(self._context_accesses)
        self._context_accesses.clear()
        return values

    def close(self) -> None:
        self._closed = True
        ctx = self._interpreter_context
        self._interpreter_context = None
        if ctx is not None and hasattr(self._sandbox, "code_interpreter"):
            ci = self._sandbox.code_interpreter
            if hasattr(ci, "delete_context") and callable(ci.delete_context):
                with contextlib.suppress(Exception):
                    ci.delete_context(ctx)


class DaytonaCodeInterpreter:
    """CodeInterpreter-compatible adapter with host-tool / SUBMIT mediation."""

    def __init__(
        self,
        *,
        backend: InterpreterBackend | None = None,
        tools: Mapping[str, Callable[..., Any]] | None = None,
        output_fields: list[dict[str, Any]] | None = None,
        callbacks: list[BaseCallback] | None = None,
        broker_port: int = DEFAULT_BROKER_PORT,
        execution_output_cap: int = DEFAULT_EXECUTION_OUTPUT_CHARS,
        max_code_chars: int = DEFAULT_INTERMEDIATE_CODE_CHARS,
    ) -> None:
        self._backend = backend
        self.callbacks = list(callbacks or [])
        self._binding_generation = 0
        self._installed_binding_generation = -1
        self._execution_lock = Lock()
        self._shutdown_lock = Lock()
        self._reservation_token: object | None = None
        self._reservation_task: asyncio.Task[Any] | None = None
        self._execution_started: bool = False
        self._reservation_state_lock = Lock()
        self._tools: _BindingTools = _BindingTools(self, tools)
        self._bound_tools: dict[str, Callable[..., Any]] = {}
        self._fleet_output_contract: FleetOutputContract | None = None
        self._output_fields: list[dict[str, Any]] | None = None
        self.output_fields = output_fields
        self._started = False
        self._shutdown = False
        self._broker_port = broker_port
        self._http_broker: Any | None = None
        self._observer: ObservationObserver | None = None
        self._observation_max_chars = 10_000
        self._turn_budget: TurnBudget | None = None
        self._turn_request: str | None = None
        self._output_budget_exhausted = False
        self._execution_output_cap = max(1, int(execution_output_cap))
        self._max_code_chars = max(1, int(max_code_chars))
        self._observation_step = 0
        self._public_observation = True
        self._observation_namespace = uuid4().hex
        self._last_execution: tuple[str, str] | None = None
        self._no_progress_repair_used = False
        self._context_accesses: list[str] = []
        self._context_binding: tuple[str, str] | None = None

    def _ensure_binding_mutation_allowed(self) -> None:
        """Reject an overlapping invocation before it can mutate the current namespace."""
        current = _BINDING_RESERVATION.get()
        with self._reservation_state_lock:
            allowed = not self._execution_lock.locked() or (
                self._reservation_token is current and current is not None and not self._execution_started
            )
        if not allowed:
            raise DaytonaAdapterError(
                message="interpreter is already executing",
                cause_type="InterpreterReuseError",
            )

    def _begin_binding_injection(self) -> None:
        """Reserve this interpreter before DSPy starts an overlapping acall."""
        try:
            task = asyncio.current_task()
        except RuntimeError:
            task = None
        if task is None:
            return
        current = _BINDING_RESERVATION.get()
        with self._reservation_state_lock:
            if self._reservation_token is current and current is not None and not self._execution_started:
                return
            if not self._execution_lock.acquire(blocking=False):
                raise DaytonaAdapterError(
                    message="interpreter is already executing",
                    cause_type="InterpreterReuseError",
                )
            token = object()
            self._reservation_token = token
            self._reservation_task = task
            self._execution_started = False
            _BINDING_RESERVATION.set(token)
        task.add_done_callback(lambda _done, token=token: self._release_reservation(token))

    def _release_reservation(self, token: object) -> None:
        """Release a pre-execution reservation when an async call settles early."""
        clear_context = False
        with self._reservation_state_lock:
            if token is not self._reservation_token or self._execution_started:
                return
            self._reservation_token = None
            self._reservation_task = None
            if self._execution_lock.locked():
                self._execution_lock.release()
            clear_context = _BINDING_RESERVATION.get() is token
        if clear_context:
            _BINDING_RESERVATION.set(None)

    def _acquire_execution(self) -> object:
        """Consume an injection reservation or acquire one for direct execution."""
        current = _BINDING_RESERVATION.get()
        with self._reservation_state_lock:
            if self._reservation_token is current and current is not None and not self._execution_started:
                self._execution_started = True
                return current
            if not self._execution_lock.acquire(blocking=False):
                raise DaytonaAdapterError(
                    message="interpreter is already executing",
                    cause_type="InterpreterReuseError",
                )
            token = object()
            self._reservation_token = token
            try:
                task = asyncio.current_task()
            except RuntimeError:
                task = None
            self._reservation_task = task
            self._execution_started = True
            _BINDING_RESERVATION.set(token)
            if task is not None:
                task.add_done_callback(lambda _done, token=token: self._release_reservation(token))
            return token

    def _release_execution(self, token: object) -> None:
        """Release the execution lease after backend output and callbacks settle."""
        clear_context = False
        with self._reservation_state_lock:
            if token is not self._reservation_token:
                return
            self._execution_started = False
            self._reservation_task = None
            self._reservation_token = None
            if self._execution_lock.locked():
                self._execution_lock.release()
            clear_context = _BINDING_RESERVATION.get() is token
        if clear_context:
            _BINDING_RESERVATION.set(None)

    @property
    def tools(self) -> dict[str, Callable[..., Any]]:
        return self._tools

    @property
    def supports_sandbox_serializable_inputs(self) -> bool:
        return True

    @property
    def broker(self) -> Any:
        return self._http_broker

    @property
    def output_fields(self) -> list[dict[str, Any]] | None:
        return self._output_fields

    @output_fields.setter
    def output_fields(self, value: list[dict[str, Any]] | None) -> None:
        self._ensure_binding_mutation_allowed()
        if value is not None and self._fleet_output_contract is not None:
            value = self._fleet_output_contract.merge(value)
        self._output_fields = value
        self._binding_generation += 1

    def bind_output_contract(self, contract: FleetOutputContract) -> None:
        self._fleet_output_contract = contract
        if self._output_fields is not None:
            self.output_fields = contract.merge(self._output_fields)

    @with_callbacks
    def start(self) -> None:
        if self._shutdown:
            raise DaytonaAdapterError(message="interpreter already shut down", cause_type="InterpreterLifecycleError")
        self._started = True

    def bind_observer(self, observer: ObservationObserver | None, *, max_chars: int = 10_000) -> None:
        self._ensure_binding_mutation_allowed()
        self._observer = observer
        self._observation_max_chars = max(1, int(max_chars))

    def bind_turn_budget(self, budget: TurnBudget | None) -> None:
        self._ensure_binding_mutation_allowed()
        self._turn_budget = budget
        self._output_budget_exhausted = False

    def bind_turn_request(self, request: str | None) -> None:
        self._turn_request = request

    def bind_context_capsule(self, capsule: Any) -> None:
        self._ensure_binding_mutation_allowed()
        from fleet_rlm.rlm.program import AttachmentContextCapsule

        if not isinstance(capsule, AttachmentContextCapsule):
            raise DaytonaAdapterError(
                message="context capsule is invalid",
                cause_type="ContextIntegrityError",
            )
        raw_manifest = capsule.to_sandbox()
        binding = (capsule.mount_root, str(hashlib_sha256(raw_manifest)))
        if self._context_binding is not None and self._context_binding != binding:
            raise DaytonaAdapterError(
                message="context manifest binding cannot be replaced",
                cause_type="ContextIntegrityError",
            )
        bind_backend = getattr(self._backend, "bind_context_manifest", None)
        if callable(bind_backend):
            bind_backend(
                trusted_mount_root=binding[0],
                expected_manifest_sha256=binding[1],
            )
        self._context_binding = binding

    def _observe(self, detail: StepStarted | RLMCode | RLMOutput | StepFinished) -> None:
        if not self._public_observation:
            return
        if isinstance(detail, RLMOutput) and detail.output and self._turn_budget is not None:
            if self._output_budget_exhausted:
                return
            try:
                self._turn_budget.reserve(
                    BudgetDimension.EXECUTION_OUTPUT_BYTES,
                    len(detail.output.encode("utf-8")),
                )
            except TurnBudgetExhausted:
                self._output_budget_exhausted = True
                raise
        if self._observer is None:
            return
        try:
            self._observer(detail)
        except Exception:
            return

    def _public_output(self, result: Any) -> str:
        if is_final_output(result):
            return PUBLIC_FINAL_OUTPUT_LABEL
        if isinstance(result, CodeInterpreterError):
            return "Execution failed"
        if isinstance(result, CodeExecutionError):
            return "Execution error"
        return truncate_public_text(str(result or ""), max_len=self._observation_max_chars)

    def _run_backend(
        self,
        code: str,
        variables: dict[str, Any] | None,
        *,
        on_stdout: OutputCallback,
    ) -> str | BackendExecutionResult:
        backend = self._backend
        if backend is None:
            raise DaytonaAdapterError(
                message="interpreter backend is not configured", cause_type="InterpreterConfigurationError"
            )
        run = cast(Callable[..., str | BackendExecutionResult], backend.run)
        try:
            return run(code, variables, on_stdout=on_stdout)  # type: ignore[call-arg]
        except TypeError:
            return run(code, variables)

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

    @with_callbacks
    def execute(self, code: str, variables: dict[str, Any] | None = None) -> Any:
        """Execute one action under single-flight concurrency protection."""
        token = self._acquire_execution()
        try:
            return self._execute_once(code, variables)
        finally:
            self._release_execution(token)

    def _execute_once(self, code: str, variables: dict[str, Any] | None = None) -> Any:
        if self._shutdown:
            raise DaytonaAdapterError(
                message="DaytonaCodeInterpreter has been shut down",
                cause_type="InterpreterLifecycleError",
            )
        if self._broker_port == 0 and bool(self._execution_tools()):
            raise DaytonaAdapterError(
                message="brokerless mode cannot dispatch host tools",
                cause_type="BrokerlessToolDispatchError",
            )
        if not self._started:
            self.start()
        if self._backend is None:
            raise DaytonaAdapterError(
                message="interpreter backend is not configured", cause_type="InterpreterConfigurationError"
            )

        public = not is_host_setup_action(code)
        self._public_observation = public
        if public:
            self._observation_step += 1
        step = self._observation_step
        output_stream_id = f"interpreter:{self._observation_namespace}:output:{step}"
        output_state = _OutputStreamState()
        stdout_projector = _PublicStdoutProjector(
            lambda value: _emit_output_delta(
                value,
                step=step,
                stream_id=output_stream_id,
                state=output_state,
                max_chars=self._observation_max_chars,
                observe=self._observe,
            )
        )
        step_started = time.perf_counter()
        trace_chars = trace_preview_limit(900)
        self._observe(StepStarted(step))
        self._observe(RLMCode(truncate_public_text(code, max_len=self._observation_max_chars), step))

        with turn_phase_span(
            "sandbox.execute",
            inputs={
                "iteration": step,
                "code_chars": len(code or ""),
                "variable_count": len(variables or {}),
                "code_preview": sanitize_trace_text(
                    truncate_head_tail(code or "", max_chars=trace_chars),
                    max_len=trace_chars,
                ),
            },
        ) as phase:

            def _fail_step(
                label: str,
                category: str,
                *,
                recovered: bool = False,
                outputs: dict[str, Any] | None = None,
            ) -> None:
                stdout_projector.finish()
                _close_output_stream(
                    label,
                    step=step,
                    stream_id=output_stream_id,
                    state=output_state,
                    observe=self._observe,
                )
                res_outputs = {"failure_category": category}
                if outputs:
                    res_outputs.update(outputs)
                attrs = {"failure_category": category}
                if recovered:
                    attrs["recovered"] = True
                phase.finish(phase_status="failed", outputs=res_outputs, attributes=attrs)

            try:
                normalized_code = "\n".join(line.rstrip() for line in code.splitlines()).strip()
                bindings_started = time.perf_counter()
                execute_started = time.perf_counter()

                if not normalized_code:
                    repair = _repair_error(
                        "No executable code was provided; execute useful Python or call SUBMIT.",
                        category="empty_code",
                    )
                    raise repair
                elif len(normalized_code) > self._max_code_chars:
                    repair = _repair_error(
                        f"Intermediate code is too large ({len(normalized_code)} chars); "
                        f"keep one action under {self._max_code_chars} chars, use variables, and submit promptly.",
                        category="code_too_large",
                    )
                    raise repair

                self._ensure_bindings()
                ensure_bindings_ms = int((time.perf_counter() - bindings_started) * 1_000)

                raw = self._run_backend(code, variables, on_stdout=stdout_projector.feed)
                if isinstance(raw, BackendExecutionResult):
                    self._context_accesses.extend(raw.context_accesses)
                result = self._finalize(raw)

                execute_ms = int((time.perf_counter() - execute_started) * 1_000)
                if isinstance(result, _RepairFeedback):
                    if not public:
                        raise DaytonaAdapterError(
                            message=result.feedback,
                            cause_type="ContextVerificationError"
                            if "integrity" in result.feedback
                            else "HostSetupError",
                        )
                    repair_np = self._reject_repeated_no_progress(normalized_code, result.feedback)
                    if repair_np is not None:
                        raise repair_np
                    raise _repair_error(result.feedback, category=result.category)

                repair_np = self._reject_repeated_no_progress(normalized_code, result)
                if repair_np is not None:
                    raise repair_np

                stdout_projector.finish(expected_final=_submitted_payload(result))
                _flush_step_output(
                    result,
                    step=step,
                    stream_id=output_stream_id,
                    state=output_state,
                    public_output=self._public_output,
                    observe=self._observe,
                )

                outputs: dict[str, Any] = {
                    "path": type(self._backend).__name__,
                    "result_kind": _result_kind(result),
                    "stdout_chars": len(str(result)),
                    "output_preview": sanitize_trace_text(str(result), max_len=trace_chars),
                    "ensure_bindings_ms": ensure_bindings_ms,
                    "execute_ms": execute_ms,
                }
                phase.set_outputs(outputs)
                return result

            except TurnBudgetExhausted as exc:
                _fail_step("Execution failed", f"budget_{exc.dimension.value}")
                raise
            except RunTerminalError:
                _fail_step("Execution failed", "terminal_error")
                raise
            except CodeInterpreterError as exc:
                if not isinstance(exc, CodeExecutionError):
                    cat = str(getattr(exc, "category", "CodeInterpreterError"))
                    msg = sanitize_repair_text(sanitize_provider_message(str(exc)))
                    _fail_step("Execution failed", cat)
                    raise _terminal_error(msg, category=cat) from None

                cat = str(getattr(exc, "category", "execution_error"))
                msg = str(exc)
                if "characters omitted" not in msg:
                    msg = sanitize_repair_text(msg)
                _fail_step(
                    "Execution error",
                    cat,
                    recovered=True,
                    outputs={
                        "path": type(self._backend).__name__,
                        "result_kind": "repair_error",
                        "execution_status": "recovered_error",
                        "repair_category": cat,
                    },
                )
                raise _repair_error(msg, category=cat) from exc
            except SyntaxError as exc:
                _fail_step(
                    "Execution error",
                    "SyntaxError",
                    recovered=True,
                    outputs={
                        "path": "syntax",
                        "result_kind": "repair_error",
                        "execution_status": "recovered_error",
                        "repair_category": "SyntaxError",
                    },
                )
                raise _repair_error(str(exc), category="SyntaxError") from None
            except DaytonaAdapterError:
                _fail_step("Execution failed", "adapter_error")
                raise
            except Exception as exc:
                _fail_step("Execution failed", "execution_error")
                raise map_provider_error(exc) from exc
            finally:
                duration_ms = int((time.perf_counter() - step_started) * 1_000)
                self._observe(StepFinished(step, duration_ms))

    @with_callbacks
    def shutdown(self, *, strict_broker_cleanup: bool = False) -> None:
        """Shut down the interpreter and release backend resources."""
        with self._shutdown_lock:
            if self._shutdown:
                return

            with self._reservation_state_lock:
                if self._reservation_token is not None and not self._execution_started:
                    self._reservation_token = None
                    self._reservation_task = None
                    if self._execution_lock.locked():
                        self._execution_lock.release()

            broker_error: BaseException | None = None
            if self._http_broker is not None:
                stop = getattr(self._http_broker, "stop", None)
                if callable(stop):
                    try:
                        stop(strict=strict_broker_cleanup)
                    except BaseException as exc:
                        if strict_broker_cleanup:
                            broker_error = exc

            backend_error: BaseException | None = None
            backend = self._backend
            if backend is not None:
                try:
                    backend.close()
                except BaseException as exc:
                    backend_error = exc
                else:
                    self._backend = None

            if broker_error is not None:
                raise broker_error
            if backend_error is not None:
                raise backend_error
            self._shutdown = True

    @with_callbacks
    def invoke_tool(self, tool_name: str, kwargs: dict[str, Any], *args: Any) -> Any:
        """Invoke one bound host Tool through callback lifecycle."""
        fn = self._bound_tools.get(str(tool_name))
        if fn is None:
            raise CodeInterpreterError(f"Unknown tool: {tool_name}")
        if inspect.iscoroutinefunction(fn):
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop is not None and loop.is_running():
                future = asyncio.run_coroutine_threadsafe(fn(*args, **dict(kwargs)), loop)
                return future.result()
            return asyncio.run(fn(*args, **dict(kwargs)))
        if args:
            return fn(*args, **dict(kwargs))
        return fn(**dict(kwargs))

    def _ensure_bindings(self) -> None:
        backend = self._backend
        if backend is None:
            return
        tools = self._execution_tools()
        self._bound_tools = tools
        if not needs_binding_refresh(
            desired_generation=self._binding_generation,
            installed_generation=self._installed_binding_generation,
            broker_ready=True,
        ):
            return

        def host_binding(name: str, source: Callable[..., Any]) -> Callable[..., Any]:
            def invoke(*args: Any, **kwargs: Any) -> Any:
                return self.invoke_tool(name, kwargs, *args)

            invoke.__name__ = name
            with contextlib.suppress(TypeError, ValueError):
                object.__setattr__(invoke, "__signature__", inspect.signature(source))
            return invoke

        bound_map = {name: host_binding(name, source) for name, source in tools.items()}

        bind_tools = getattr(backend, "bind_host_tools", None)
        if callable(bind_tools):
            bind_tools(bound_map)

        ensure_submit = getattr(backend, "ensure_submit", None)
        if callable(ensure_submit):
            ensure_submit(self._output_fields)

        bind_manifest = getattr(backend, "bind_context_manifest", None)
        if self._context_binding is not None and callable(bind_manifest):
            bind_manifest(
                trusted_mount_root=self._context_binding[0],
                expected_manifest_sha256=self._context_binding[1],
            )
        self._installed_binding_generation = self._binding_generation

    def drain_context_accesses(self) -> tuple[str, ...]:
        values = tuple(self._context_accesses)
        self._context_accesses.clear()
        return values

    def _finalize(self, raw: str | BackendExecutionResult) -> Any:
        if isinstance(raw, BackendExecutionResult):
            if raw.error:
                error = sanitize_repair_text(sanitize_provider_message(raw.error))
                category = raw.error_category or _repair_category(error)
                if category in {"CodeInterpreterError", "InterpreterLifecycleError"}:
                    raise _terminal_error(error, category=category)
                feedback = error
                stderr = truncate_head_tail(raw.stderr, max_chars=self._execution_output_cap).strip()
                if stderr:
                    feedback = f"{feedback}\nstderr: {stderr}"
                return _RepairFeedback(feedback=feedback, category=category)
            if raw.final is not None:
                return wrap_final_output(raw.final)
            return truncate_head_tail(raw.stdout, max_chars=self._execution_output_cap)
        final = extract_final_payload(str(raw))
        if final is not None:
            return wrap_final_output(final)
        return truncate_head_tail(str(raw), max_chars=self._execution_output_cap)

    def _reject_repeated_no_progress(self, normalized_code: str, result: Any) -> CodeExecutionError | None:
        if is_final_output(result):
            self._last_execution = None
            self._no_progress_repair_used = False
            return None
        current = (normalized_code, str(result))
        if current == self._last_execution:
            if not self._no_progress_repair_used:
                self._no_progress_repair_used = True
                return _repair_error(
                    "Repeated interpreter action produced no progress. "
                    "Choose a different action, use the existing output, or call SUBMIT.",
                    category="no_progress",
                )
            raise RunNoProgressError
        self._last_execution = current
        self._no_progress_repair_used = False
        return None


def hashlib_sha256(data: bytes | str) -> str:
    import hashlib

    raw = data.encode("utf-8") if isinstance(data, str) else bytes(data)
    return hashlib.sha256(raw).hexdigest()


def sandbox_backend(
    sandbox: Any,
    *,
    loop: asyncio.AbstractEventLoop | None = None,
    dispatcher: SyncBridgeDispatcher | None = None,
    timeout_s: int | None = DEFAULT_EXECUTION_TIMEOUT_S,
) -> InterpreterBackend:
    """Build a stateful backend from a live Daytona sandbox."""
    if loop is not None:
        sandbox = sync_sandbox(sandbox, loop, dispatcher)
    return _SandboxProcessBackend(sandbox, timeout_s=timeout_s)
