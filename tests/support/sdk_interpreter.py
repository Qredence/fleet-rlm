"""Offline SDK execution double; generated Python executes only in tests."""

from __future__ import annotations

import contextlib
import io
from types import SimpleNamespace
from typing import Any


class LocalSDKInterpreter:
    def __init__(self) -> None:
        self.contexts: list[dict[str, Any]] = []
        self.deleted: list[dict[str, Any]] = []
        self.calls: list[str] = []

    def create_context(self, *, request_timeout: float | None = None) -> dict[str, Any]:
        del request_timeout
        context: dict[str, Any] = {}
        self.contexts.append(context)
        return context

    def delete_context(self, context: dict[str, Any], *, request_timeout: float | None = None) -> None:
        del request_timeout
        self.deleted.append(context)
        self.contexts.remove(context)

    def run_code(self, code, *, context, on_stdout=None, on_stderr=None, timeout=0):
        del timeout
        self.calls.append(code)

        class Writer(io.StringIO):
            def __init__(self, callback):
                super().__init__()
                self.callback = callback

            def write(self, value):
                if self.callback:
                    self.callback(SimpleNamespace(output=value))
                return super().write(value)

        stdout, stderr = Writer(on_stdout), Writer(on_stderr)
        error = None
        try:
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                exec(compile(code, "<sdk-test>", "exec"), context, context)
        except BaseException as exc:
            error = SimpleNamespace(name=type(exc).__name__, value=str(exc))
        return SimpleNamespace(stdout=stdout.getvalue(), stderr=stderr.getvalue(), error=error)
