from __future__ import annotations

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


def _build_default_image(
    *, python_version: str, pip_packages: Sequence[str]
) -> modal.Image:
    return modal.Image.debian_slim(python_version=python_version).pip_install(
        *pip_packages
    )


class ModalInterpreter:
    """CodeInterpreter implementation backed by a Modal sandbox process."""

    def __init__(
        self,
        *,
        image: modal.Image | None = None,
        app: modal.App | None = None,
        secrets: list[modal.Secret] | None = None,
        timeout: int = 600,
        execute_timeout: int | None = None,
        app_name: str = "dspy-rlm-interpreter",
        secret_name: str = "LITELLM",
        image_python_version: str = "3.12",
        image_pip_packages: Sequence[str] = ("numpy", "pandas"),
    ) -> None:
        self.image = image or _build_default_image(
            python_version=image_python_version, pip_packages=image_pip_packages
        )
        self.app = app or modal.App.lookup(app_name, create_if_missing=True)
        self.secrets = secrets or [modal.Secret.from_name(secret_name)]
        self.timeout = timeout
        self.execute_timeout = execute_timeout or timeout

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

    @staticmethod
    def _redact_sensitive_text(text: str) -> str:
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
        if self._stdout_iter is None:
            return

        self._stdout_queue = queue.Queue()

        def _reader() -> None:
            try:
                for line in self._stdout_iter:
                    self._stdout_queue.put(line)
            finally:
                # Sentinel to signal EOF from sandbox process.
                self._stdout_queue.put(None)

        self._stdout_reader_thread = threading.Thread(target=_reader, daemon=True)
        self._stdout_reader_thread.start()

    @property
    def tools(self) -> dict[str, Callable[..., Any]]:
        return self._tools

    @tools.setter
    def tools(self, value: dict[str, Callable[..., Any]]) -> None:
        self._tools = value

    def start(self) -> None:
        if self._sandbox is not None:
            return

        driver_source = inspect.getsource(sandbox_driver)
        driver_command = f"{driver_source}\n\nsandbox_driver()"

        self._sandbox = modal.Sandbox.create(
            app=self.app, image=self.image, secrets=self.secrets
        )
        self._proc = self._sandbox.exec(
            "python", "-u", "-c", driver_command, bufsize=1, timeout=self.timeout
        )

        self._stdin = self._proc.stdin
        self._stdout_iter = iter(self._proc.stdout)
        self._stderr_iter = iter(getattr(self._proc, "stderr", []))
        self._start_stdout_reader()

    def _tool_names(self) -> list[str]:
        return list(self._tools.keys()) if self._tools else []

    def _output_names(self) -> list[str]:
        if not self.output_fields:
            return []
        return [
            field["name"]
            for field in self.output_fields
            if isinstance(field, dict) and field.get("name")
        ]

    def _write_line(self, payload: dict[str, Any]) -> None:
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

    def shutdown(self) -> None:
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
