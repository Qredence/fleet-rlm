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

import inspect
import json
import queue
import re
import threading
import time
from concurrent.futures import (
    ThreadPoolExecutor,
    as_completed,
    TimeoutError as FutureTimeoutError,
)
from typing import Any, Callable, Iterator, Sequence

import dspy
import modal
from dspy.primitives.code_interpreter import CodeInterpreterError, FinalOutput

from .driver import sandbox_driver


def _build_default_image(
    *, python_version: str, pip_packages: Sequence[str]
) -> modal.Image:
    """Build a default Modal image for sandbox execution.

    Args:
        python_version: Python version string (e.g., "3.12").
        pip_packages: Sequence of pip packages to install.

    Returns:
        A configured Modal Image based on Debian slim.
    """
    return modal.Image.debian_slim(python_version=python_version).pip_install(
        *pip_packages
    )


class ModalInterpreter:
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
        image_python_version: Python version for default image (default: "3.12").
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

    Metadata-Only History:
        As described in the RLM paper (Section 2), long stdout outputs can
        pollute the LLM's context window during recursive iterations. When
        ``summarize_stdout=True`` (default) and output exceeds the threshold,
        the interpreter returns metadata instead:

            [Output: 1,247 chars, 42 lines]
            Prefix: "First 200 chars of output..."

        This keeps the context window clean while still providing useful
        information about what was produced. Errors are always shown in full.

    Built-in RLM Tools:
        The interpreter provides built-in tools for recursive LLM calls:

        - ``llm_query(prompt: str) -> str``: Query a sub-LLM for semantic
          analysis. Counts against ``max_llm_calls`` limit.

        - ``llm_query_batched(prompts: list[str]) -> list[str]]: Query
          multiple prompts concurrently. Each prompt counts against
          ``max_llm_calls`` limit.

        These tools enable the RLM pattern where code can call sub-LLMs
        for semantic tasks while the parent LLM handles the orchestration.
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
        image_python_version: str = "3.12",
        image_pip_packages: Sequence[str] = ("numpy", "pandas"),
        volume_name: str | None = None,
        volume_mount_path: str = "/data",
        summarize_stdout: bool = True,
        stdout_summary_threshold: int = 500,
        stdout_summary_prefix_len: int = 200,
        sub_lm: dspy.LM | None = None,
        max_llm_calls: int = 50,
        llm_call_timeout: int = 60,
    ) -> None:
        self.image = image or _build_default_image(
            python_version=image_python_version, pip_packages=image_pip_packages
        )
        # Store both app object and name; defer App.lookup() to start()
        # to avoid stale _client references across Jupyter cells.
        self._app_obj = app
        self._app_name = app_name
        self.secrets = secrets or [modal.Secret.from_name(secret_name)]
        self.timeout = timeout
        self.idle_timeout = idle_timeout
        self.execute_timeout = execute_timeout or timeout
        self.volume_name = volume_name
        self.volume_mount_path = volume_mount_path

        # Sub-LM configuration for llm_query/llm_query_batched (DSPy RLM pattern)
        self.sub_lm = sub_lm
        self.max_llm_calls = max_llm_calls
        self.llm_call_timeout = llm_call_timeout
        self._llm_call_count = 0
        self._llm_call_lock = threading.Lock()
        self._sub_lm_executor: ThreadPoolExecutor | None = None
        self._sub_lm_executor_lock = threading.Lock()

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
        self._volume: modal.Volume | None = None

    @staticmethod
    def _redact_sensitive_text(text: str) -> str:
        """Redact potentially sensitive information from text.

        Scans for and masks:
            - API keys (sk-... format)
            - Authorization headers with Bearer tokens
            - Key/value pairs containing api_key, token, or secret

        Args:
            text: The text to redact.

        Returns:
            The text with sensitive values replaced by ***REDACTED***.
        """
        redacted = text
        # Redact likely API keys/tokens.
        redacted = re.sub(r"sk-[A-Za-z0-9_-]{8,}", "sk-***REDACTED***", redacted)
        redacted = re.sub(
            r"(Authorization\s*:\s*Bearer\s+)[^\s]+",
            r"\1***REDACTED***",
            redacted,
            flags=re.IGNORECASE,
        )
        redacted = re.sub(
            r"((?:api[_-]?key|token|secret)\s*[=:]\s*)[^\s'\"\\]+",
            r"\1***REDACTED***",
            redacted,
            flags=re.IGNORECASE,
        )
        return redacted

    def _start_stdout_reader(self) -> None:
        """Start a background thread to read sandbox stdout.

        Creates a queue and daemon thread that continuously reads lines
        from the sandbox stdout iterator and places them in the queue.
        This allows non-blocking reads with timeout support.

        The thread puts None as a sentinel value when EOF is reached.
        """
        if self._stdout_iter is None:
            return

        self._stdout_queue = queue.Queue()

        # Capture references to avoid race conditions with shutdown()
        # clearing self._stdout_queue and self._stdout_iter.
        q = self._stdout_queue
        it = self._stdout_iter

        def _reader() -> None:
            try:
                for line in it:
                    q.put(line)
            except Exception:
                # Ignore errors reading from closed stream during shutdown
                pass
            finally:
                # Sentinel to signal EOF from sandbox process.
                q.put(None)

        self._stdout_reader_thread = threading.Thread(target=_reader, daemon=True)
        self._stdout_reader_thread.start()

    @property
    def tools(self) -> dict[str, Callable[..., Any]]:
        """Dictionary of registered tools available to sandboxed code.

        Tools are Python callables that can be invoked from within the
        sandbox via the JSON protocol. They are typically used by the RLM
        to perform actions like file operations or API calls.

        Returns:
            Dictionary mapping tool names to callable functions.

        Example:
            >>> interpreter.tools = {"my_tool": lambda x: x * 2}
        """
        return self._tools

    @tools.setter
    def tools(self, value: dict[str, Callable[..., Any]]) -> None:
        """Set the tools dictionary.

        Args:
            value: Dictionary mapping tool names to callable functions.
        """
        self._tools = value

    def _resolve_app(self) -> modal.App:
        """Return a fresh App handle.

        ``modal.App.lookup()`` returns a transient handle whose internal
        ``_client`` expires between Jupyter cells.  We call it here —
        right before ``Sandbox.create()`` — so the client is always live.

        Returns:
            A Modal App handle, either from the stored reference or looked up by name.
        """
        if self._app_obj is not None:
            return self._app_obj
        return modal.App.lookup(self._app_name, create_if_missing=True)

    def _resolve_volume(self) -> modal.Volume:
        """Return a Volume V2 handle (created lazily if needed).

        Returns:
            A Modal Volume V2 handle, creating the volume if it doesn't exist.

        Raises:
            ValueError: If volume_name was not configured.
        """
        if self.volume_name is None:
            raise ValueError("volume_name was not configured")

        return modal.Volume.from_name(
            self.volume_name, create_if_missing=True, version=2
        )

    def start(self) -> None:
        """Start the Modal sandbox and initialize the driver process.

        Creates a Modal Sandbox with the configured image, secrets, and volumes,
        then starts the sandbox driver process that will execute code via the
        JSON protocol. This method is idempotent - calling it multiple times
        has no effect if the sandbox is already running.

        Raises:
            modal.error.Error: If sandbox creation fails.
        """
        if self._sandbox is not None:
            return

        # Reset per-session sub-LLM call counter on fresh sandbox start.
        with self._llm_call_lock:
            self._llm_call_count = 0

        driver_source = inspect.getsource(sandbox_driver)
        driver_command = f"{driver_source}\n\nsandbox_driver()"

        # Resolve App at sandbox-creation time to keep _client fresh.
        app = self._resolve_app()

        sandbox_kwargs: dict[str, Any] = {
            "app": app,
            "image": self.image,
            "secrets": self.secrets,
            "timeout": self.timeout,
        }
        if self.idle_timeout is not None:
            sandbox_kwargs["idle_timeout"] = self.idle_timeout
        if self.volume_name:
            self._volume = self._resolve_volume()
            sandbox_kwargs["volumes"] = {self.volume_mount_path: self._volume}

        self._sandbox = modal.Sandbox.create(**sandbox_kwargs)
        self._proc = self._sandbox.exec(
            "python", "-u", "-c", driver_command, bufsize=1, timeout=self.timeout
        )

        self._stdin = self._proc.stdin
        self._stdout_iter = iter(self._proc.stdout)
        self._stderr_iter = iter(getattr(self._proc, "stderr", []))
        self._start_stdout_reader()

    def _tool_names(self) -> list[str]:
        """Get the list of registered tool names.

        Returns:
            List of tool names available to sandboxed code.
            Always includes built-in RLM tools: llm_query, llm_query_batched.
        """
        tools = ["llm_query", "llm_query_batched"]  # Built-in RLM tools
        if self._tools:
            tools.extend(self._tools.keys())
        return tools

    def _output_names(self) -> list[str]:
        """Get the list of output field names.

        Returns:
            List of output field names from the output_fields configuration.
        """
        if not self.output_fields:
            return []
        return [
            field["name"]
            for field in self.output_fields
            if isinstance(field, dict) and field.get("name")
        ]

    def _summarize_stdout(self, stdout: str) -> str:
        """Summarize stdout output to prevent context window pollution.

        As described in the RLM paper (Section 2), long stdout outputs can
        pollute the LLM's context window during recursive iterations. This
        method returns metadata about the output instead of the full content
        when the output exceeds the configured threshold.

        The summary includes:
            - Total character count
            - Line count
            - A short prefix of the output (first N chars)
            - Indication if output was truncated

        Args:
            stdout: The stdout output from sandbox execution.

        Returns:
            Either the original stdout (if short) or a metadata summary
            (if long and summarize_stdout is enabled).

        Example:
            Short output (under threshold):
                "Hello, world!"

            Long output (over threshold):
                "[Output: 1,247 chars, 42 lines]\\n"
                "Prefix: \\"First 200 chars of output...\\""
        """
        if not self.summarize_stdout:
            return stdout

        if len(stdout) <= self.stdout_summary_threshold:
            return stdout

        # Calculate metadata
        total_chars = len(stdout)
        line_count = stdout.count("\n")
        prefix_len = min(self.stdout_summary_prefix_len, len(stdout))
        prefix = stdout[:prefix_len]

        # Escape newlines in prefix for cleaner display
        prefix_display = prefix.replace("\n", "\\n")

        # Truncate prefix if it was cut mid-line
        if len(prefix) == prefix_len and prefix_len < len(stdout):
            prefix_display += "..."

        summary = (
            f"[Output: {total_chars:,} chars, {line_count} lines]\n"
            f'Prefix: "{prefix_display}"'
        )

        return summary

    def _check_and_increment_llm_calls(self, n: int = 1) -> None:
        """Check and increment the LLM call counter.

        Args:
            n: Number of calls to add (default: 1 for single query,
               len(prompts) for batched queries).

        Raises:
            RuntimeError: If the call would exceed max_llm_calls limit.
        """
        with self._llm_call_lock:
            if self._llm_call_count + n > self.max_llm_calls:
                raise RuntimeError(
                    f"LLM call limit exceeded: {self._llm_call_count} + {n} > {self.max_llm_calls}. "
                    f"Use Python code for aggregation instead of making more LLM calls."
                )
            self._llm_call_count += n

    def _query_sub_lm(self, prompt: str) -> str:
        """Query the sub-LM with a prompt string.

        Args:
            prompt: The prompt to send to the sub-LM.

        Returns:
            The response text from the sub-LM.

        Raises:
            RuntimeError: If no LM is configured or if the call times out.
        """
        target_lm = self.sub_lm if self.sub_lm is not None else dspy.settings.lm
        if target_lm is None:
            raise RuntimeError(
                "No LM configured. Use dspy.configure(lm=...) or pass sub_lm to ModalInterpreter."
            )

        # Execute LM call with timeout to prevent hangs
        def _execute_lm():
            response = target_lm(prompt)
            if isinstance(response, list) and response:
                item = response[0]
                if isinstance(item, dict) and "text" in item:
                    return item["text"]
                return str(item)
            return str(response)

        # Reuse a single-worker executor to avoid creating unbounded background
        # threads when repeated calls time out.
        with self._sub_lm_executor_lock:
            if self._sub_lm_executor is None:
                self._sub_lm_executor = ThreadPoolExecutor(max_workers=1)
            executor = self._sub_lm_executor

        future = executor.submit(_execute_lm)
        try:
            return future.result(timeout=self.llm_call_timeout)
        except FutureTimeoutError as exc:
            future.cancel()
            raise RuntimeError(
                f"LLM call timed out after {self.llm_call_timeout}s. "
                "Consider increasing llm_call_timeout or checking API connectivity."
            ) from exc

    def llm_query(self, prompt: str) -> str:
        """Query a sub-LLM for semantic analysis.

        This is a built-in RLM tool that allows sandboxed code to make
        recursive LLM calls. Each call counts against max_llm_calls.

        Args:
            prompt: The prompt to send to the sub-LLM.

        Returns:
            The response text from the sub-LLM.

        Raises:
            ValueError: If prompt is empty.
            RuntimeError: If max_llm_calls would be exceeded.

        Example:
            >>> result = llm_query("Summarize this text in one sentence.")
        """
        if not prompt:
            raise ValueError("prompt cannot be empty")
        self._check_and_increment_llm_calls(1)
        return self._query_sub_lm(prompt)

    def llm_query_batched(self, prompts: list[str]) -> list[str]:
        """Query the sub-LLM with multiple prompts concurrently.

        This is a built-in RLM tool for making multiple LLM calls in parallel.
        Each prompt counts against max_llm_calls.

        Args:
            prompts: List of prompts to send to the sub-LLM.

        Returns:
            List of response texts, in the same order as prompts.

        Raises:
            RuntimeError: If max_llm_calls would be exceeded, or if any
                batched query fails.

        Example:
            >>> prompts = ["Summarize A", "Summarize B", "Summarize C"]
            >>> results = llm_query_batched(prompts)
        """
        if not prompts:
            return []
        self._check_and_increment_llm_calls(len(prompts))

        results: dict[int, str] = {}
        errors: list[tuple[int, Exception]] = []

        # Adaptive ThreadPool sizing: use min of max_llm_calls and 8, or batch size
        # This prevents over-allocation for small batches and under-utilization for large ones
        adaptive_workers = max(1, min(len(prompts), self.max_llm_calls, 8))

        with ThreadPoolExecutor(max_workers=adaptive_workers) as executor:
            future_to_idx = {
                executor.submit(self._query_sub_lm, p): i for i, p in enumerate(prompts)
            }
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                try:
                    results[idx] = future.result()
                except Exception as exc:
                    errors.append((idx, exc))
        if errors:
            errors.sort(key=lambda x: x[0])
            details = "; ".join(
                f"prompt[{idx}]: {type(exc).__name__}: {exc}" for idx, exc in errors
            )
            raise RuntimeError(
                f"llm_query_batched failed for {len(errors)}/{len(prompts)} prompts: {details}"
            ) from errors[0][1]
        return [results[i] for i in range(len(prompts))]

    def _write_line(self, payload: dict[str, Any]) -> None:
        """Write a JSON payload to the sandbox stdin.

        Args:
            payload: Dictionary to serialize and send to the sandbox.

        Raises:
            CodeInterpreterError: If the stdin stream is not initialized.
        """
        if self._stdin is None:
            raise CodeInterpreterError("Sandbox input stream is not initialized")
        self._stdin.write(json.dumps(payload) + "\n")
        if hasattr(self._stdin, "drain"):
            self._stdin.drain()
        elif hasattr(self._stdin, "flush"):
            self._stdin.flush()

    def execute(
        self, code: str, variables: dict[str, Any] | None = None
    ) -> str | FinalOutput:
        """Execute Python code in the Modal sandbox.

        Sends the code to the sandbox driver via the JSON protocol and
        waits for the response. Handles tool calls, variable serialization,
        and timeout management.

        If the sandbox is not running, it will be started automatically.

        Args:
            code: Python code to execute in the sandbox.
            variables: Optional dictionary of variables to inject into the
                sandbox's global namespace. Non-serializable values are
                converted to strings.

        Returns:
            Either a string (stdout/stderr output) or a FinalOutput object
            containing structured data from a SUBMIT() call.

        Raises:
            CodeInterpreterError: If the sandbox process exits unexpectedly,
                times out, or if there are communication errors.

        Example:
            >>> result = interpreter.execute("x = 1 + 1\\nprint(x)")
            >>> print(result)
            2
        """
        if self._sandbox is None:
            self.start()

        safe_vars: dict[str, Any] = {}
        for key, value in (variables or {}).items():
            try:
                json.dumps(value)
                safe_vars[key] = value
            except TypeError:
                safe_vars[key] = str(value)

        self._write_line(
            {
                "code": code,
                "variables": safe_vars,
                "tool_names": self._tool_names(),
                "output_names": self._output_names(),
            }
        )

        deadline = (
            time.monotonic() + self.execute_timeout if self.execute_timeout else None
        )

        while True:
            remaining = (
                None if deadline is None else max(0.0, deadline - time.monotonic())
            )
            if remaining is not None and remaining <= 0:
                self.shutdown()
                raise CodeInterpreterError(
                    f"Timed out waiting for sandbox response after {self.execute_timeout}s"
                )

            try:
                if self._stdout_queue is None:
                    raise CodeInterpreterError(
                        "Sandbox output queue is not initialized"
                    )
                line = self._stdout_queue.get(timeout=remaining)
            except queue.Empty as exc:
                self.shutdown()
                raise CodeInterpreterError(
                    f"Timed out waiting for sandbox response after {self.execute_timeout}s"
                ) from exc

            if line is None:
                stderr_tail = ""
                try:
                    if self._stderr_iter is not None:
                        stderr_tail = "".join(list(self._stderr_iter)[:50])
                except Exception:
                    stderr_tail = ""
                msg = "Modal sandbox process exited unexpectedly."
                if stderr_tail:
                    msg += f"\nStderr: {self._redact_sensitive_text(stderr_tail)}"
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
                    # Handle built-in RLM tools
                    if name == "llm_query":
                        result = self.llm_query(*args, **kwargs)
                    elif name == "llm_query_batched":
                        result = self.llm_query_batched(*args, **kwargs)
                    # Handle user-registered tools
                    elif name and name in self._tools:
                        result = self._tools[name](*args, **kwargs)
                    else:
                        raise CodeInterpreterError(f"Unknown tool: {name}")

                    try:
                        json.dumps(result)
                        reply = {"tool_result": result}
                    except TypeError:
                        reply = {"tool_result": str(result)}
                except Exception as exc:
                    reply = {"tool_error": f"{type(exc).__name__}: {exc}"}

                self._write_line(reply)
                continue

            if "stdout" in message or "stderr" in message or "final" in message:
                stdout = message.get("stdout", "") or ""
                stderr = message.get("stderr", "") or ""
                final_obj = message.get("final")

                if final_obj is not None:
                    return FinalOutput(final_obj)

                # Apply metadata-only history for long stdout (RLM paper Section 2)
                # Errors are always shown in full to aid debugging
                if stderr:
                    # Always show full stderr for debugging, but summarize stdout if long
                    summarized_stdout = self._summarize_stdout(stdout)
                    return (
                        summarized_stdout + ("\n" if summarized_stdout else "") + stderr
                    )
                return self._summarize_stdout(stdout)

    def commit(self) -> None:
        """Commit volume changes to persistent storage.

        Only works if a volume was specified at init. No-op otherwise.
        """
        if self._volume is not None:
            self._volume.commit()

    def reload(self) -> None:
        """Reload volume to see changes from other containers.

        Only works if a volume was specified at init. No-op otherwise.
        """
        if self._volume is not None:
            self._volume.reload()

    def upload_to_volume(
        self,
        local_dirs: dict[str, str] | None = None,
        local_files: dict[str, str] | None = None,
    ) -> None:
        """Upload local directories/files to the Modal Volume if they don't exist.

        Args:
            local_dirs: Mapping of local directory path → remote directory
                path on the volume.  E.g. ``{"rlm_content/dspy-knowledge": "/dspy-knowledge"}``
            local_files: Mapping of local file path → remote file path.
        """
        if not self.volume_name:
            raise ValueError("No volume_name configured on this interpreter.")

        vol = self._resolve_volume()

        def _exists(remote_path: str) -> bool:
            """Check if a remote file or directory exists."""
            remote_path = remote_path.rstrip("/")
            if not remote_path:  # Root always exists
                return True

            parent = "/"
            if "/" in remote_path:
                parent, name = remote_path.rsplit("/", 1)
                parent = parent or "/"
            else:
                name = remote_path

            try:
                # listdir returns FileEntry objects with a .path attribute (filename)
                for entry in vol.listdir(parent):
                    if entry.path == name:
                        return True
            except Exception:
                # Parent probably doesn't exist
                pass
            return False

        # Use force=True to handle cases where we proceed with upload,
        # ensuring no spurious FileExistsErrors from the batch mechanism itself.
        with vol.batch_upload(force=True) as batch:
            for local_dir, remote_dir in (local_dirs or {}).items():
                if _exists(remote_dir):
                    print(f"Volume: '{remote_dir}' exists, skipping upload.")
                    continue
                print(f"Volume: Uploading directory '{local_dir}' to '{remote_dir}'...")
                batch.put_directory(local_dir, remote_dir)

            for local_file, remote_file in (local_files or {}).items():
                if _exists(remote_file):
                    print(f"Volume: '{remote_file}' exists, skipping upload.")
                    continue
                print(f"Volume: Uploading file '{local_file}' to '{remote_file}'...")
                batch.put_file(local_file, remote_file)

    def shutdown(self) -> None:
        """Terminate the sandbox and clean up all resources.

        Safely terminates the Modal sandbox if it's running and clears
        all internal state. This method is idempotent and safe to call
        multiple times.
        """
        if self._sandbox is not None:
            try:
                self._sandbox.terminate()
            except Exception:
                pass

        self._sandbox = None
        self._proc = None
        self._stdin = None
        self._stdout_iter = None
        self._stdout_queue = None
        self._stdout_reader_thread = None
        self._stderr_iter = None
        self._volume = None
        with self._sub_lm_executor_lock:
            if self._sub_lm_executor is not None:
                self._sub_lm_executor.shutdown(wait=False, cancel_futures=True)
                self._sub_lm_executor = None

    def __enter__(self) -> "ModalInterpreter":
        """Start the interpreter and return it for use as a context manager.

        Enables the ``with ModalInterpreter() as interp:`` pattern for
        automatic resource cleanup.

        Returns:
            The started ModalInterpreter instance.

        Example:
            >>> with ModalInterpreter(timeout=120) as interp:
            ...     result = interp.execute("print('hello')")
        """
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        """Shutdown the interpreter on context manager exit.

        Always shuts down the sandbox, regardless of whether an
        exception occurred.

        Returns:
            False — exceptions are not suppressed.
        """
        self.shutdown()
        return False
