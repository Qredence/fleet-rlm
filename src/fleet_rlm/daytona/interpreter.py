"""Minimal Daytona-backed code interpreter for dspy.RLM wiring.

Live execution uses one persistent Python namespace inside the sandbox broker
process so generated code and localhost host-tool wrappers share a namespace.

Host-tool / SUBMIT mediation (B1):
- In-process backends bind host callables directly (offline seam).
- Daytona sandbox backends use an HTTP-in-sandbox broker + host poll
  (Daytona-appropriate channel; mirrors DSPy host-tool + FinalOutput outcomes).

Public per-step output projection (stdout delta replay, stream closure, final
flush, native-error privacy) lives in ``interpreter_output.py``. The sync
view over async Daytona sandboxes lives in ``broker.py``.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import hashlib
import inspect
import io
import json
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from threading import Lock
from typing import TYPE_CHECKING, Any, Protocol, cast
from uuid import uuid4

import dspy
from dspy.utils.callback import BaseCallback, with_callbacks

from fleet_rlm.daytona.broker import (
    DEFAULT_BROKER_PORT,
    FINAL_OUTPUT_MARKER,
    FleetFinalOutputError,
    SyncBridgeDispatcher,
    build_submit_setup_code,
    extract_final_payload,
    sync_sandbox,
    tombstone_sync_sandbox,
)
from fleet_rlm.daytona.errors import (
    DaytonaAdapterError,
    map_provider_error,
    sanitize_provider_message,
)
from fleet_rlm.daytona.interpreter_output import (
    OutputCallback,
    _close_output_stream,
    _emit_output_delta,
    _flush_step_output,
    _OutputStreamState,
    _PublicStdoutProjector,
)
from fleet_rlm.observability.tracing import trace_preview_limit, turn_phase_span
from fleet_rlm.rlm.compat_3_3_1 import (
    PUBLIC_FINAL_OUTPUT_LABEL,
    CodeExecutionError,
    CodeInterpreterError,
    copy_output_fields,
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
    sanitize_public_text,
    sanitize_repair_text,
    truncate_head_tail,
    truncate_public_text,
)
from fleet_rlm.runtime.errors import FilesystemToolError

if TYPE_CHECKING:
    from fleet_rlm.daytona.broker import DaytonaHttpToolBroker

logger = logging.getLogger(__name__)

DEFAULT_EXECUTION_OUTPUT_CHARS = 4_000
DEFAULT_EXECUTION_TIMEOUT_S = 120
DEFAULT_INTERMEDIATE_CODE_CHARS = 12_000
_MAX_CAPTURED_OUTPUT_CHARS = 64 * 1024
_MISSING = object()
_UNSET = object()
_BINDING_RESERVATION: contextvars.ContextVar[object | None] = contextvars.ContextVar(
    "fleet_interpreter_binding_reservation",
    default=None,
)
_TOOL_POSITIONAL_ARGS: contextvars.ContextVar[tuple[Any, ...]] = contextvars.ContextVar(
    "fleet_interpreter_tool_positional_args",
    default=(),
)


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


class _BindingTools(dict[str, Callable[..., Any]]):
    """Invocation tool map that marks Fleet binding state dirty on mutation."""

    def __init__(
        self,
        owner: DaytonaCodeInterpreter,
        initial: Mapping[str, Callable[..., Any]] | None = None,
    ) -> None:
        self._owner = owner
        super().__init__(initial or {})

    def __setitem__(self, key: str, value: Callable[..., Any]) -> None:
        self._owner._ensure_binding_mutation_allowed()
        super().__setitem__(key, value)
        self._owner._mark_bindings_dirty()

    def __delitem__(self, key: str) -> None:
        self._owner._ensure_binding_mutation_allowed()
        super().__delitem__(key)
        self._owner._mark_bindings_dirty()

    def clear(self) -> None:
        self._owner._ensure_binding_mutation_allowed()
        super().clear()
        self._owner._mark_bindings_dirty()

    def pop(self, key: Any, default: Any = _MISSING) -> Any:
        self._owner._ensure_binding_mutation_allowed()
        if key not in self:
            if default is _MISSING:
                raise KeyError(key)
            return default
        value = super().pop(key)
        self._owner._mark_bindings_dirty()
        return value

    def popitem(self) -> tuple[str, Callable[..., Any]]:
        self._owner._ensure_binding_mutation_allowed()
        value = super().popitem()
        self._owner._mark_bindings_dirty()
        return value

    def setdefault(self, key: Any, default: Any = None) -> Any:
        self._owner._ensure_binding_mutation_allowed()
        if key in self:
            return self[key]
        value = super().setdefault(key, default)
        self._owner._mark_bindings_dirty()
        return value

    def update(
        self,
        other: Any = None,
        /,
        **kwargs: Callable[..., Any],
    ) -> None:
        # DSPy calls ``tools.update(execution_tools)`` for each invocation.
        # Replacement, rather than merge, is intentional: removed names must
        # become unreachable in the persistent interpreter namespace.
        self._owner._begin_binding_injection()
        self._owner._ensure_binding_mutation_allowed()
        values: dict[str, Callable[..., Any]] = {}
        if other is not None:
            values.update(other)
        values.update(kwargs)
        super().clear()
        super().update(values)
        self._owner._mark_bindings_dirty()

    def __ior__(self, other: Any) -> _BindingTools:
        self.update(other)
        return self


def _combine_stdout(captured: str, legacy: object) -> str:
    """Prefer real stdout, retaining the legacy ``_out`` fallback for tests."""
    return captured or str(legacy or "")


def _submitted_payload(result: Any) -> Mapping[str, Any] | None:
    if not is_final_output(result):
        return None
    value = getattr(result, "output", None)
    return value if isinstance(value, Mapping) else None


class InProcessInterpreterBackend:
    """Shared-namespace offline backend for host-tool and SUBMIT contracts."""

    def __init__(self) -> None:
        self.namespace: dict[str, object] = {"_out": ""}
        self.closed = False
        self._host_tools: dict[str, Callable[..., Any]] = {}
        self._bound_tool_names: set[str] = set()
        self._submit_key: object = _UNSET
        self._context_accesses: list[str] = []
        self._context_binding: tuple[str, str] | None = None

        def load_context(
            raw_manifest: bytes | str,
        ) -> list[dict[str, Any]]:
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
                self.namespace.pop("context", None)
            return values

        self.namespace["_fleet_load_context_manifest"] = load_context

    def bind_context_manifest(self, *, trusted_mount_root: str, expected_manifest_sha256: str) -> None:
        """Bind the host-authorized capsule before generated code can load it."""
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
        """
        Install or refresh the namespace bindings required for submitting final outputs.

        Parameters:
            output_fields (list[dict[str, Any]] | None): Output-field definitions used
                to configure submission support.
        """
        key = _submit_signature_key(output_fields)
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
        with contextlib.redirect_stdout(stdout):
            try:
                exec(code, self.namespace, self.namespace)
            except FleetFinalOutputError as final:
                return BackendExecutionResult(
                    stdout=_combine_stdout(stdout.getvalue(), self.namespace.get("_out", "")),
                    final=dict(final.value),
                    context_accesses=self._drain_context_accesses(),
                )
            except Exception as exc:
                value = getattr(exc, "value", None)
                if type(exc).__name__ == "FleetFinalOutputError" and isinstance(value, dict):
                    return BackendExecutionResult(
                        stdout=_combine_stdout(stdout.getvalue(), self.namespace.get("_out", "")),
                        final=dict(value),
                        context_accesses=self._drain_context_accesses(),
                    )
                return BackendExecutionResult(
                    stdout=_combine_stdout(stdout.getvalue(), self.namespace.get("_out", "")),
                    error=sanitize_provider_message(str(exc)),
                    error_category=type(exc).__name__,
                    context_accesses=self._drain_context_accesses(),
                )
        return BackendExecutionResult(
            stdout=_combine_stdout(stdout.getvalue(), self.namespace.get("_out", "")),
            context_accesses=self._drain_context_accesses(),
        )

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


def _submit_signature_key(
    output_fields: list[dict[str, Any]] | None,
) -> tuple[tuple[str, str], ...] | None:
    digest = _output_fields_digest(output_fields)
    return ((digest, ""),) if digest is not None else None


def _output_fields_digest(output_fields: list[dict[str, Any]] | None) -> str | None:
    """Hash the complete output metadata, including nested schema details."""
    if output_fields is None:
        return None
    try:
        encoded = json.dumps(
            output_fields,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=lambda value: {"__type__": type(value).__name__},
        ).encode("utf-8")
    except (TypeError, ValueError):
        encoded = repr(output_fields).encode("utf-8", errors="replace")
    return hashlib.sha256(encoded).hexdigest()


class InterpreterBackend(Protocol):
    """Narrow execute/close surface; SDK details stay behind this protocol."""

    def run(self, code: str, variables: dict[str, object] | None = None) -> str | BackendExecutionResult: ...

    def close(self) -> None: ...


def _result_kind(result: Any) -> str:
    """Bounded outcome classification for span metadata (never content)."""
    if is_final_output(result):
        return "final"
    return "output"


_REPAIR_CATEGORIES = frozenset(
    {
        "AttributeError",
        "ImportError",
        "IndexError",
        "KeyError",
        "ModuleNotFoundError",
        "NameError",
        "OSError",
        "RuntimeError",
        "SyntaxError",
        "TypeError",
        "ValueError",
        "ZeroDivisionError",
        "code_too_large",
        "empty_code",
        "execution_error",
        "no_progress",
    }
)
_TERMINAL_CATEGORIES = frozenset({"CodeInterpreterError", "InterpreterLifecycleError"})


class _FleetCodeExecutionError(CodeExecutionError):
    """Native DSPy recoverable error carrying bounded Fleet classification."""

    category: str

    def __init__(self, message: str, *, category: str) -> None:
        super().__init__(message)
        self.category = category


class _FleetCodeInterpreterError(CodeInterpreterError):
    """Native DSPy terminal error carrying bounded Fleet classification."""

    category: str

    def __init__(self, message: str, *, category: str) -> None:
        super().__init__(message)
        self.category = category


def _repair_error(message: str, *, category: str) -> CodeExecutionError:
    """Create DSPy's native recoverable error with bounded Fleet metadata."""
    bounded_category = category if category in _REPAIR_CATEGORIES else "execution_error"
    return _FleetCodeExecutionError(sanitize_repair_text(message), category=bounded_category)


def _terminal_error(message: str, *, category: str) -> CodeInterpreterError:
    """Create DSPy's terminal interpreter error without private details."""
    bounded_category = category if category in _TERMINAL_CATEGORIES else "CodeInterpreterError"
    return _FleetCodeInterpreterError(sanitize_repair_text(message), category=bounded_category)


def _repair_category(error: str) -> str:
    """Return a bounded runtime-error category without retaining generated details."""
    prefix = error.split(":", 1)[0].strip()
    return prefix if prefix in _REPAIR_CATEGORIES else "execution_error"


class _SandboxProcessBackend:
    """Live sandbox handle whose persistent namespace is owned by the broker."""

    def __init__(self, sandbox: Any, *, timeout_s: int | None = None) -> None:
        self._sandbox = sandbox
        if timeout_s is not None and int(timeout_s) <= 0:
            raise DaytonaAdapterError(
                message="execution timeout must be positive",
                cause_type="InterpreterConfigurationError",
            )
        self._timeout_s: int | None = int(timeout_s) if timeout_s is not None else None

    @property
    def sandbox(self) -> Any:
        return self._sandbox

    @property
    def timeout_s(self) -> int | None:
        return self._timeout_s

    def run(
        self,
        code: str,
        variables: dict[str, object] | None = None,
        *,
        on_stdout: OutputCallback | None = None,
    ) -> BackendExecutionResult:
        del code, variables, on_stdout
        raise DaytonaAdapterError(
            message="live execution requires the co-located broker",
            cause_type="InterpreterConfigurationError",
        )

    def close(self) -> None:
        # Tombstone the sync view so late calls fail typed-fast after lease
        # release; the shared service loop outlives individual Turns.
        tombstone_sync_sandbox(self._sandbox)
        return None


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
        # DSPy 3.3.1's callback contract is opt-in and engineering-only.
        # Fleet Runtime Events and manual spans remain the product/trace
        # authorities; callbacks are never installed implicitly.
        self.callbacks = list(callbacks or [])
        self._binding_generation = 0
        self._installed_binding_generation = -1
        self._execution_lock = Lock()
        self._shutdown_lock = Lock()
        self._reservation_state_lock = Lock()
        self._reservation_token: object | None = None
        self._reservation_task: asyncio.Task[Any] | None = None
        self._execution_started = False
        self._tools: _BindingTools = _BindingTools(self, tools)
        self._bound_tools: dict[str, Callable[..., Any]] = {}
        self._fleet_output_contract: FleetOutputContract | None = None
        self._output_fields: list[dict[str, Any]] | None = None
        self._output_fields_digest: str | None = None
        self.output_fields = output_fields
        self._started = False
        self._shutdown = False
        self._broker_port = broker_port
        self._http_broker: DaytonaHttpToolBroker | None = None
        self._observer: ObservationObserver | None = None
        self._observation_max_chars = 10_000
        self._execution_output_cap = max(1, int(execution_output_cap))
        self._max_code_chars = max(1, int(max_code_chars))
        self._observation_step = 0
        self._observation_namespace = uuid4().hex
        self._last_execution: tuple[str, str] | None = None
        self._no_progress_repair_used = False
        self._context_accesses: list[str] = []
        self._context_binding: tuple[str, str] | None = None

    @property
    def tools(self) -> dict[str, Callable[..., Any]]:
        return self._tools

    @property
    def supports_sandbox_serializable_inputs(self) -> bool:
        """Whether non-primitive inputs are injected through the remote broker.

        Native DSPy treats ``SandboxSerializable`` inputs specially.  Fleet's
        in-process test backend can retain a dspy.History object directly, while
        the Daytona HTTP broker must receive the typed transport form.
        """
        return isinstance(self._backend, _SandboxProcessBackend)

    @property
    def broker(self) -> DaytonaHttpToolBroker | None:
        """Return the broker context owned by this interpreter, when started."""
        return self._http_broker

    @property
    def output_fields(self) -> list[dict[str, Any]] | None:
        """Return the current typed-output metadata copy."""
        return copy_output_fields(self._output_fields)

    def bind_output_contract(self, contract: FleetOutputContract) -> None:
        self._ensure_binding_mutation_allowed()
        self._fleet_output_contract = contract
        self.output_fields = [{"name": field.name} for field in contract.fields]

    @output_fields.setter
    def output_fields(self, value: list[dict[str, Any]] | None) -> None:
        self._ensure_binding_mutation_allowed()
        copied = copy_output_fields(value)
        if copied is not None and self._fleet_output_contract is not None:
            copied = self._fleet_output_contract.merge(copied)
        digest = _output_fields_digest(copied)
        self._output_fields = copied
        if digest != getattr(self, "_output_fields_digest", None):
            self._output_fields_digest = digest
            self._mark_bindings_dirty()

    def _mark_bindings_dirty(self) -> None:
        """Advance the Fleet-owned generation for the next execution."""
        if hasattr(self, "_binding_generation"):
            self._binding_generation += 1

    def _ensure_binding_mutation_allowed(self) -> None:
        """Reject a second invocation before it can mutate the current namespace."""
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
        """Reserve this interpreter before DSPy starts an overlapping ``acall``."""
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
            self._execution_lock.release()
            clear_context = _BINDING_RESERVATION.get() is token
        if clear_context:
            _BINDING_RESERVATION.set(None)

    @with_callbacks
    def start(self) -> None:
        if self._shutdown:
            msg = "interpreter already shut down"
            raise DaytonaAdapterError(message=msg, cause_type="InterpreterLifecycleError")
        self._started = True

    def bind_observer(self, observer: ObservationObserver | None, *, max_chars: int = 10_000) -> None:
        """Bind one run-local observer without changing interpreter execution semantics."""
        self._ensure_binding_mutation_allowed()
        normalized_max_chars = max(1, int(max_chars))
        if self._observer is not observer or self._observation_max_chars != normalized_max_chars:
            self._mark_bindings_dirty()
        self._observer = observer
        self._observation_max_chars = normalized_max_chars
        self._observation_step = 0
        self._last_execution = None
        self._no_progress_repair_used = False

    def bind_context_capsule(self, capsule: Any) -> None:
        """Bind one host-created context capsule before DSPy starts the RLM."""
        from fleet_rlm.rlm.program import AttachmentContextCapsule

        if not isinstance(capsule, AttachmentContextCapsule):
            raise DaytonaAdapterError(
                message="context capsule is invalid",
                cause_type="ContextIntegrityError",
            )
        raw_manifest = capsule.to_sandbox()
        binding = (capsule.mount_root, hashlib.sha256(raw_manifest).hexdigest())
        if self._context_binding is not None and self._context_binding != binding:
            raise DaytonaAdapterError(
                message="context manifest binding cannot be replaced",
                cause_type="ContextIntegrityError",
            )
        if self._http_broker is not None:
            self._http_broker.bind_context_manifest(
                trusted_mount_root=binding[0],
                expected_manifest_sha256=binding[1],
            )
        bind_backend = getattr(self._backend, "bind_context_manifest", None)
        if callable(bind_backend):
            bind_backend(
                trusted_mount_root=binding[0],
                expected_manifest_sha256=binding[1],
            )
        self._context_binding = binding

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
            parameters = inspect.signature(run).parameters.values()
        except (TypeError, ValueError):
            parameters = ()
        supports_callback = any(
            parameter.name == "on_stdout" or parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters
        )
        if supports_callback:
            return run(code, variables, on_stdout=on_stdout)
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
        """Execute one action while rejecting overlapping interpreter reuse."""
        token = self._acquire_execution()
        try:
            return self._execute_once(code, variables)
        finally:
            self._release_execution(token)

    def _execute_once(self, code: str, variables: dict[str, Any] | None = None) -> Any:
        """
        Execute one code step in the configured interpreter.

        Parameters:
            code (str): Python code to execute.
            variables (dict[str, Any] | None): Variables to make available during execution.

        Returns:
            Any: The submitted final value or bounded ordinary execution output.

        Raises:
            CodeExecutionError: If execution produces a recoverable error.
            CodeInterpreterError: If execution cannot safely continue.
            DaytonaAdapterError: If the interpreter is unavailable, misconfigured, shut down, or the provider fails.
            RunTerminalError: If repeated execution makes no progress.
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
                "code_preview": sanitize_public_text(
                    truncate_head_tail(code or "", max_chars=trace_chars),
                    max_len=trace_chars,
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
                    repair = _repair_error(
                        "No executable code was provided; execute useful Python or call SUBMIT.",
                        category="empty_code",
                    )
                    no_progress = self._reject_repeated_no_progress(normalized_code, repair)
                    raise no_progress or repair
                elif len(normalized_code) > self._max_code_chars:
                    repair = _repair_error(
                        f"Intermediate code is too large ({len(normalized_code)} chars); "
                        f"keep one action under {self._max_code_chars} chars, use variables, and submit promptly.",
                        category="code_too_large",
                    )
                    no_progress = self._reject_repeated_no_progress(normalized_code, repair)
                    raise no_progress or repair
                else:
                    self._ensure_bindings()
                    ensure_bindings_ms = int((time.perf_counter() - bindings_started) * 1_000)
                    if self._http_broker is not None:
                        result = self._execute_with_http_broker(
                            code,
                            variables,
                            on_stdout=stdout_projector.feed,
                        )
                    else:
                        raw = self._run_backend(code, variables, on_stdout=stdout_projector.feed)
                        if isinstance(raw, BackendExecutionResult):
                            self._context_accesses.extend(raw.context_accesses)
                            self._raise_context_injection_error(code, raw)
                        result = self._finalize(raw)
                execute_ms = int((time.perf_counter() - execute_started) * 1_000)
                repair = self._reject_repeated_no_progress(normalized_code, result)
                if repair is not None:
                    raise repair
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
                    "path": "http_broker" if self._http_broker is not None else type(self._backend).__name__,
                    "result_kind": _result_kind(result),
                    "stdout_chars": len(str(result)),
                    "output_preview": sanitize_public_text(str(result), max_len=trace_chars),
                }
                if self._http_broker is not None:
                    outputs["ensure_bindings_ms"] = ensure_bindings_ms
                    outputs["execute_ms"] = execute_ms
                    broker_metrics = dict(self._http_broker.last_execution_stats)
                    # Keep the flat keys for existing trace consumers, but put
                    # the complete broker breakdown under one bounded mapping.
                    # The generic trace projection caps a mapping at 32 keys;
                    # placing these metrics together prevents the six
                    # human-readable fields above from hiding the tail of the
                    # execution statistics.
                    outputs["broker_metrics"] = broker_metrics
                    outputs.update(broker_metrics)
                phase.set_outputs(outputs)
                return result
            except RunTerminalError:
                stdout_projector.finish()
                _close_output_stream(
                    "Execution failed", step=step, stream_id=output_stream_id, state=output_state, observe=self._observe
                )
                raise
            except CodeInterpreterError as exc:
                if not isinstance(exc, CodeExecutionError):
                    category = str(getattr(exc, "category", "CodeInterpreterError"))
                    exc = _terminal_error(str(exc), category=category)
                    stdout_projector.finish()
                    _close_output_stream(
                        "Execution failed",
                        step=step,
                        stream_id=output_stream_id,
                        state=output_state,
                        observe=self._observe,
                    )
                    raise exc from None
                category = str(getattr(exc, "category", "execution_error"))
                exc = _repair_error(str(exc), category=category)
                category = str(getattr(exc, "category", "execution_error"))
                if category not in {"empty_code", "code_too_large", "no_progress"}:
                    repair = self._reject_repeated_no_progress(normalized_code, exc)
                    if repair is not None:
                        exc = repair
                        category = str(getattr(exc, "category", "no_progress"))
                phase.finish(
                    phase_status="failed",
                    outputs={
                        "path": "http_broker" if self._http_broker is not None else type(self._backend).__name__,
                        "result_kind": "repair_error",
                        "execution_status": "recovered_error",
                        "repair_category": category,
                    },
                    attributes={"recovered": True, "failure_category": category},
                )
                stdout_projector.finish()
                _close_output_stream(
                    "Execution error", step=step, stream_id=output_stream_id, state=output_state, observe=self._observe
                )
                raise exc
            except SyntaxError as exc:
                repair = _repair_error(str(exc), category="SyntaxError")
                phase.finish(
                    phase_status="failed",
                    outputs={
                        "path": "syntax",
                        "result_kind": "repair_error",
                        "execution_status": "recovered_error",
                        "repair_category": "SyntaxError",
                    },
                    attributes={"recovered": True, "failure_category": "SyntaxError"},
                )
                stdout_projector.finish()
                _close_output_stream(
                    "Execution error", step=step, stream_id=output_stream_id, state=output_state, observe=self._observe
                )
                raise repair from None
            except DaytonaAdapterError:
                stdout_projector.finish()
                _close_output_stream(
                    "Execution failed", step=step, stream_id=output_stream_id, state=output_state, observe=self._observe
                )
                raise
            except Exception as exc:
                mapped = map_provider_error(exc)
                stdout_projector.finish()
                _close_output_stream(
                    "Execution failed", step=step, stream_id=output_stream_id, state=output_state, observe=self._observe
                )
                raise mapped from exc
            finally:
                duration_ms = int((time.perf_counter() - step_started) * 1_000)
                self._observe(StepFinished(step, duration_ms))

    @with_callbacks
    def shutdown(self, *, strict_broker_cleanup: bool = False) -> None:
        """
        Shut down the interpreter and release its broker and backend resources.

        Parameters:
            strict_broker_cleanup (bool): Whether broker cleanup errors should be
                propagated. When false, broker cleanup errors are suppressed.
        """
        # Shutdown may be requested by both the owner-loop close path and a
        # worker-thread release callback.  Single-flight it, and only mark the
        # interpreter closed after every owned resource has settled so a
        # strict failure remains retryable.
        with self._shutdown_lock:
            if self._shutdown:
                return
            reservation_token: object | None = None
            with self._reservation_state_lock:
                if self._reservation_token is not None and not self._execution_started:
                    reservation_token = self._reservation_token
                    self._reservation_token = None
                    self._reservation_task = None
                    self._execution_lock.release()
            if reservation_token is not None and _BINDING_RESERVATION.get() is reservation_token:
                _BINDING_RESERVATION.set(None)
            else:
                current = _BINDING_RESERVATION.get()
                with self._reservation_state_lock:
                    owns_active_execution = (
                        self._execution_started and self._reservation_token is current and current is not None
                    )
                    execution_active = self._execution_lock.locked()
                if execution_active and not owns_active_execution:
                    self._execution_lock.acquire()
                    self._execution_lock.release()
            first_error: BaseException | None = None
            broker = self._http_broker
            broker_settled = True
            if broker is not None:
                try:
                    broker_result = broker.stop(strict=strict_broker_cleanup)
                    # Older injected broker doubles returned None; treat that
                    # as the historical successful-stop result.
                    broker_settled = broker_result is not False
                except BaseException as exc:
                    first_error = exc
                if broker_settled:
                    self._http_broker = None
                else:
                    # Non-strict shutdown may suppress a provider cleanup
                    # error, but the broker remains owned and retryable. Do
                    # not publish interpreter shutdown until it settles.
                    logger.warning("broker cleanup remains pending during interpreter shutdown")
            backend = self._backend
            if backend is not None:
                try:
                    backend.close()
                except BaseException as exc:
                    first_error = first_error or exc
                else:
                    self._backend = None
            if first_error is not None:
                raise first_error
            self._shutdown = broker_settled

    @with_callbacks
    def invoke_tool(self, tool_name: str, kwargs: dict[str, Any]) -> Any:
        """Invoke one bound host Tool through DSPy's callback lifecycle."""
        fn = self._bound_tools.get(str(tool_name))
        if fn is None:
            raise CodeInterpreterError(f"Unknown tool: {tool_name}")
        return fn(*_TOOL_POSITIONAL_ARGS.get(), **dict(kwargs))

    def _invoke_tool_with_args(
        self,
        tool_name: str,
        args: tuple[Any, ...],
        kwargs: Mapping[str, Any],
    ) -> Any:
        """Route positional completeness through the callback-decorated seam."""
        token = _TOOL_POSITIONAL_ARGS.set(tuple(args))
        try:
            return self.invoke_tool(tool_name, dict(kwargs))
        finally:
            _TOOL_POSITIONAL_ARGS.reset(token)

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
            if not needs_binding_refresh(
                desired_generation=self._binding_generation,
                installed_generation=self._installed_binding_generation,
                broker_ready=True,
            ):
                return
            backend.bind_host_tools(
                {
                    name: (
                        lambda *_args, _name=name, **kwargs: self._invoke_tool_with_args(
                            _name,
                            _args,
                            kwargs,
                        )
                    )
                    for name in tools
                }
            )
            backend.ensure_submit(self.output_fields)
            self._installed_binding_generation = self._binding_generation
            return
        if not isinstance(backend, _SandboxProcessBackend):
            self._installed_binding_generation = self._binding_generation
            return
        broker_ready = self._http_broker is not None and not bool(getattr(self._http_broker, "_stopped", False))
        if not needs_binding_refresh(
            desired_generation=self._binding_generation,
            installed_generation=self._installed_binding_generation,
            broker_ready=broker_ready,
        ):
            return
        if not broker_ready:
            from fleet_rlm.daytona.broker import DaytonaHttpToolBroker

            context_binding = self._context_binding
            if self._http_broker is None or bool(getattr(self._http_broker, "_stopped", False)):
                self._http_broker = DaytonaHttpToolBroker(
                    sandbox=backend.sandbox,
                    broker_port=self._broker_port,
                    context_mount_root=context_binding[0] if context_binding is not None else None,
                    context_manifest_sha256=context_binding[1] if context_binding is not None else None,
                )
            self._http_broker.ensure_started()
        self._http_broker.register_tools(tools)
        self._http_broker.execute_code(
            self._http_broker.submit_setup_code(self.output_fields),
            timeout_s=float(backend.timeout_s or DEFAULT_EXECUTION_TIMEOUT_S),
        )
        self._installed_binding_generation = self._binding_generation

    def _execute_with_http_broker(
        self,
        code: str,
        variables: dict[str, Any] | None,
        *,
        on_stdout: OutputCallback,
    ) -> Any:
        broker = self._http_broker
        backend = self._backend
        if broker is None or backend is None:
            msg = "http broker is not configured"
            raise DaytonaAdapterError(message=msg, cause_type="InterpreterConfigurationError")

        def tool_executor(name: str, args: list[Any], kwargs: dict[str, Any]) -> Any:
            if name not in self._bound_tools:
                msg = f"unknown tool: {name}"
                raise DaytonaAdapterError(message=msg, cause_type="UnknownToolError")
            try:
                # Host contract is kwargs-only: DSPy 3.3.x interpreter tools are
                # ``def invoke(**kwargs)`` callables behind spoofed signatures,
                # so broker payloads forward every parameter by name. ``args``
                # is retained only for POSITIONAL_ONLY completeness.
                return self._invoke_tool_with_args(name, tuple(args), kwargs)
            except FilesystemToolError as exc:
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

        if isinstance(backend, _SandboxProcessBackend):
            timeout_s = float(backend.timeout_s or DEFAULT_EXECUTION_TIMEOUT_S)

            def run_code() -> str | BackendExecutionResult:
                return broker.execute_code(code, variables, timeout_s=timeout_s, on_stdout=on_stdout)

        else:

            def run_code() -> str | BackendExecutionResult:
                return self._run_backend(code, variables, on_stdout=on_stdout)

        raw = broker.execute_with_callbacks(
            run_code=run_code,
            tool_executor=tool_executor,
        )
        self._context_accesses.extend(raw.context_accesses)
        self._raise_context_injection_error(code, raw)
        return self._finalize(raw)

    def drain_context_accesses(self) -> tuple[str, ...]:
        """Return and clear sanitized attachment IDs read during capsule injection."""
        values = tuple(self._context_accesses)
        self._context_accesses.clear()
        return values

    @staticmethod
    def _raise_context_injection_error(code: str, raw: BackendExecutionResult) -> None:
        if raw.error and "_fleet_load_context_manifest" in code:
            raise DaytonaAdapterError(
                message="prepared context failed integrity verification",
                cause_type="ContextIntegrityError",
            )

    def _finalize(self, raw: str | BackendExecutionResult) -> Any:
        if isinstance(raw, BackendExecutionResult):
            if raw.error:
                error = sanitize_provider_message(raw.error)
                if "f-string expression part cannot include a backslash" in error:
                    error = (
                        f"{error}. Build the escaped fragment before the f-string expression, "
                        "then interpolate the variable."
                    )
                category = raw.error_category or _repair_category(error)
                if category in {"CodeInterpreterError", "InterpreterLifecycleError"}:
                    raise _terminal_error(error, category=category)
                feedback = error
                stderr = truncate_head_tail(raw.stderr, max_chars=self._execution_output_cap).strip()
                if stderr:
                    feedback = f"{feedback}\nstderr: {sanitize_repair_text(stderr)}"
                raise _repair_error(feedback, category=category)
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


def sandbox_backend(
    sandbox: Any,
    *,
    loop: asyncio.AbstractEventLoop | None = None,
    dispatcher: SyncBridgeDispatcher | None = None,
    timeout_s: int | None = DEFAULT_EXECUTION_TIMEOUT_S,
) -> InterpreterBackend:
    """Build a stateful backend from a live Daytona sandbox (daytona package only).

    ``timeout_s`` bounds each ``run_code`` call (Daytona's SDK default is ten
    minutes when unset); pass ``None`` to keep the SDK default. When ``loop``
    is given, ``dispatcher`` optionally injects the composition-owned bridge
    authority (QRE-154); omitted, the view resolves through the legacy
    process-default dispatcher.
    """
    if loop is not None:
        sandbox = sync_sandbox(sandbox, loop, dispatcher)
    return _SandboxProcessBackend(sandbox, timeout_s=timeout_s)
