"""Offline in-process interpreter backend for host-tool and SUBMIT contract tests.

The shared namespace executes generated Python in this process. It is a test double
for the interpreter contract and is never a runtime execution path.
"""

from __future__ import annotations

import contextlib
import io
from collections.abc import Callable, Mapping
from typing import Any

from fleet_rlm.daytona.errors import DaytonaAdapterError, sanitize_provider_message
from fleet_rlm.daytona.interpreter import (
    _MAX_CAPTURED_OUTPUT_CHARS,
    FINAL_OUTPUT_MARKER,
    BackendExecutionResult,
    FleetFinalOutputError,
    OutputCallback,
    build_submit_setup_code,
)

_UNSET = object()


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

        def load_context(raw_manifest: bytes | str) -> list[dict[str, object]]:
            from fleet_rlm.rlm.program import _context_convenience, _materialize_context_manifest

            binding = self._context_binding
            if binding is None:
                raise ValueError("context manifest is not host bound")
            values, accesses = _materialize_context_manifest(
                raw_manifest,
                trusted_mount_root=binding[0],
                expected_manifest_sha256=binding[1],
            )
            self._context_accesses.extend(accesses)
            self.namespace["context"] = _context_convenience(values)
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

    @property
    def fleet_host_tool_dispatch_available(self) -> bool:
        """Whether Fleet-provided host tools reach executed code directly."""
        return True

    def bind_host_tools(self, tools: Mapping[str, Callable[..., Any]]) -> None:
        for name in self._bound_tool_names.difference(tools):
            self.namespace.pop(name, None)
        self._host_tools = dict(tools)
        self._bound_tool_names = set(tools)
        for name, fn in self._host_tools.items():
            self.namespace[name] = self._wrap_host_tool(name, fn)

    def ensure_submit(self, output_fields: list[dict[str, Any]] | None, max_output_chars: int | None = None) -> None:
        key = (repr(output_fields), max_output_chars)
        if key == self._submit_key:
            return
        self.namespace["FleetFinalOutputError"] = FleetFinalOutputError
        self.namespace["FINAL_OUTPUT_MARKER"] = FINAL_OUTPUT_MARKER
        self.namespace["json"] = __import__("json")
        self.namespace["_json"] = self.namespace["json"]
        exec(build_submit_setup_code(output_fields, max_output_chars=max_output_chars), self.namespace, self.namespace)
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
