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

import importlib.resources
import inspect
import json
import queue
import re
import threading
import time
from typing import Any, Callable, Iterator, Sequence

import modal
from dspy.primitives.code_interpreter import CodeInterpreterError, FinalOutput

from .driver import sandbox_driver


def _load_driver_source() -> str:
    """Load the sandbox_driver function source.

    Tries ``inspect.getsource`` first (works for normal installs).
    Falls back to reading the driver module file via ``importlib.resources``
    (works for zipped / compiled distributions where source introspection
    may fail).
    """
    try:
        return inspect.getsource(sandbox_driver)
    except OSError:
        ref = importlib.resources.files("fleet_rlm").joinpath("driver.py")
        source = ref.read_text(encoding="utf-8")
        # Extract only the sandbox_driver function body (everything after
        # the module-level code preceding 'def sandbox_driver').
        marker = "def sandbox_driver"
        idx = source.find(marker)
        if idx == -1:
            raise RuntimeError(
                "Cannot locate sandbox_driver in driver.py source"
            )
        return source[idx:]


_DRIVER_SOURCE: str = _load_driver_source()


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
        - Volume support for persistent file storage
        - Secret management for API keys
        - Automatic sensitive data redaction in logs
        - Configurable timeouts for sandbox and execution

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
        image_python_version: str = "3.12",
        image_pip_packages: Sequence[str] = ("numpy", "pandas"),
        volume_name: str | None = None,
        volume_mount_path: str = "/data",
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

        driver_command = f"{_DRIVER_SOURCE}\n\nsandbox_driver()"

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
        """
        return list(self._tools.keys()) if self._tools else []

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
                    if not name or name not in self._tools:
                        raise CodeInterpreterError(f"Unknown tool: {name}")
                    result = self._tools[name](*args, **kwargs)
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

                if stderr:
                    return stdout + ("\n" if stdout else "") + stderr
                return stdout

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
