"""In-process interpreter backend for offline host-tool / SUBMIT mediation.

Mirrors DSPy host-callable + FinalOutput outcomes without Daytona transport.
Product turns use the Daytona HTTP broker path; this backend is the offline
double and unit-test seam.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from fleet_rlm.daytona.errors import DaytonaAdapterError, sanitize_provider_message
from fleet_rlm.daytona.submit import FleetFinalOutput, build_submit_setup_code


@dataclass(frozen=True, slots=True)
class BackendExecutionResult:
    """Normalized backend outcome for interpreter finalization."""

    stdout: str = ""
    final: dict[str, Any] | None = None
    error: str | None = None


class InProcessInterpreterBackend:
    """Shared-namespace exec backend with host-tool and SUBMIT bindings."""

    def __init__(self) -> None:
        self.namespace: dict[str, object] = {"_out": ""}
        self.closed = False
        self._host_tools: dict[str, Callable[..., Any]] = {}
        self._output_fields: list[dict[str, Any]] | None = None
        self._submit_key: tuple[tuple[str, str], ...] | None = None

    def bind_host_tools(self, tools: Mapping[str, Callable[..., Any]]) -> None:
        self._host_tools = dict(tools)
        for name, fn in self._host_tools.items():
            self.namespace[name] = self._wrap_host_tool(name, fn)

    def ensure_submit(self, output_fields: list[dict[str, Any]] | None) -> None:
        key = _submit_signature_key(output_fields)
        if key == self._submit_key:
            return
        self._output_fields = list(output_fields) if output_fields else None
        self.namespace["FleetFinalOutput"] = FleetFinalOutput
        self.namespace["_FINAL_OUTPUT_MARKER"] = "__FLEET_FINAL_OUTPUT__"
        self.namespace["_json"] = __import__("json")
        exec(build_submit_setup_code(self._output_fields), self.namespace, self.namespace)  # noqa: S102
        self._submit_key = key

    def run(self, code: str, variables: dict[str, object] | None = None) -> BackendExecutionResult:
        if self.closed:
            msg = "backend already closed"
            raise DaytonaAdapterError(message=msg, cause_type="InterpreterLifecycleError")
        if variables:
            self.namespace.update(variables)
        try:
            exec(code, self.namespace, self.namespace)  # noqa: S102
        except FleetFinalOutput as final:
            return BackendExecutionResult(stdout=str(self.namespace.get("_out", "")), final=dict(final.value))
        except Exception as exc:  # noqa: BLE001 - sanitize at the mediation boundary
            # Remote-style SUBMIT may raise a namespace-local FleetFinalOutput twin.
            value = getattr(exc, "value", None)
            if type(exc).__name__ == "FleetFinalOutput" and isinstance(value, dict):
                return BackendExecutionResult(stdout=str(self.namespace.get("_out", "")), final=dict(value))
            raise DaytonaAdapterError(
                message=sanitize_provider_message(str(exc)),
                cause_type=type(exc).__name__,
            ) from exc
        return BackendExecutionResult(stdout=str(self.namespace.get("_out", "")))

    def close(self) -> None:
        self.closed = True
        self._host_tools.clear()

    def _wrap_host_tool(self, name: str, fn: Callable[..., Any]) -> Callable[..., Any]:
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return fn(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 - public sandbox errors stay sanitized
                raise DaytonaAdapterError(
                    message=sanitize_provider_message(str(exc)),
                    cause_type=type(exc).__name__,
                ) from exc

        wrapper.__name__ = name
        return wrapper


def _submit_signature_key(
    output_fields: list[dict[str, Any]] | None,
) -> tuple[tuple[str, str], ...] | None:
    if not output_fields:
        return None
    normalized: list[tuple[str, str]] = []
    for field in output_fields:
        name = str(field.get("name") or "").strip()
        if not name:
            continue
        normalized.append((name, str(field.get("type") or "").strip()))
    return tuple(normalized) or None
