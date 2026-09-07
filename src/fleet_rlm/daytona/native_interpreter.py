"""Caller-owned native feasibility backend; not a selectable production runtime.

Context acquisition belongs to the async resource owner. This backend consumes
its explicit context through the existing synchronous SDK view and host gateway.
The gateway services callbacks only: generated code runs in Daytona's native
context. A caller-supplied containment operation is mandatory until the native
context's subprocess termination contract has live certification.
"""

from __future__ import annotations

import contextlib
import json
import math
import time
from collections.abc import Callable, Mapping
from typing import Any

from fleet_rlm.daytona.broker import DaytonaHttpToolBroker, extract_final_payload
from fleet_rlm.daytona.errors import DaytonaAdapterError, map_provider_error
from fleet_rlm.daytona.interpreter import BackendExecutionResult


class NativeInterpreterBackend:
    """Fresh explicit native context behind Fleet's existing interpreter adapter."""

    supports_sandbox_serializable_inputs = True

    def __init__(
        self,
        *,
        service: Any,
        context: Any,
        gateway: DaytonaHttpToolBroker,
        deadline: float,
        max_output_bytes: int,
        contain: Callable[[], None],
        is_authorized: Callable[[], bool],
        cleanup_timeout_seconds: float,
    ) -> None:
        if not isinstance(getattr(context, "id", None), str) or not context.id:
            raise ValueError("an explicit native interpreter context is required")
        if not math.isfinite(deadline) or type(max_output_bytes) is not int or max_output_bytes < 1:
            raise ValueError("native execution requires finite bounds")
        if not math.isfinite(cleanup_timeout_seconds) or cleanup_timeout_seconds <= 0:
            raise ValueError("native cleanup requires a positive finite timeout")
        self._service = service
        self._context = context
        self._gateway = gateway
        self._deadline = deadline
        self._max_output_bytes = max_output_bytes
        self._contain = contain
        self._is_authorized = is_authorized
        self._tools: dict[str, Callable[..., Any]] = {}
        self._closed = False
        self._uncertain = False
        self._context_deleted = False
        self._cleanup_timeout_seconds = cleanup_timeout_seconds

    @property
    def containment_required(self) -> bool:
        """Transport completion cannot prove remote subprocess containment."""
        return not self._closed

    def _admit(self) -> int:
        if self._closed or self._uncertain or not self._is_authorized():
            raise DaytonaAdapterError(
                "native interpreter authority unavailable", cause_type="InterpreterLifecycleError"
            )
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("native interpreter deadline exceeded")
        return max(1, math.ceil(remaining))

    def bind_host_tools(self, tools: Mapping[str, Callable[..., Any]]) -> None:
        self._admit()
        self._tools = dict(tools)
        self._gateway.ensure_started()
        self._gateway.register_tools(tools)

    def ensure_submit(self, output_fields: list[dict[str, Any]] | None) -> None:
        result = self.run(self._gateway.submit_setup_code(output_fields))
        if result.error:
            raise DaytonaAdapterError("native bindings could not be installed", cause_type="InterpreterLifecycleError")

    def run(
        self,
        code: str,
        variables: dict[str, object] | None = None,
        *,
        on_stdout: Callable[[str], None] | None = None,
    ) -> BackendExecutionResult:
        timeout = self._admit()
        # DSPy's SandboxSerializable prelude carries non-primitive values as
        # code. Plain iteration variables use a strict JSON representation.
        if variables:
            if any(not isinstance(key, str) or not key.isidentifier() for key in variables):
                raise ValueError("invalid native variable name")
            encoded = json.dumps(variables, ensure_ascii=True, allow_nan=False)
            code = f"import json as _fleet_json\nglobals().update(_fleet_json.loads({encoded!r}))\n{code}"
        output_bytes = 0

        def observe(message: Any, *, stdout: bool) -> None:
            nonlocal output_bytes
            self._admit()
            chunk = message.output
            output_bytes += len(chunk.encode("utf-8"))
            if output_bytes > self._max_output_bytes:
                raise DaytonaAdapterError("native output limit exceeded", cause_type="InterpreterOutputLimitError")
            if stdout and on_stdout is not None:
                on_stdout(chunk)

        def observe_error(error: Any) -> None:
            nonlocal output_bytes
            self._admit()
            output_bytes += sum(
                len(str(getattr(error, field, "")).encode("utf-8")) for field in ("name", "value", "traceback")
            )
            if output_bytes > self._max_output_bytes:
                raise DaytonaAdapterError("native output limit exceeded", cause_type="InterpreterOutputLimitError")

        def execute() -> BackendExecutionResult:
            result = self._service.run_code(
                code,
                context=self._context,
                timeout=timeout,
                on_stdout=lambda msg: observe(msg, stdout=True),
                on_stderr=lambda msg: observe(msg, stdout=False),
                on_error=observe_error,
            )
            self._admit()
            final = extract_final_payload(result.stdout)
            error = result.error
            if error is not None and not (error.name == "FleetFinalOutputError" and final is not None):
                return BackendExecutionResult(
                    stdout=result.stdout, stderr=result.stderr, error=error.value, error_category=error.name
                )
            return BackendExecutionResult(stdout=result.stdout, stderr=result.stderr, final=final)

        def tool(name: str, args: list[Any], kwargs: dict[str, Any]) -> Any:
            self._admit()
            if name not in self._tools:
                raise DaytonaAdapterError("native host tool unavailable", cause_type="UnknownToolError")
            result = self._tools[name](*args, **kwargs)
            self._admit()
            return result

        try:
            return self._gateway.execute_with_callbacks(
                run_code=execute, tool_executor=tool, check_authority=self._admit
            )
        except BaseException as exc:
            # Never reuse a context after timeout, callback failure or transport
            # ambiguity. The resource owner must contain it before settlement.
            self._uncertain = True
            if isinstance(exc, Exception):
                raise map_provider_error(exc) from exc
            raise

    def close(self) -> None:
        from daytona.common.errors import DaytonaNotFoundError

        if self._closed:
            return
        self._uncertain = True
        try:
            if not self._context_deleted:
                # This operation specifically addresses this context.
                with contextlib.suppress(DaytonaNotFoundError):
                    self._service.delete_context(self._context, request_timeout=self._cleanup_timeout_seconds)
                self._context_deleted = True
        finally:
            # Always attempt containment, even if context deletion failed.
            # The owner must confirm sandbox stop/deletion, not socket closure.
            self._contain()
        if self._gateway.stop(strict=True) is False:
            raise DaytonaAdapterError("native gateway cleanup remains pending", cause_type="InterpreterLifecycleError")
        self._closed = True
