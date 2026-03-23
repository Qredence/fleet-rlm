"""Modal-backed code interpreter for DSPy RLM execution.

This module provides the ModalInterpreter class, a CodeInterpreter implementation
that executes Python code in isolated Modal sandbox environments. It supports:

    - Stateful code execution across multiple calls
    - Tool registration and invocation
    - Volume persistence for data storage
    - Secure secret management
    - Output capture and streaming
    - Automatic resource cleanup

The interpreter communicates with the sandbox via a JSON protocol over stdin/stdout,
enabling bidirectional communication for tool calls and structured output.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import queue
import threading
import time
from contextlib import contextmanager
from typing import Any, Callable, Iterator, Sequence

import dspy
import modal
from dspy.primitives import CodeInterpreterError, FinalOutput

from fleet_rlm.runtime.execution.core_driver import sandbox_driver
from fleet_rlm.runtime.execution.profiles import ExecutionProfile  # noqa: F811
from fleet_rlm.runtime.tools.llm_tools import LLMQueryMixin
from fleet_rlm.runtime.tools.modal_volumes import VolumeOpsMixin

from . import driver_factories
from .interpreter_events import emit_execution_event as _emit_execution_event_impl
from .interpreter_events import (
    complete_event_data,
    start_event_data,
)
from .interpreter_events import summarize_code as _summarize_code_impl
from .output_utils import _redact_sensitive_text, _summarize_stdout

logger = logging.getLogger(__name__)


def _start_stdout_reader_impl(interpreter: ModalInterpreter) -> None:
    """Start a background thread to read sandbox stdout."""
    if interpreter._stdout_iter is None:
        return

    interpreter._stdout_queue = queue.Queue()
    q = interpreter._stdout_queue
    iterator = interpreter._stdout_iter

    def _reader() -> None:
        try:
            for line in iterator:
                q.put(line)
        except Exception:
            logger.exception("Error while reading from sandbox stdout iterator")
        finally:
            q.put(None)

    interpreter._stdout_reader_thread = threading.Thread(target=_reader, daemon=True)
    interpreter._stdout_reader_thread.start()


def _resolve_app_impl(interpreter: ModalInterpreter) -> modal.App:
    if interpreter._app_obj is not None:
        return interpreter._app_obj
    return modal.App.lookup(interpreter._app_name, create_if_missing=True)


async def _aresolve_app_impl(interpreter: ModalInterpreter) -> modal.App:
    if interpreter._app_obj is not None:
        return interpreter._app_obj
    return await modal.App.lookup.aio(interpreter._app_name, create_if_missing=True)


def _module_source_for_sandbox_impl(module: Any) -> str:
    source = inspect.getsource(module)
    return "\n".join(
        line
        for line in source.splitlines()
        if line.strip() != "from __future__ import annotations"
    )


def _build_driver_command_and_sandbox_kwargs_impl(
    interpreter: ModalInterpreter, *, app: modal.App
) -> tuple[str, dict[str, Any]]:
    with interpreter._llm_call_lock:
        interpreter._llm_call_count = 0

    from fleet_rlm.runtime.agent import session_history

    from . import sandbox_assets

    bundled_sources = [
        _module_source_for_sandbox_impl(driver_factories),
        _module_source_for_sandbox_impl(sandbox_assets),
        _module_source_for_sandbox_impl(session_history),
        inspect.getsource(sandbox_driver),
        "sandbox_driver()",
    ]
    driver_command = "\n\n".join(bundled_sources)

    sandbox_kwargs: dict[str, Any] = {
        "app": app,
        "image": interpreter.image,
        "secrets": interpreter.secrets,
        "timeout": interpreter.timeout,
    }
    if interpreter.idle_timeout is not None:
        sandbox_kwargs["idle_timeout"] = interpreter.idle_timeout
    if interpreter.volume_name:
        interpreter._volume = interpreter._resolve_volume()
        sandbox_kwargs["volumes"] = {interpreter.volume_mount_path: interpreter._volume}

    return driver_command, sandbox_kwargs


def _start_impl(interpreter: ModalInterpreter) -> None:
    if interpreter._sandbox is not None:
        return

    app = interpreter._resolve_app()
    driver_command, sandbox_kwargs = (
        interpreter._build_driver_command_and_sandbox_kwargs(app=app)
    )

    interpreter._sandbox = modal.Sandbox.create(**sandbox_kwargs)
    interpreter._proc = interpreter._sandbox.exec(
        "python", "-u", "-c", driver_command, bufsize=1, timeout=interpreter.timeout
    )
    interpreter._stdin = interpreter._proc.stdin
    interpreter._stdout_iter = iter(interpreter._proc.stdout)
    interpreter._stderr_iter = iter(getattr(interpreter._proc, "stderr", []))
    interpreter._start_stdout_reader()


async def _astart_impl(interpreter: ModalInterpreter) -> None:
    if interpreter._sandbox is not None:
        return

    app = await interpreter._aresolve_app()
    driver_command, sandbox_kwargs = (
        interpreter._build_driver_command_and_sandbox_kwargs(app=app)
    )

    interpreter._sandbox = await modal.Sandbox.create.aio(**sandbox_kwargs)
    interpreter._proc = await interpreter._sandbox.exec.aio(
        "python", "-u", "-c", driver_command, bufsize=1, timeout=interpreter.timeout
    )
    interpreter._stdin = interpreter._proc.stdin
    interpreter._stdout_iter = iter(interpreter._proc.stdout)
    interpreter._stderr_iter = iter(getattr(interpreter._proc, "stderr", []))
    interpreter._start_stdout_reader()


def _shutdown_impl(interpreter: ModalInterpreter) -> None:
    if interpreter._sandbox is not None:
        try:
            interpreter._sandbox.terminate()
        except Exception:
            logger.exception("Error while terminating Modal sandbox during shutdown")

    interpreter._sandbox = None
    interpreter._proc = None
    interpreter._stdin = None
    interpreter._stdout_iter = None
    interpreter._stdout_queue = None
    interpreter._stdout_reader_thread = None
    interpreter._stderr_iter = None
    interpreter._volume = None
    with interpreter._sub_lm_executor_lock:
        if interpreter._sub_lm_executor is not None:
            interpreter._sub_lm_executor.shutdown(wait=False, cancel_futures=True)
            interpreter._sub_lm_executor = None


async def _ashutdown_impl(interpreter: ModalInterpreter) -> None:
    if interpreter._sandbox is not None:
        try:
            if hasattr(interpreter._sandbox.terminate, "aio"):
                await interpreter._sandbox.terminate.aio()
            else:
                interpreter._sandbox.terminate()
        except Exception:
            logger.exception(
                "Error while terminating Modal sandbox during async shutdown"
            )

    if interpreter._stdout_reader_thread is not None:
        interpreter._stdout_reader_thread.join(timeout=2.0)
        interpreter._stdout_reader_thread = None

    interpreter._sandbox = None
    interpreter._proc = None
    interpreter._stdin = None
    interpreter._stdout_iter = None
    interpreter._stdout_queue = None
    interpreter._stderr_iter = None
    interpreter._volume = None
    with interpreter._sub_lm_executor_lock:
        if interpreter._sub_lm_executor is not None:
            interpreter._sub_lm_executor.shutdown(wait=False, cancel_futures=True)
            interpreter._sub_lm_executor = None


def _tool_names_impl(interpreter: ModalInterpreter) -> list[str]:
    tool_names = ["llm_query", "llm_query_batched"]
    if interpreter._tools:
        tool_names.extend(interpreter._tools.keys())
    return tool_names


def _output_names_impl(interpreter: ModalInterpreter) -> list[str]:
    if not interpreter.output_fields:
        return []
    return [
        field["name"]
        for field in interpreter.output_fields
        if isinstance(field, dict) and field.get("name")
    ]


def _summarize_stdout_impl(interpreter: ModalInterpreter, stdout: str) -> str:
    if not interpreter.summarize_stdout:
        return stdout
    return _summarize_stdout(
        stdout,
        threshold=interpreter.stdout_summary_threshold,
        prefix_len=interpreter.stdout_summary_prefix_len,
    )


def _drain_or_flush_stdin_impl(interpreter: ModalInterpreter) -> None:
    if interpreter._stdin is None:
        raise CodeInterpreterError("Sandbox input stream is not initialized")

    def _drain_or_flush() -> None:
        drain = getattr(interpreter._stdin, "drain", None)
        if callable(drain):
            drain_aio = getattr(drain, "aio", None)
            if callable(drain_aio):
                asyncio.run(drain_aio())
                return
            drain()
            return
        flush = getattr(interpreter._stdin, "flush", None)
        if callable(flush):
            flush()

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        _drain_or_flush()
        return

    thread_exc: Exception | None = None

    def _run_async_drain() -> None:
        nonlocal thread_exc
        try:
            _drain_or_flush()
        except Exception as exc:
            thread_exc = exc

    drain_thread = threading.Thread(
        target=_run_async_drain,
        name="modal-stdin-drain",
        daemon=True,
    )
    drain_thread.start()
    drain_thread.join()
    if thread_exc is not None:
        raise thread_exc


def _write_line_impl(interpreter: ModalInterpreter, payload: dict[str, Any]) -> None:
    if interpreter._stdin is None:
        raise CodeInterpreterError("Sandbox input stream is not initialized")
    interpreter._stdin.write(json.dumps(payload) + "\n")
    interpreter._drain_or_flush_stdin()


def _is_recoverable_exec_channel_error_impl(exc: Exception) -> bool:
    text = str(exc).lower()
    recoverable_signatures = (
        "failed to write to exec stdin",
        "broken pipe",
        "sandbox input stream is not initialized",
        "sandbox process exited unexpectedly",
    )
    return any(sig in text for sig in recoverable_signatures)


def _is_recoverable_start_error_impl(exc: Exception) -> bool:
    text = str(exc).lower()
    recoverable_signatures = (
        "timed out",
        "temporarily unavailable",
        "connection reset",
        "connection aborted",
        "service unavailable",
        "rate limit",
        "429",
        "resource exhausted",
    )
    return any(sig in text for sig in recoverable_signatures)


@contextmanager
def _execution_profile_impl(interpreter: ModalInterpreter, profile: ExecutionProfile):
    previous = interpreter.default_execution_profile
    interpreter.default_execution_profile = profile
    try:
        yield interpreter
    finally:
        interpreter.default_execution_profile = previous


def _execute_impl(
    interpreter: ModalInterpreter,
    code: str,
    variables: dict[str, Any] | None = None,
    *,
    execution_profile: ExecutionProfile | None = None,
) -> str | FinalOutput:
    safe_vars: dict[str, Any] = {}
    for key, value in (variables or {}).items():
        try:
            json.dumps(value)
            safe_vars[key] = value
        except TypeError:
            safe_vars[key] = str(value)

    request_payload = {
        "code": code,
        "variables": safe_vars,
        "tool_names": interpreter._tool_names(),
        "output_names": interpreter._output_names(),
        "execution_profile": (
            execution_profile or interpreter.default_execution_profile
        ).value,
    }
    profile_value = str(request_payload["execution_profile"])
    code_hash, code_preview = _summarize_code_impl(code)
    started_at = time.time()
    _emit_execution_event_impl(
        interpreter,
        start_event_data(
            execution_profile=profile_value,
            code_hash=code_hash,
            code_preview=code_preview,
        ),
    )

    max_attempts = 3
    for attempt in range(max_attempts):
        if interpreter._sandbox is None:
            try:
                interpreter.start()
            except Exception as exc:
                can_retry = attempt < (max_attempts - 1) and (
                    interpreter._is_recoverable_start_error(exc)
                )
                if can_retry:
                    interpreter.shutdown()
                    time.sleep(0.25 * (2**attempt))
                    continue
                raise CodeInterpreterError(
                    f"[sandbox_unavailable] Failed to start sandbox: {exc}"
                ) from exc

        try:
            interpreter._write_line(request_payload)
            deadline = (
                time.monotonic() + interpreter.execute_timeout
                if interpreter.execute_timeout
                else None
            )

            while True:
                remaining = (
                    None if deadline is None else max(0.0, deadline - time.monotonic())
                )
                if remaining is not None and remaining <= 0:
                    interpreter.shutdown()
                    raise CodeInterpreterError(
                        f"Timed out waiting for sandbox response after {interpreter.execute_timeout}s"
                    )

                try:
                    if interpreter._stdout_queue is None:
                        raise CodeInterpreterError(
                            "Sandbox output queue is not initialized"
                        )
                    line = interpreter._stdout_queue.get(timeout=remaining)
                except queue.Empty as exc:
                    interpreter.shutdown()
                    raise CodeInterpreterError(
                        f"Timed out waiting for sandbox response after {interpreter.execute_timeout}s"
                    ) from exc

                if line is None:
                    stderr_tail = ""
                    try:
                        if interpreter._stderr_iter is not None:
                            stderr_tail = "".join(list(interpreter._stderr_iter)[:50])
                    except Exception:
                        stderr_tail = ""
                    msg = "Modal sandbox process exited unexpectedly."
                    if stderr_tail:
                        msg += f"\nStderr: {_redact_sensitive_text(stderr_tail)}"
                    raise CodeInterpreterError(msg)

                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if "tool_call" in message:
                    call = message["tool_call"] or {}
                    name = call.get("name")
                    args = call.get("args") or []
                    kwargs = call.get("kwargs") or {}
                    try:
                        if name == "llm_query":
                            result = interpreter.llm_query(*args, **kwargs)
                        elif name == "llm_query_batched":
                            result = interpreter.llm_query_batched(*args, **kwargs)
                        elif name and name in interpreter._tools:
                            result = interpreter._tools[name](*args, **kwargs)
                        else:
                            raise CodeInterpreterError(f"Unknown tool: {name}")

                        try:
                            json.dumps(result)
                            reply = {"tool_result": result}
                        except TypeError:
                            reply = {"tool_result": str(result)}
                    except Exception as exc:
                        reply = {"tool_error": f"{type(exc).__name__}: {exc}"}

                    interpreter._write_line(reply)
                    continue

                if "stdout" in message or "stderr" in message or "final" in message:
                    stdout = message.get("stdout", "") or ""
                    stderr = message.get("stderr", "") or ""
                    final_obj = message.get("final")

                    if final_obj is not None:
                        output_keys = (
                            list(final_obj.keys())[:50]
                            if isinstance(final_obj, dict)
                            else None
                        )
                        _emit_execution_event_impl(
                            interpreter,
                            complete_event_data(
                                started_at=started_at,
                                execution_profile=profile_value,
                                code_hash=code_hash,
                                code_preview=code_preview,
                                success=True,
                                result_kind="final_output",
                                output_keys=output_keys,
                            ),
                        )
                        return FinalOutput(final_obj)

                    if stderr:
                        summarized_stdout = interpreter._summarize_stdout(stdout)
                        _emit_execution_event_impl(
                            interpreter,
                            complete_event_data(
                                started_at=started_at,
                                execution_profile=profile_value,
                                code_hash=code_hash,
                                code_preview=code_preview,
                                success=False,
                                result_kind="stderr",
                                stderr_preview=_redact_sensitive_text(stderr),
                            ),
                        )
                        return (
                            summarized_stdout
                            + ("\n" if summarized_stdout else "")
                            + stderr
                        )

                    stdout_preview = interpreter._summarize_stdout(stdout)
                    _emit_execution_event_impl(
                        interpreter,
                        complete_event_data(
                            started_at=started_at,
                            execution_profile=profile_value,
                            code_hash=code_hash,
                            code_preview=code_preview,
                            success=True,
                            result_kind="stdout",
                            stdout_preview=stdout_preview,
                        ),
                    )
                    return stdout_preview
        except Exception as exc:
            can_retry = attempt < (max_attempts - 1) and (
                interpreter._is_recoverable_exec_channel_error(exc)
            )
            if can_retry:
                interpreter.shutdown()
                time.sleep(0.25 * (2**attempt))
                continue
            _emit_execution_event_impl(
                interpreter,
                complete_event_data(
                    started_at=started_at,
                    execution_profile=profile_value,
                    code_hash=code_hash,
                    code_preview=code_preview,
                    success=False,
                    result_kind="exception",
                    error_type=type(exc).__name__,
                    error=str(exc),
                ),
            )
            raise

    _emit_execution_event_impl(
        interpreter,
        complete_event_data(
            started_at=started_at,
            execution_profile=profile_value,
            code_hash=code_hash,
            code_preview=code_preview,
            success=False,
            result_kind="retry_exhausted",
        ),
    )
    raise CodeInterpreterError(
        "[sandbox_unavailable] Unexpected execute retry exhaustion"
    )


def _build_default_image(
    *, python_version: str, pip_packages: Sequence[str]
) -> modal.Image:
    """Build a default Modal image for sandbox execution.

    Args:
        python_version: Python version string (e.g., "3.13").
        pip_packages: Sequence of pip packages to install.

    Returns:
        A configured Modal Image based on Debian slim.
    """
    return modal.Image.debian_slim(python_version=python_version).pip_install(
        *pip_packages
    )


class ModalInterpreter(LLMQueryMixin, VolumeOpsMixin):
    """DSPy CodeInterpreter implementation backed by a Modal sandbox process.

    This interpreter executes Python code in an isolated Modal sandbox,
    maintaining state across executions and supporting tool registration.
    It implements the CodeInterpreter interface required by DSPy's RLM.

    Features:
        - Isolated execution environment via Modal Sandbox
        - Stateful globals that persist across code executions
        - Tool registration for custom function calls
        - Built-in RLM tools: llm_query, llm_query_batched (with max_llm_calls limit)
        - Volume support for persistent file storage
        - Secret management for API keys
        - Automatic sensitive data redaction in logs
        - Configurable timeouts for sandbox and execution
        - Metadata-only stdout history to prevent context pollution (RLM paper Section 2)

    Lifecycle:
        1. Initialize with configuration (image, secrets, volumes, etc.)
        2. Call start() to create the sandbox and start the driver process
        3. Call execute() one or more times to run code
        4. Call shutdown() to terminate resources (or use context manager)

    Args:
        image: Optional custom Modal Image. If not provided, a default
            Debian slim image with numpy and pandas is used.
        app: Optional existing Modal App. If not provided, looked up by app_name.
        secrets: Optional list of Modal Secrets. Defaults to [Secret.from_name(secret_name)].
        timeout: Sandbox lifetime timeout in seconds (default: 600).
        idle_timeout: Optional idle timeout for the sandbox.
        execute_timeout: Timeout for individual execute() calls (default: same as timeout).
        app_name: Name for Modal App lookup/creation (default: "dspy-rlm-interpreter").
        secret_name: Default secret name if secrets not provided (default: "LITELLM").
        image_python_version: Python version for default image (default: "3.13").
        image_pip_packages: Packages for default image (default: ("numpy", "pandas")).
        volume_name: Optional Modal Volume name for persistent storage.
        volume_mount_path: Mount path for volume inside sandbox (default: "/data").
        summarize_stdout: Whether to summarize long stdout to prevent context
            window pollution (default: True). Per RLM paper Section 2.
        stdout_summary_threshold: Character threshold above which stdout is
            summarized (default: 500).
        stdout_summary_prefix_len: Number of characters to include in summary
            prefix (default: 200).
        sub_lm: Optional LM for llm_query/llm_query_batched calls. Defaults to
            dspy.settings.lm. Allows using a different (e.g., cheaper) model
            for sub-queries.
        max_llm_calls: Maximum number of sub-LLM calls (llm_query/
            llm_query_batched) allowed per session (default: 50).
        llm_call_timeout: Timeout in seconds for individual LLM calls
            (default: 60). Prevents hung calls from blocking indefinitely.

    Example:
        >>> interpreter = ModalInterpreter(timeout=300, volume_name="my-data")
        >>> interpreter.start()
        >>> result = interpreter.execute("print('Hello from sandbox')")
        >>> interpreter.shutdown()

        Or using the context manager pattern:
        >>> with ModalInterpreter() as interp:
        ...     result = interp.execute("x = 1 + 1")
    """

    def __init__(
        self,
        *,
        image: modal.Image | None = None,
        app: modal.App | None = None,
        secrets: list[modal.Secret] | None = None,
        timeout: int = 600,
        idle_timeout: int | None = None,
        execute_timeout: int | None = None,
        app_name: str = "dspy-rlm-interpreter",
        secret_name: str = "LITELLM",
        image_python_version: str = "3.13",
        image_pip_packages: Sequence[str] = ("numpy", "pandas"),
        volume_name: str | None = None,
        volume_mount_path: str = "/data",
        summarize_stdout: bool = True,
        stdout_summary_threshold: int = 10000,
        stdout_summary_prefix_len: int = 200,
        sub_lm: dspy.LM | None = None,
        max_llm_calls: int = 50,
        llm_call_timeout: int = 60,
        default_execution_profile: ExecutionProfile = ExecutionProfile.RLM_DELEGATE,
        async_execute: bool = True,
    ) -> None:
        # LLMQueryMixin attributes
        self.sub_lm = sub_lm
        self.max_llm_calls = max_llm_calls
        self.llm_call_timeout = llm_call_timeout
        self._llm_call_count = 0
        self._llm_call_lock = threading.Lock()
        self._sub_lm_executor = None
        self._sub_lm_executor_lock = threading.Lock()

        # VolumeOpsMixin attributes
        self.volume_name = volume_name
        self.volume_mount_path = volume_mount_path
        self._volume: modal.Volume | None = None

        # Interpreter-specific attributes
        self.image = image or _build_default_image(
            python_version=image_python_version, pip_packages=image_pip_packages
        )
        self._app_obj = app
        self._app_name = app_name
        self.secrets = secrets or [modal.Secret.from_name(secret_name)]
        self.timeout = timeout
        self.idle_timeout = idle_timeout
        self.execute_timeout = execute_timeout or timeout
        self.default_execution_profile = default_execution_profile
        self.async_execute = async_execute

        # Metadata-only history configuration (RLM paper Section 2)
        self.summarize_stdout = summarize_stdout
        self.stdout_summary_threshold = stdout_summary_threshold
        self.stdout_summary_prefix_len = stdout_summary_prefix_len

        self.output_fields: list[dict] | None = None
        self._tools_registered = False

        self._sandbox: modal.Sandbox | None = None
        self._proc = None
        self._stdin = None
        self._stdout_iter: Iterator[str] | None = None
        self._stdout_queue: queue.Queue[str | None] | None = None
        self._stdout_reader_thread: threading.Thread | None = None
        self._stderr_iter: Iterator[str] | None = None
        self._tools: dict[str, Callable[..., Any]] = {}
        self.execution_event_callback: Callable[[dict[str, Any]], None] | None = None

    @staticmethod
    def _summarize_code(code: str) -> tuple[str, str]:
        """Return deterministic code hash and compact preview text."""
        return _summarize_code_impl(code)

    def _emit_execution_event(self, payload: dict[str, Any]) -> None:
        """Best-effort execution hook dispatch for observability callbacks."""
        from .interpreter_events import InterpreterExecutionEventData

        event = InterpreterExecutionEventData(
            phase=str(payload.get("phase", "")),
            timestamp=float(payload.get("timestamp", 0.0)),
            execution_profile=str(payload.get("execution_profile", "")),
            code_hash=str(payload.get("code_hash", "")),
            code_preview=str(payload.get("code_preview", "")),
            duration_ms=payload.get("duration_ms"),
            success=payload.get("success"),
            result_kind=payload.get("result_kind"),
            output_keys=payload.get("output_keys"),
            stdout_preview=payload.get("stdout_preview"),
            stderr_preview=payload.get("stderr_preview"),
            error_type=payload.get("error_type"),
            error=payload.get("error"),
        )
        _emit_execution_event_impl(self, event)

    def _start_stdout_reader(self) -> None:
        """Start a background thread to read sandbox stdout."""
        _start_stdout_reader_impl(self)

    @property
    def tools(self) -> dict[str, Callable[..., Any]]:
        """Dictionary of registered tools available to sandboxed code."""
        return self._tools

    @tools.setter
    def tools(self, value: dict[str, Callable[..., Any]]) -> None:
        self._tools = value

    def _resolve_app(self) -> modal.App:
        """Return a fresh App handle."""
        return _resolve_app_impl(self)

    async def _aresolve_app(self) -> modal.App:
        """Return a fresh App handle (async)."""
        return await _aresolve_app_impl(self)

    @staticmethod
    def _module_source_for_sandbox(module: Any) -> str:
        """Return module source with future-import lines stripped for embedding."""
        return _module_source_for_sandbox_impl(module)

    def _build_driver_command_and_sandbox_kwargs(
        self, *, app: modal.App
    ) -> tuple[str, dict[str, Any]]:
        """Build sandbox driver command and kwargs shared by start/astart."""
        return _build_driver_command_and_sandbox_kwargs_impl(self, app=app)

    def start(self) -> None:
        """Start the Modal sandbox and initialize the driver process."""
        _start_impl(self)

    async def astart(self) -> None:
        """Start the Modal sandbox and initialize the driver process (async)."""
        await _astart_impl(self)

    def _tool_names(self) -> list[str]:
        """Get the list of registered tool names."""
        return _tool_names_impl(self)

    def _output_names(self) -> list[str]:
        """Get the list of output field names."""
        return _output_names_impl(self)

    def _summarize_stdout(self, stdout: str) -> str:
        """Summarize stdout output to prevent context window pollution."""
        return _summarize_stdout_impl(self, stdout)

    def _drain_or_flush_stdin(self) -> None:
        """Flush sandbox stdin, preferring Modal's async drain when available.

        ``aexecute()`` dispatches ``execute()`` to a worker thread via
        ``asyncio.to_thread(...)``. In that thread there is no running event loop,
        so bridging to Modal's async stream API with ``asyncio.run(...)`` is safe
        and avoids Modal's AsyncUsageWarning for blocking ``drain()`` calls.
        """
        _drain_or_flush_stdin_impl(self)

    def _write_line(self, payload: dict[str, Any]) -> None:
        """Write a JSON payload to the sandbox stdin."""
        _write_line_impl(self, payload)

    @staticmethod
    def _is_recoverable_exec_channel_error(exc: Exception) -> bool:
        """Return ``True`` when an exec transport error is likely transient."""
        return _is_recoverable_exec_channel_error_impl(exc)

    @staticmethod
    def _is_recoverable_start_error(exc: Exception) -> bool:
        """Return ``True`` when sandbox startup failures are likely transient."""
        return _is_recoverable_start_error_impl(exc)

    def execution_profile(self, profile: ExecutionProfile):
        """Temporarily override the default execution profile."""
        return _execution_profile_impl(self, profile)

    def build_delegate_child(self, *, remaining_llm_budget: int) -> ModalInterpreter:
        """Build a child interpreter for recursive RLM delegation."""
        child = ModalInterpreter(
            image=self.image,
            app=getattr(self, "_app_obj", None),
            secrets=list(self.secrets),
            timeout=self.timeout,
            idle_timeout=self.idle_timeout,
            execute_timeout=self.execute_timeout,
            app_name=getattr(self, "_app_name", "dspy-rlm-interpreter"),
            volume_name=self.volume_name,
            volume_mount_path=self.volume_mount_path,
            summarize_stdout=self.summarize_stdout,
            stdout_summary_threshold=self.stdout_summary_threshold,
            stdout_summary_prefix_len=self.stdout_summary_prefix_len,
            sub_lm=self.sub_lm,
            max_llm_calls=remaining_llm_budget,
            llm_call_timeout=self.llm_call_timeout,
            default_execution_profile=ExecutionProfile.RLM_DELEGATE,
            async_execute=self.async_execute,
        )
        setattr(
            child,
            "_check_and_increment_llm_calls",
            self._check_and_increment_llm_calls,
        )
        return child

    def execute(
        self,
        code: str,
        variables: dict[str, Any] | None = None,
        *,
        execution_profile: ExecutionProfile | None = None,
    ) -> str | FinalOutput:
        """Execute Python code in the Modal sandbox."""
        return _execute_impl(
            self,
            code,
            variables,
            execution_profile=execution_profile,
        )

    async def aexecute(
        self,
        code: str,
        variables: dict[str, Any] | None = None,
        *,
        execution_profile: ExecutionProfile | None = None,
    ) -> str | FinalOutput:
        """Asynchronously execute Python code in the Modal sandbox."""
        if self.async_execute:
            return await asyncio.to_thread(
                self.execute,
                code,
                variables,
                execution_profile=execution_profile,
            )
        return self.execute(code, variables, execution_profile=execution_profile)

    def shutdown(self) -> None:
        """Terminate the sandbox and clean up all resources."""
        _shutdown_impl(self)

    async def ashutdown(self) -> None:
        """Terminate the sandbox and clean up all resources (async)."""
        await _ashutdown_impl(self)

    def __enter__(self) -> ModalInterpreter:
        """Start the interpreter and return it for use as a context manager."""
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        """Shutdown the interpreter on context manager exit."""
        self.shutdown()
        return False

    async def __aenter__(self) -> ModalInterpreter:
        """Async context manager entrypoint."""
        if self.async_execute:
            await self.astart()
        else:
            self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> bool:
        """Async context manager exitpoint."""
        if self.async_execute:
            await self.ashutdown()
        else:
            self.shutdown()
        return False
