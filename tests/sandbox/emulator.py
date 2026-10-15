"""A local stand-in for a Daytona sandbox's code_interpreter and fs, for transport tests.

Each context is its own Python subprocess (Daytona runs one worker process per context), state
persists across run_code calls, and every stdout write() reaches on_stdout as its own message, as
over Daytona's websocket. /workspace and the reply directory are mapped under a temporary root.
"""

from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from daytona.common.errors import DaytonaConnectionError, DaytonaTimeoutError

_DRIVER = r"""
import json, os, sys, traceback
channel = os.fdopen(os.dup(1), "w", buffering=1)
scope = {"__name__": "__main__"}

class Stream:
    def __init__(self, kind):
        self.kind = kind
    def write(self, text):
        if text:
            channel.write(json.dumps({"t": self.kind, "d": text}) + "\n")
            channel.flush()
        return len(text)
    def flush(self):
        pass
    def isatty(self):
        return False

for line in sys.stdin:
    request = json.loads(line)
    sys.stdout, sys.stderr = Stream("o"), Stream("e")
    error = None
    try:
        exec(compile(request["code"], "<cell>", "exec"), scope)
    except BaseException as exc:
        error = {"name": type(exc).__name__, "value": str(exc), "traceback": traceback.format_exc()}
    finally:
        sys.stdout, sys.stderr = sys.__stdout__, sys.__stderr__
    channel.write(json.dumps({"t": "done", "error": error}) + "\n")
    channel.flush()
"""


class _Context:
    def __init__(self) -> None:
        self.id = uuid.uuid4().hex
        self.process = subprocess.Popen(
            [sys.executable, "-c", _DRIVER],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        self.lines: queue.Queue[str | None] = queue.Queue()
        self.lock = threading.Lock()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            self.lines.put(line)
        self.lines.put(None)


class EmulatedInterpreter:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.contexts: dict[str, _Context] = {}
        self.run_calls = 0
        self.stdout_messages = 0

    def translate(self, text: str) -> str:
        return text.replace("/workspace", str(self.root / "workspace")).replace(
            "/tmp/fleet/replies", str(self.root / "replies")
        )

    def create_context(self) -> Any:
        context = _Context()
        self.contexts[context.id] = context
        return SimpleNamespace(id=context.id)

    def list_contexts(self) -> list[Any]:
        return [SimpleNamespace(id=key) for key, value in self.contexts.items() if value.process.poll() is None]

    def delete_context(self, context: Any) -> None:
        live = self.contexts.pop(context.id, None)
        if live is not None:
            live.process.kill()

    def run_code(
        self,
        code: str,
        *,
        context: Any = None,
        on_stdout: Any = None,
        on_stderr: Any = None,
        timeout: int | None = None,
        envs: Any = None,  # noqa: ARG002
    ) -> Any:
        self.run_calls += 1
        live = self.contexts[context.id]
        result = SimpleNamespace(stdout="", stderr="", error=None)
        with live.lock:
            assert live.process.stdin is not None
            live.process.stdin.write(json.dumps({"code": self.translate(code)}) + "\n")
            live.process.stdin.flush()
            deadline = time.monotonic() + timeout if timeout else None
            timed_out = False
            while True:
                wait = None if deadline is None else max(0.01, deadline - time.monotonic())
                try:
                    line = live.lines.get(timeout=wait)
                except queue.Empty:
                    os.kill(live.process.pid, signal.SIGINT)  # Daytona interrupts the cell and keeps the context
                    timed_out, deadline = True, None
                    continue
                if line is None:
                    raise DaytonaConnectionError("Failed to run code: worker process ended (close code 1001)")
                message = json.loads(line)
                if message["t"] == "o":
                    self.stdout_messages += 1
                    result.stdout += message["d"]
                    if on_stdout:
                        on_stdout(SimpleNamespace(output=message["d"]))
                elif message["t"] == "e":
                    result.stderr += message["d"]
                    if on_stderr:
                        on_stderr(SimpleNamespace(output=message["d"]))
                else:
                    if timed_out:
                        raise DaytonaTimeoutError("Execution timed out")
                    if message["error"]:
                        result.error = SimpleNamespace(**message["error"])
                    return result

    def close(self) -> None:
        for live in self.contexts.values():
            live.process.kill()


class EmulatedFs:
    def __init__(self, interpreter: EmulatedInterpreter) -> None:
        self.interpreter = interpreter
        self.uploads: list[str] = []

    def upload_file(self, data: bytes, path: str, timeout: int | None = None) -> None:  # noqa: ARG002
        target = Path(self.interpreter.translate(path))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        self.uploads.append(path)


def emulated_sandbox(root: Path) -> SimpleNamespace:
    (root / "workspace").mkdir(parents=True, exist_ok=True)
    interpreter = EmulatedInterpreter(root)
    return SimpleNamespace(
        id="emulated-sandbox", state="started", code_interpreter=interpreter, fs=EmulatedFs(interpreter)
    )
