"""Execution helpers for the Daytona interpreter."""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from typing import AbstractSet, Any, Callable

from dspy.primitives import CodeInterpreterError, FinalOutput

from fleet_rlm.runtime.execution.interpreter_events import (
    complete_event_data,
    emit_execution_event,
    start_event_data,
    summarize_code,
)
from fleet_rlm.runtime.execution.profiles import ExecutionProfile

from .bridge import DaytonaBridgeExecution, DaytonaToolBridge
from .interpreter_assets import (
    _DAYTONA_SANDBOX_NATIVE_TOOL_NAMES,
    _FINAL_OUTPUT_MARKER,
    _UNSUPPORTED_RECURSIVE_SANDBOX_CALLBACKS,
    _base_setup_code,
    _generic_submit_code,
    _typed_submit_code,
)
from .runtime import (
    DaytonaSandboxRuntime,
    DaytonaSandboxSession,
)
from .runtime_helpers import _await_if_needed, _run_async_compat


@dataclass(slots=True)
class _DaytonaExecutionResponse:
    stdout: str = ""
    stderr: str = ""
    error: str | None = None
    final_artifact: dict[str, Any] | None = None
    callback_count: int = 0


def build_delegate_child(
    interpreter: Any,
    *,
    remaining_llm_budget: int,
) -> Any:
    runtime = DaytonaSandboxRuntime(config=interpreter.runtime._resolved_config)
    child = interpreter.__class__(
        runtime=runtime,
        owns_runtime=True,
        timeout=interpreter.timeout,
        execute_timeout=interpreter.execute_timeout,
        volume_name=interpreter.volume_name,
        repo_url=interpreter.repo_url,
        repo_ref=interpreter.repo_ref,
        context_paths=list(interpreter.context_paths),
        delete_session_on_shutdown=True,
        sub_lm=interpreter.sub_lm,
        max_llm_calls=remaining_llm_budget,
        llm_call_timeout=interpreter.llm_call_timeout,
        default_execution_profile=ExecutionProfile.RLM_DELEGATE,
        async_execute=interpreter.async_execute,
    )
    setattr(
        child,
        "_check_and_increment_llm_calls",
        interpreter._check_and_increment_llm_calls,
    )
    return child


def execute(
    interpreter: Any,
    code: str,
    variables: dict[str, Any] | None = None,
    *,
    execution_profile: ExecutionProfile | None = None,
) -> str | FinalOutput:
    return _run_async_compat(
        aexecute,
        interpreter,
        code,
        variables,
        execution_profile=execution_profile,
    )


async def aexecute(
    interpreter: Any,
    code: str,
    variables: dict[str, Any] | None = None,
    *,
    execution_profile: ExecutionProfile | None = None,
) -> str | FinalOutput:
    session = await interpreter._aensure_session_impl()
    await session.astart_driver(timeout=float(interpreter.execute_timeout))
    safe_vars = safe_variables(interpreter, variables)
    profile = execution_profile or interpreter.default_execution_profile
    profile_value = profile.value if hasattr(profile, "value") else str(profile)
    code_hash, code_preview = summarize_code(code)
    started_at = time.time()
    emit_execution_event(
        interpreter,
        start_event_data(
            execution_profile=str(profile_value),
            code_hash=code_hash,
            code_preview=code_preview,
        ),
    )
    try:
        response = await interpreter._aexecute_in_session(
            session=session,
            code=code,
            variables=safe_vars,
        )
    except Exception as exc:
        emit_execution_event(
            interpreter,
            complete_event_data(
                started_at=started_at,
                execution_profile=str(profile_value),
                code_hash=code_hash,
                code_preview=code_preview,
                success=False,
                result_kind="exception",
                error_type=type(exc).__name__,
                error=str(exc),
            ),
        )
        raise CodeInterpreterError(str(exc)) from exc
    return finalize_execution_result(
        interpreter,
        response=response,
        started_at=started_at,
        execution_profile=str(profile_value),
        code_hash=code_hash,
        code_preview=code_preview,
    )


def safe_variables(
    interpreter: Any, variables: dict[str, Any] | None
) -> dict[str, Any]:
    safe_vars: dict[str, Any] = {}
    for key, value in (variables or {}).items():
        normalized_key = str(key)
        try:
            json.dumps(value)
            safe_vars[normalized_key] = value
        except TypeError:
            safe_vars[normalized_key] = str(value)
    return safe_vars


def submit_signature(interpreter: Any) -> tuple[tuple[str, str], ...] | None:
    if not interpreter.output_fields:
        return None
    normalized: list[tuple[str, str]] = []
    for field in interpreter.output_fields:
        name = str(field.get("name") or "").strip()
        if not name:
            continue
        normalized.append((name, str(field.get("type") or "").strip()))
    return tuple(normalized) or None


async def aensure_setup(
    interpreter: Any,
    session: DaytonaSandboxSession,
    *,
    base_setup_code: Callable[..., str] = _base_setup_code,
    generic_submit_code: Callable[[], str] = _generic_submit_code,
    typed_submit_code: Callable[[list[dict[str, Any]]], str] = _typed_submit_code,
    submit_signature_fn: Callable[[], tuple[tuple[str, str], ...] | None] | None = None,
) -> Any:
    submit_signature_fn = submit_signature_fn or (lambda: submit_signature(interpreter))
    context = await session.aensure_context()
    if (
        interpreter._setup_context_id != session.context_id
        or interpreter._setup_workspace_path != session.workspace_path
    ):
        result = await _await_if_needed(
            session.sandbox.code_interpreter.run_code(
                base_setup_code(
                    workspace_path=session.workspace_path,
                    volume_mount_path=interpreter.volume_mount_path,
                ),
                context=context,
            )
        )
        if result.error:
            raise CodeInterpreterError(
                f"Failed to initialize Daytona sandbox helpers: {result.error.value}"
            )
        interpreter._setup_context_id = session.context_id
        interpreter._setup_workspace_path = session.workspace_path
        interpreter._submit_signature_key = None

    current_submit_signature = submit_signature_fn()
    if current_submit_signature is None:
        if interpreter._submit_signature_key is not None:
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
            interpreter._submit_signature_key = None
        return context

    if current_submit_signature != interpreter._submit_signature_key:
        result = await _await_if_needed(
            session.sandbox.code_interpreter.run_code(
                typed_submit_code(interpreter.output_fields or []),
                context=context,
            )
        )
        if result.error:
            raise CodeInterpreterError(
                f"Failed to register typed SUBMIT: {result.error.value}"
            )
        interpreter._submit_signature_key = current_submit_signature
    return context


async def aensure_bridge(
    interpreter: Any,
    *,
    session: DaytonaSandboxSession,
    context: Any,
    tools: dict[str, Callable[..., Any]],
    bridge_cls: type[DaytonaToolBridge] = DaytonaToolBridge,
) -> DaytonaToolBridge:
    sandbox_id = session.sandbox_id
    context_id = session.context_id
    bridge = interpreter._bridge
    if (
        bridge is None
        or interpreter._bridge_sandbox_id != sandbox_id
        or interpreter._bridge_context_id != context_id
    ):
        await interpreter._aclose_bridge()
        bridge = bridge_cls(
            sandbox=session.sandbox,
            context=context,
        )
        interpreter._bridge = bridge
        interpreter._bridge_sandbox_id = sandbox_id
        interpreter._bridge_context_id = context_id
    else:
        bridge.bind_context(context)
    await bridge.async_tools(tools)
    return bridge


async def aexecute_in_session(
    interpreter: Any,
    *,
    session: DaytonaSandboxSession,
    code: str,
    variables: dict[str, Any],
    bridge_tools_fn: Callable[[], dict[str, Callable[..., Any]]] | None = None,
    reject_unsupported_recursive_callbacks_fn: Callable[[str], None] | None = None,
    requires_bridge_fn: Callable[[str, dict[str, Callable[..., Any]]], bool]
    | None = None,
    aensure_bridge_fn: Callable[..., Any] | None = None,
    aexecute_direct_fn: Callable[..., Any] | None = None,
    response_from_execution_fn: Callable[
        [DaytonaBridgeExecution], _DaytonaExecutionResponse
    ]
    | None = None,
) -> _DaytonaExecutionResponse:
    bridge_tools_call = bridge_tools_fn or (lambda: bridge_tools(interpreter))
    reject_recursive_callbacks = reject_unsupported_recursive_callbacks_fn or (
        lambda code: reject_unsupported_recursive_callbacks(interpreter, code)
    )
    requires_bridge_call = requires_bridge_fn or (
        lambda code, tools: requires_bridge(interpreter, code, tools)
    )
    ensure_bridge_call = aensure_bridge_fn or (
        lambda *, session, context, tools: aensure_bridge(
            interpreter,
            session=session,
            context=context,
            tools=tools,
        )
    )
    execute_direct_call = aexecute_direct_fn or (
        lambda *, session, context, code: aexecute_direct(
            interpreter,
            session=session,
            context=context,
            code=code,
        )
    )
    response_from_execution_call = response_from_execution_fn or (
        lambda execution: response_from_execution(interpreter, execution)
    )
    context = await aensure_setup(
        interpreter,
        session,
        submit_signature_fn=interpreter._submit_signature,
    )
    prepared_code = inject_variables(interpreter, code, variables)
    reject_recursive_callbacks(prepared_code)
    tools = bridge_tools_call()
    if requires_bridge_call(prepared_code, tools):
        bridge = await ensure_bridge_call(
            session=session,
            context=context,
            tools=tools,
        )
        execution = await bridge.aexecute(
            code=prepared_code,
            timeout=int(interpreter.execute_timeout),
            tool_executor=lambda name, args, kwargs: invoke_tool(
                interpreter,
                name,
                args,
                kwargs,
            ),
        )
    else:
        execution = await execute_direct_call(
            session=session,
            context=context,
            code=prepared_code,
        )
    return response_from_execution_call(execution)


async def aexecute_direct(
    interpreter: Any,
    *,
    session: DaytonaSandboxSession,
    context: Any,
    code: str,
) -> DaytonaBridgeExecution:
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
            timeout=int(interpreter.execute_timeout),
        )
    )
    return DaytonaBridgeExecution(
        result=result,
        stdout="".join(stdout_parts),
        stderr="".join(stderr_parts),
        callback_count=0,
    )


def reject_unsupported_recursive_callbacks(
    interpreter: Any,
    code: str,
    *,
    callbacks: tuple[str, ...] = _UNSUPPORTED_RECURSIVE_SANDBOX_CALLBACKS,
) -> None:
    for callback_name in callbacks:
        if f"{callback_name}(" not in code:
            continue
        raise CodeInterpreterError(
            f"{callback_name}() is not available inside Daytona sandbox code. "
            "Use llm_query()/llm_query_batched() for semantic callbacks; "
            "recursive rlm_query* tools are agent-level only."
        )


def bridge_tools(
    interpreter: Any,
    *,
    native_tool_names: AbstractSet[str] = _DAYTONA_SANDBOX_NATIVE_TOOL_NAMES,
) -> dict[str, Callable[..., Any]]:
    tools = {
        name: tool
        for name, tool in interpreter._tools.items()
        if name not in native_tool_names
    }
    tools["llm_query"] = interpreter.llm_query
    tools["llm_query_batched"] = interpreter.llm_query_batched
    return tools


def requires_bridge(
    interpreter: Any,
    code: str,
    tools: dict[str, Callable[..., Any]],
) -> bool:
    for tool_name in tools:
        if f"{tool_name}(" in code:
            return True
    return False


def invoke_tool(
    interpreter: Any,
    name: str,
    args: list[Any],
    kwargs: dict[str, Any],
) -> Any:
    try:
        if name == "llm_query":
            prompt = args[0] if args else kwargs.get("prompt", "")
            value = interpreter.llm_query(str(prompt))
        elif name == "llm_query_batched":
            prompts = args[0] if args else kwargs.get("prompts", [])
            if not isinstance(prompts, list):
                prompts = []
            value = interpreter.llm_query_batched([str(item) for item in prompts])
        elif name in interpreter._tools:
            value = interpreter._tools[name](*args, **kwargs)
        else:
            raise RuntimeError(f"Unknown host callback: {name}")
        try:
            json.dumps(value)
            return value
        except TypeError:
            return str(value)
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def response_from_execution(
    interpreter: Any,
    execution: DaytonaBridgeExecution,
    *,
    extract_final_artifact_fn: Callable[[str], dict[str, Any] | None] | None = None,
) -> _DaytonaExecutionResponse:
    extract_final_artifact_fn = extract_final_artifact_fn or extract_final_artifact
    final_artifact = extract_final_artifact_fn(execution.stdout)
    result = execution.result
    error = getattr(result, "error", None)
    if error is None:
        return _DaytonaExecutionResponse(
            stdout=execution.stdout,
            stderr=execution.stderr,
            final_artifact=final_artifact,
            callback_count=execution.callback_count,
        )

    error_name = str(getattr(error, "name", "") or "")
    error_value = str(getattr(error, "value", "") or "")
    if error_name == "_FleetFinalOutput" and final_artifact is not None:
        return _DaytonaExecutionResponse(
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
    return _DaytonaExecutionResponse(
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
    interpreter: Any,
    *,
    response: _DaytonaExecutionResponse,
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
            interpreter,
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
            list(final_payload.keys())[:50] if isinstance(final_payload, dict) else None
        )
        emit_execution_event(
            interpreter,
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
        interpreter,
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


def inject_variables(interpreter: Any, code: str, variables: dict[str, Any]) -> str:
    if not variables:
        return code
    assignments = [
        f"{name} = {literal(interpreter, value)}" for name, value in variables.items()
    ]
    return "\n".join(assignments) + "\n" + code


def literal(interpreter: Any, value: Any) -> str:
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
        return "[" + ", ".join(literal(interpreter, item) for item in value) + "]"
    if isinstance(value, tuple):
        inner = ", ".join(literal(interpreter, item) for item in value)
        if len(value) == 1:
            inner += ","
        return "(" + inner + ")"
    if isinstance(value, set):
        if not value:
            return "set()"
        return "{" + ", ".join(literal(interpreter, item) for item in value) + "}"
    if isinstance(value, dict):
        pairs = [
            f"{literal(interpreter, key)}: {literal(interpreter, item)}"
            for key, item in value.items()
        ]
        return "{" + ", ".join(pairs) + "}"
    raise CodeInterpreterError(f"Unsupported value type: {type(value).__name__}")


__all__ = [
    "_DaytonaExecutionResponse",
    "aensure_bridge",
    "aensure_setup",
    "aexecute",
    "aexecute_direct",
    "aexecute_in_session",
    "build_delegate_child",
    "bridge_tools",
    "execute",
    "extract_final_artifact",
    "finalize_execution_result",
    "inject_variables",
    "invoke_tool",
    "literal",
    "reject_unsupported_recursive_callbacks",
    "requires_bridge",
    "response_from_execution",
    "safe_variables",
    "submit_signature",
]
