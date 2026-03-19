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
import queue
import threading
from typing import Any, Callable, Iterator, Sequence

import dspy
import modal
from dspy.primitives import FinalOutput

from fleet_rlm.core.execution.profiles import ExecutionProfile  # noqa: F811
from fleet_rlm.core.tools.llm_tools import LLMQueryMixin
from fleet_rlm.core.tools.volume_ops import VolumeOpsMixin

from .interpreter_events import emit_execution_event as _emit_execution_event_impl
from .interpreter_events import summarize_code as _summarize_code_impl
from .interpreter_lifecycle import (
    aresolve_app as _aresolve_app_impl,
    ashutdown as _ashutdown_impl,
    astart as _astart_impl,
    build_driver_command_and_sandbox_kwargs as _build_driver_command_and_sandbox_kwargs_impl,
    module_source_for_sandbox as _module_source_for_sandbox_impl,
    resolve_app as _resolve_app_impl,
    shutdown as _shutdown_impl,
    start as _start_impl,
    start_stdout_reader as _start_stdout_reader_impl,
)
from .interpreter_session import (
    drain_or_flush_stdin as _drain_or_flush_stdin_impl,
    execute as _execute_impl,
    execution_profile as _execution_profile_impl,
    is_recoverable_exec_channel_error as _is_recoverable_exec_channel_error_impl,
    is_recoverable_start_error as _is_recoverable_start_error_impl,
    output_names as _output_names_impl,
    summarize_stdout as _summarize_stdout_impl,
    tool_names as _tool_names_impl,
    write_line as _write_line_impl,
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

    def build_delegate_child(self, *, remaining_llm_budget: int) -> "ModalInterpreter":
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

    def __enter__(self) -> "ModalInterpreter":
        """Start the interpreter and return it for use as a context manager."""
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        """Shutdown the interpreter on context manager exit."""
        self.shutdown()
        return False

    async def __aenter__(self) -> "ModalInterpreter":
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
