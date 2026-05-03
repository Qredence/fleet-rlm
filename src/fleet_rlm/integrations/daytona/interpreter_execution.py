"""Execution and code-sanitization helpers for the Daytona interpreter."""

from __future__ import annotations

import ast
import json
import math
import re
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from dspy.primitives import CodeInterpreterError, FinalOutput

from fleet_rlm.runtime.execution.interpreter_support import (
    SupportsExecutionEventCallback,
    complete_event_data,
    emit_execution_event,
)

from .async_compat import _await_if_needed
from .bridge import DaytonaBridgeExecution, DaytonaToolBridge
from .bridge_callbacks import invoke_tool
from .interpreter_assets import (
    _FINAL_OUTPUT_MARKER,
    _base_setup_code,
    _generic_submit_code,
    _typed_submit_code,
)
from .session_runtime import DaytonaSandboxSession


@dataclass(slots=True)
class DaytonaExecutionResponse:
    stdout: str = ""
    stderr: str = ""
    error: str | None = None
    final_artifact: dict[str, Any] | None = None
    callback_count: int = 0


@dataclass(slots=True)
class ExecutionCallbacks:
    bridge_tools: Callable[[], dict[str, Callable[..., Any]]]
    reject_recursive_callbacks: Callable[[str], None]
    requires_bridge: Callable[[str, dict[str, Callable[..., Any]]], bool]
    ensure_bridge: Callable[..., Any]
    execute_direct: Callable[..., Any]
    response_from_execution: Callable[
        [DaytonaBridgeExecution], DaytonaExecutionResponse
    ]


class CodeSanitizationError(Exception):
    """Raised when model-emitted code cannot be made executable."""


_DSPY_FIELD_SENTINEL_RE = re.compile(r"\[\[\s*##\s*[\w-]+\s*##\s*\]\]")
_PYTHON_FENCE_RE = re.compile(
    r"^\s*(?:Code:\s*)?```(?:python|py)?\s*\n(?P<body>.*?)(?:\n```)?\s*$",
    re.IGNORECASE | re.DOTALL,
)


class DaytonaExecutionOwner(SupportsExecutionEventCallback, Protocol):
    timeout: int
    execute_timeout: int | None
    output_fields: list[dict[str, Any]] | None
    volume_mount_path: str
    _setup_context_id: str | None
    _setup_workspace_path: str | None
    _submit_signature_key: tuple[tuple[str, str], ...] | None
    _bridge: DaytonaToolBridge | None
    _bridge_sandbox_id: str | None
    _bridge_context_id: str | None
    _bridge_tools: Callable[..., Any]
    _reject_unsupported_recursive_callbacks: Callable[..., None]
    _requires_bridge: Callable[..., bool]

    async def _aclose_bridge(self) -> None:
        pass

    async def aensure_bridge(
        self,
        *,
        session: DaytonaSandboxSession,
        context: Any,
        tools: dict[str, Callable[..., Any]],
        bridge_cls: type[DaytonaToolBridge] | None = None,
    ) -> DaytonaToolBridge:
        pass

    async def aexecute_direct(
        self,
        *,
        session: DaytonaSandboxSession,
        context: Any,
        code: str,
        envs: dict[str, str] | None = None,
    ) -> DaytonaBridgeExecution:
        pass

    def response_from_execution(
        self,
        execution: DaytonaBridgeExecution,
        *,
        extract_final_artifact_fn: Callable[[str], dict[str, Any] | None] | None = None,
    ) -> DaytonaExecutionResponse:
        pass


def python_parses(code: str) -> bool:
    try:
        ast.parse(code)
    except SyntaxError:
        return False
    return True


def strip_trailing_fence(code: str) -> str:
    lines = str(code or "").strip().splitlines()
    while lines and lines[-1].strip() in {"```", "```python", "```py"}:
        lines.pop()
    return "\n".join(lines).strip()


def strip_dspy_sentinel_lines(code: str) -> str:
    cleaned: list[str] = []
    for line in str(code or "").splitlines():
        match = _DSPY_FIELD_SENTINEL_RE.search(line)
        if match is None:
            if line.strip() not in {"```", "```python", "```py", "Code:"}:
                cleaned.append(line)
            continue
        prefix = line[: match.start()].rstrip()
        if prefix and prefix not in {"]]", "```"}:
            cleaned.append(prefix)
    return "\n".join(cleaned).strip()


def safe_variables(variables: dict[str, Any] | None) -> dict[str, Any]:
    safe_vars: dict[str, Any] = {}
    for key, value in (variables or {}).items():
        normalized_key = str(key)
        try:
            json.dumps(value)
            safe_vars[normalized_key] = value
        except (TypeError, ValueError, RecursionError):
            safe_vars[normalized_key] = str(value)
    return safe_vars


def submit_signature(
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


async def aensure_setup(
    owner: DaytonaExecutionOwner,
    session: DaytonaSandboxSession,
    *,
    base_setup_code: Callable[..., str] = _base_setup_code,
    generic_submit_code: Callable[[], str] = _generic_submit_code,
    typed_submit_code: Callable[[list[dict[str, Any]]], str] = _typed_submit_code,
    submit_signature_fn: Callable[[], tuple[tuple[str, str], ...] | None],
) -> Any:
    context = await session.aensure_context()
    if (
        owner._setup_context_id != session.context_id
        or owner._setup_workspace_path != session.workspace_path
    ):
        result = await _await_if_needed(
            session.sandbox.code_interpreter.run_code(
                base_setup_code(
                    workspace_path=session.workspace_path,
                    volume_mount_path=owner.volume_mount_path,
                ),
                context=context,
            )
        )
        if result.error:
            raise CodeInterpreterError(
                f"Failed to initialize Daytona sandbox helpers: {result.error.value}"
            )
        owner._setup_context_id = session.context_id
        owner._setup_workspace_path = session.workspace_path
        owner._submit_signature_key = None

    current_submit_signature = submit_signature_fn()
    if current_submit_signature is None:
        if owner._submit_signature_key is not None:
            result = await _await_if_needed(
                session.sandbox.code_interpreter.run_code(
                    generic_submit_code(),
                    context=context,
                )
            )
            if result.error:
                raise CodeInterpreterError(
                    f"Failed to restore generic SUBMIT: {result.error.value}"
                )
            owner._submit_signature_key = None
        return context

    if current_submit_signature != owner._submit_signature_key:
        result = await _await_if_needed(
            session.sandbox.code_interpreter.run_code(
                typed_submit_code(owner.output_fields or []),
                context=context,
            )
        )
        if result.error:
            raise CodeInterpreterError(
                f"Failed to register typed SUBMIT: {result.error.value}"
            )
        owner._submit_signature_key = current_submit_signature
    return context


async def aensure_bridge(
    owner: DaytonaExecutionOwner,
    *,
    session: DaytonaSandboxSession,
    context: Any,
    tools: dict[str, Callable[..., Any]],
    bridge_cls: type[DaytonaToolBridge] | None = None,
) -> DaytonaToolBridge:
    if bridge_cls is None:
        bridge_cls = DaytonaToolBridge
    sandbox_id = session.sandbox_id
    context_id = session.context_id
    bridge = owner._bridge
    if (
        bridge is None
        or owner._bridge_sandbox_id != sandbox_id
        or owner._bridge_context_id != context_id
    ):
        await owner._aclose_bridge()
        bridge = bridge_cls(
            sandbox=session.sandbox,
            context=context,
        )
        owner._bridge = bridge
        owner._bridge_sandbox_id = sandbox_id
        owner._bridge_context_id = context_id
    else:
        bridge.bind_context(context)
    await bridge.async_tools(tools)
    return bridge


async def aexecute_in_session(
    owner: DaytonaExecutionOwner,
    *,
    session: DaytonaSandboxSession,
    code: str,
    variables: dict[str, Any],
    envs: dict[str, str] | None = None,
    bridge_tools_fn: Callable[[], dict[str, Callable[..., Any]]] | None = None,
    reject_unsupported_recursive_callbacks_fn: Callable[[str], None] | None = None,
    requires_bridge_fn: Callable[[str, dict[str, Callable[..., Any]]], bool]
    | None = None,
    aensure_bridge_fn: Callable[..., Any] | None = None,
    aexecute_direct_fn: Callable[..., Any] | None = None,
    response_from_execution_fn: Callable[
        [DaytonaBridgeExecution], DaytonaExecutionResponse
    ]
    | None = None,
) -> DaytonaExecutionResponse:
    callbacks = resolve_execution_callbacks(
        owner,
        bridge_tools_fn=bridge_tools_fn,
        reject_unsupported_recursive_callbacks_fn=reject_unsupported_recursive_callbacks_fn,
        requires_bridge_fn=requires_bridge_fn,
        aensure_bridge_fn=aensure_bridge_fn,
        aexecute_direct_fn=aexecute_direct_fn,
        response_from_execution_fn=response_from_execution_fn,
    )
    context = await aensure_setup(
        owner,
        session,
        submit_signature_fn=lambda: submit_signature(owner.output_fields),
    )
    prepared_code = prepare_execution_code(
        owner,
        code=code,
        variables=variables,
        reject_recursive_callbacks=callbacks.reject_recursive_callbacks,
    )
    execution = await arun_prepared_execution(
        owner,
        session=session,
        context=context,
        code=prepared_code,
        callbacks=callbacks,
        envs=envs,
    )
    return callbacks.response_from_execution(execution)


def resolve_execution_callbacks(
    owner: DaytonaExecutionOwner,
    *,
    bridge_tools_fn: Callable[[], dict[str, Callable[..., Any]]] | None = None,
    reject_unsupported_recursive_callbacks_fn: Callable[[str], None] | None = None,
    requires_bridge_fn: Callable[[str, dict[str, Callable[..., Any]]], bool]
    | None = None,
    aensure_bridge_fn: Callable[..., Any] | None = None,
    aexecute_direct_fn: Callable[..., Any] | None = None,
    response_from_execution_fn: Callable[
        [DaytonaBridgeExecution], DaytonaExecutionResponse
    ]
    | None = None,
) -> ExecutionCallbacks:
    return ExecutionCallbacks(
        bridge_tools=bridge_tools_fn or owner._bridge_tools,
        reject_recursive_callbacks=reject_unsupported_recursive_callbacks_fn
        or owner._reject_unsupported_recursive_callbacks,
        requires_bridge=requires_bridge_fn or owner._requires_bridge,
        ensure_bridge=aensure_bridge_fn
        or (
            lambda *, session, context, tools: owner.aensure_bridge(
                session=session,
                context=context,
                tools=tools,
            )
        ),
        execute_direct=aexecute_direct_fn
        or (
            lambda *, session, context, code, envs=None: owner.aexecute_direct(
                session=session,
                context=context,
                code=code,
                envs=envs,
            )
        ),
        response_from_execution=response_from_execution_fn
        or (lambda execution: owner.response_from_execution(execution)),
    )


def prepare_execution_code(
    owner: DaytonaExecutionOwner,
    *,
    code: str,
    variables: dict[str, Any],
    reject_recursive_callbacks: Callable[[str], None],
) -> str:
    prepared_code = sanitize_execution_code(inject_variables(owner, code, variables))
    reject_recursive_callbacks(prepared_code)
    return prepared_code


def sanitize_execution_code(code: str) -> str:
    """Strip DSPy adapter framing that occasionally leaks into Python code."""
    original = str(code or "")
    candidate = original.strip()
    fence_match = _PYTHON_FENCE_RE.match(candidate)
    if fence_match:
        candidate = fence_match.group("body").strip()
    if candidate.lower().startswith("code:\n"):
        candidate = candidate.split("\n", 1)[1].strip()

    candidate = strip_trailing_fence(strip_dspy_sentinel_lines(candidate))
    if python_parses(candidate):
        return candidate

    sentinel_match = _DSPY_FIELD_SENTINEL_RE.search(original)
    if sentinel_match:
        truncated = strip_trailing_fence(original[: sentinel_match.start()])
        truncated = strip_dspy_sentinel_lines(truncated)
        if python_parses(truncated):
            return truncated

    try:
        ast.parse(candidate)
    except SyntaxError as exc:
        raise CodeSanitizationError(
            f"Unable to prepare executable Python: {exc.msg} (line {exc.lineno or '?'})"
        ) from exc
    return candidate


def structured_execution_error(*, reason: str, error: str) -> DaytonaExecutionResponse:
    payload = json.dumps(
        {
            "status": "error",
            "reason": reason,
            "error": error,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return DaytonaExecutionResponse(error=payload)


async def arun_prepared_execution(
    owner: DaytonaExecutionOwner,
    *,
    session: DaytonaSandboxSession,
    context: Any,
    code: str,
    callbacks: ExecutionCallbacks,
    envs: dict[str, str] | None = None,
) -> DaytonaBridgeExecution:
    tools = callbacks.bridge_tools()
    if callbacks.requires_bridge(code, tools):
        bridge = await callbacks.ensure_bridge(
            session=session,
            context=context,
            tools=tools,
        )
        return await bridge.aexecute(
            code=code,
            timeout=int(owner.execute_timeout or owner.timeout),
            tool_executor=lambda name, args, kwargs: invoke_tool(
                owner,
                name,
                args,
                kwargs,
            ),
        )
    return await callbacks.execute_direct(
        session=session,
        context=context,
        code=code,
        envs=envs,
    )


async def aexecute_direct(
    owner: DaytonaExecutionOwner,
    *,
    session: DaytonaSandboxSession,
    context: Any,
    code: str,
    envs: dict[str, str] | None = None,
) -> DaytonaBridgeExecution:
    """Run *code* directly via the sandbox code interpreter."""
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []

    def _on_stdout(message: Any) -> None:
        stdout_parts.append(str(getattr(message, "output", "") or ""))

    def _on_stderr(message: Any) -> None:
        stderr_parts.append(str(getattr(message, "output", "") or ""))

    result = await _await_if_needed(
        session.sandbox.code_interpreter.run_code(
            code,
            context=context,
            on_stdout=_on_stdout,
            on_stderr=_on_stderr,
            envs=envs,
            timeout=int(owner.execute_timeout or owner.timeout),
        )
    )
    return DaytonaBridgeExecution(
        result=result,
        stdout="".join(stdout_parts),
        stderr="".join(stderr_parts),
        callback_count=0,
    )


def response_from_execution(
    owner: DaytonaExecutionOwner,
    execution: DaytonaBridgeExecution,
    *,
    extract_final_artifact_fn: Callable[[str], dict[str, Any] | None] | None = None,
) -> DaytonaExecutionResponse:
    extract_final_artifact_fn = extract_final_artifact_fn or extract_final_artifact
    final_artifact = extract_final_artifact_fn(execution.stdout)
    result = execution.result
    error = getattr(result, "error", None)
    if error is None:
        return DaytonaExecutionResponse(
            stdout=execution.stdout,
            stderr=execution.stderr,
            final_artifact=final_artifact,
            callback_count=execution.callback_count,
        )

    error_name = str(getattr(error, "name", "") or "")
    error_value = str(getattr(error, "value", "") or "")
    if error_name == "_FleetFinalOutput" and final_artifact is not None:
        return DaytonaExecutionResponse(
            stdout=execution.stdout,
            stderr=execution.stderr,
            final_artifact=final_artifact,
            callback_count=execution.callback_count,
        )

    error_text = (
        ": ".join(part for part in [error_name, error_value] if part)
        or error_value
        or error_name
        or "Execution failed"
    )
    return DaytonaExecutionResponse(
        stdout=execution.stdout,
        stderr=execution.stderr,
        error=error_text,
        final_artifact=final_artifact,
        callback_count=execution.callback_count,
    )


def extract_final_artifact(
    stdout: str,
    *,
    marker: str = _FINAL_OUTPUT_MARKER,
) -> dict[str, Any] | None:
    start = stdout.find(marker)
    if start == -1:
        return None
    start += len(marker)
    end = stdout.find(marker, start)
    if end == -1:
        return None
    payload = stdout[start:end]
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return None
    return {
        "kind": "structured",
        "value": parsed,
        "finalization_mode": "SUBMIT",
    }


def finalize_execution_result(
    owner: DaytonaExecutionOwner,
    *,
    response: DaytonaExecutionResponse,
    started_at: float,
    execution_profile: str,
    code_hash: str,
    code_preview: str,
) -> str | FinalOutput:
    final_payload = None
    if isinstance(response.final_artifact, dict):
        final_payload = response.final_artifact.get("value")

    stdout_preview = str(response.stdout or "")
    stderr_preview = str(response.stderr or "")
    if response.error:
        error_text = str(response.error)
        emit_execution_event(
            owner,
            complete_event_data(
                started_at=started_at,
                execution_profile=execution_profile,
                code_hash=code_hash,
                code_preview=code_preview,
                success=False,
                result_kind="stderr",
                stdout_preview=stdout_preview or None,
                stderr_preview=stderr_preview or None,
                error_type="ExecutionError",
                error=error_text,
            ),
        )
        combined = stdout_preview.strip()
        return f"{combined}\n{error_text}" if combined else error_text

    if final_payload is not None:
        output_keys = (
            [str(key) for key in list(final_payload.keys())[:50]]
            if isinstance(final_payload, dict)
            else None
        )
        emit_execution_event(
            owner,
            complete_event_data(
                started_at=started_at,
                execution_profile=execution_profile,
                code_hash=code_hash,
                code_preview=code_preview,
                success=True,
                result_kind="final_output",
                output_keys=output_keys,
                stdout_preview=stdout_preview or None,
                stderr_preview=stderr_preview or None,
            ),
        )
        return FinalOutput(final_payload)

    emit_execution_event(
        owner,
        complete_event_data(
            started_at=started_at,
            execution_profile=execution_profile,
            code_hash=code_hash,
            code_preview=code_preview,
            success=not bool(stderr_preview),
            result_kind="stderr" if stderr_preview else "stdout",
            stdout_preview=stdout_preview or None,
            stderr_preview=stderr_preview or None,
        ),
    )
    if stderr_preview:
        combined = stdout_preview.strip()
        return f"{combined}\n{stderr_preview}" if combined else stderr_preview
    return stdout_preview


def inject_variables(
    owner: DaytonaExecutionOwner,
    code: str,
    variables: dict[str, Any],
) -> str:
    del owner
    if not variables:
        return code
    assignments = [f"{name} = {literal(value)}" for name, value in variables.items()]
    return "\n".join(assignments) + "\n" + code


def literal(value: Any) -> str:
    if value is None:
        return "None"
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, int):
        return repr(value)
    if isinstance(value, float):
        if math.isnan(value):
            return "float('nan')"
        if math.isinf(value):
            return "float('inf')" if value > 0 else "float('-inf')"
        return repr(value)
    if isinstance(value, str):
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(literal(item) for item in value) + "]"
    if isinstance(value, tuple):
        inner = ", ".join(literal(item) for item in value)
        if len(value) == 1:
            inner += ","
        return "(" + inner + ")"
    if isinstance(value, set):
        if not value:
            return "set()"
        return "{" + ", ".join(literal(item) for item in value) + "}"
    if isinstance(value, dict):
        pairs = [f"{literal(key)}: {literal(item)}" for key, item in value.items()]
        return "{" + ", ".join(pairs) + "}"
    raise CodeInterpreterError(f"Unsupported value type: {type(value).__name__}")
