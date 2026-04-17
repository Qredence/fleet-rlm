"""Daytona-backed interpreter compatible with the shared ReAct + RLM runtime."""

from __future__ import annotations

import inspect
import json
import logging
import math
import time
from dataclasses import dataclass
from typing import AbstractSet, Any, Callable, Protocol, cast

import dspy
from dspy.primitives import CodeInterpreterError, FinalOutput

from fleet_rlm.runtime.execution.interpreter_support import (
    SupportsExecutionEventCallback,
    async_enter as _async_enter_impl,
    async_exit as _async_exit_impl,
    complete_event_data,
    emit_execution_event,
    execution_profile_context,
    get_registered_tools,
    initialize_llm_query_state,
    initialize_sub_rlm_state,
    initialize_tool_runtime_state,
    set_registered_tools,
    start_event_data,
    summarize_code,
    sync_enter as _sync_enter_impl,
    sync_exit as _sync_exit_impl,
)
from fleet_rlm.runtime.execution.interpreter_protocol import (
    RLMInterpreterProtocol,
    StatefulWorkspaceInterpreterProtocol,
)
from fleet_rlm.runtime.execution.profiles import ExecutionProfile
from fleet_rlm.runtime.tools.llm_tools import LLMQueryMixin

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
    DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH,
    DaytonaSandboxRuntime,
    DaytonaSandboxSession,
)
from .async_compat import _await_if_needed, _run_async_compat
from .types import SandboxSpec, dedupe_paths, normalized_context_sources


@dataclass(slots=True)
class _DaytonaExecutionResponse:
    stdout: str = ""
    stderr: str = ""
    error: str | None = None
    final_artifact: dict[str, Any] | None = None
    callback_count: int = 0


@dataclass(slots=True)
class _ExecutionCallbacks:
    bridge_tools: Callable[[], dict[str, Callable[..., Any]]]
    reject_recursive_callbacks: Callable[[str], None]
    requires_bridge: Callable[[str, dict[str, Callable[..., Any]]], bool]
    ensure_bridge: Callable[..., Any]
    execute_direct: Callable[..., Any]
    response_from_execution: Callable[
        [DaytonaBridgeExecution], _DaytonaExecutionResponse
    ]


class _DaytonaInterpreterLike(
    RLMInterpreterProtocol,
    SupportsExecutionEventCallback,
    Protocol,
):
    """Protocol describing the attributes accessed by the execution mixin."""

    timeout: int
    execute_timeout: int | None
    repo_url: str | None
    repo_ref: str | None
    context_paths: list[str]
    sandbox_spec: SandboxSpec | None
    sub_lm: Any
    llm_call_timeout: float
    _bridge_context_id: str | None
    _bridge_tools: Callable[..., Any]
    _reject_unsupported_recursive_callbacks: Callable[..., None]
    _requires_bridge: Callable[..., bool]
    _aensure_session_impl: Callable[..., Any]
    _aclose_bridge: Callable[..., Any]
    _check_and_increment_llm_calls: Callable[..., bool]


class _DaytonaInterpreterExecutionMixin(_DaytonaInterpreterLike):
    def _parent_session_for_child(self) -> DaytonaSandboxSession | None:
        parent_session = getattr(self, "_session", None)
        if parent_session is None or getattr(parent_session, "sandbox", None) is None:
            return None
        return parent_session

    def _build_child_interpreter(
        self,
        *,
        runtime: DaytonaSandboxRuntime,
        owns_runtime: bool,
        delete_session_on_shutdown: bool,
        delete_context_on_shutdown: bool = False,
        remaining_llm_budget: int,
    ) -> Any:
        return cast(Any, self).__class__(
            runtime=runtime,
            owns_runtime=owns_runtime,
            timeout=self.timeout,
            execute_timeout=self.execute_timeout,
            volume_name=self.volume_name,
            repo_url=self.repo_url,
            repo_ref=self.repo_ref,
            context_paths=list(self.context_paths),
            sandbox_spec=getattr(self, "sandbox_spec", None),
            delete_session_on_shutdown=delete_session_on_shutdown,
            delete_context_on_shutdown=delete_context_on_shutdown,
            sub_lm=self.sub_lm,
            max_llm_calls=remaining_llm_budget,
            llm_call_timeout=self.llm_call_timeout,
            default_execution_profile=ExecutionProfile.RLM_DELEGATE,
            async_execute=self.async_execute,
        )

    def _attach_shared_parent_session(
        self,
        child: Any,
        *,
        parent_session: DaytonaSandboxSession,
        runtime: DaytonaSandboxRuntime,
    ) -> None:
        child._session = DaytonaSandboxSession(
            sandbox=parent_session.sandbox,
            repo_url=parent_session.repo_url,
            ref=parent_session.ref,
            volume_name=parent_session.volume_name,
            workspace_path=parent_session.workspace_path,
            context_sources=list(parent_session.context_sources),
            volume_mount_path=parent_session.volume_mount_path,
            context_id=None,
        )
        child._session._runtime_ref = runtime
        try:
            child._session.bind_current_async_owner()
        except RuntimeError as exc:
            import logging

            logger = logging.getLogger(__name__)
            logger.debug(
                "Failed to bind Daytona sandbox session to current async owner: %s",
                exc,
            )
        child._persisted_sandbox_id = parent_session.sandbox_id
        child._persisted_workspace_path = parent_session.workspace_path

    def _propagate_parent_recursion_state(self, child: Any) -> None:
        from fleet_rlm.runtime.execution.interpreter_support import (
            initialize_sub_rlm_state,
        )

        setattr(
            child,
            "_check_and_increment_llm_calls",
            self._check_and_increment_llm_calls,
        )
        parent_depth = getattr(self, "_sub_rlm_depth", 0)
        parent_max = getattr(self, "_sub_rlm_max_depth", 2)
        initialize_sub_rlm_state(child, depth=parent_depth + 1, max_depth=parent_max)

    def execute(
        self,
        code: str,
        variables: dict[str, Any] | None = None,
        *,
        execution_profile: ExecutionProfile | None = None,
    ) -> str | FinalOutput:
        return _run_async_compat(
            self.aexecute,
            code,
            variables,
            execution_profile=execution_profile,
        )

    async def aexecute(
        self,
        code: str,
        variables: dict[str, Any] | None = None,
        *,
        execution_profile: ExecutionProfile | None = None,
    ) -> str | FinalOutput:
        session = await self._aensure_session_impl()
        await session.astart_driver(timeout=float(self.execute_timeout or self.timeout))
        safe_vars = self.safe_variables(variables)
        profile = execution_profile or self.default_execution_profile
        profile_value = profile.value if hasattr(profile, "value") else str(profile)
        code_hash, code_preview = summarize_code(code)
        started_at = time.time()
        emit_execution_event(
            self,
            start_event_data(
                execution_profile=str(profile_value),
                code_hash=code_hash,
                code_preview=code_preview,
            ),
        )
        try:
            response = await self.aexecute_in_session(
                session=session,
                code=code,
                variables=safe_vars,
            )
        except Exception as exc:
            emit_execution_event(
                self,
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
        return self.finalize_execution_result(
            response=response,
            started_at=started_at,
            execution_profile=str(profile_value),
            code_hash=code_hash,
            code_preview=code_preview,
        )

    def safe_variables(self, variables: dict[str, Any] | None) -> dict[str, Any]:
        safe_vars: dict[str, Any] = {}
        for key, value in (variables or {}).items():
            normalized_key = str(key)
            try:
                json.dumps(value)
                safe_vars[normalized_key] = value
            except TypeError:
                safe_vars[normalized_key] = str(value)
        return safe_vars

    def submit_signature(self) -> tuple[tuple[str, str], ...] | None:
        if not self.output_fields:
            return None
        normalized: list[tuple[str, str]] = []
        for field in self.output_fields:
            name = str(field.get("name") or "").strip()
            if not name:
                continue
            normalized.append((name, str(field.get("type") or "").strip()))
        return tuple(normalized) or None

    async def aensure_setup(
        self,
        session: DaytonaSandboxSession,
        *,
        base_setup_code: Callable[..., str] = _base_setup_code,
        generic_submit_code: Callable[[], str] = _generic_submit_code,
        typed_submit_code: Callable[[list[dict[str, Any]]], str] = _typed_submit_code,
        submit_signature_fn: Callable[[], tuple[tuple[str, str], ...] | None]
        | None = None,
    ) -> Any:
        submit_signature_fn = submit_signature_fn or self.submit_signature
        context = await session.aensure_context()
        if (
            self._setup_context_id != session.context_id
            or self._setup_workspace_path != session.workspace_path
        ):
            result = await _await_if_needed(
                session.sandbox.code_interpreter.run_code(
                    base_setup_code(
                        workspace_path=session.workspace_path,
                        volume_mount_path=self.volume_mount_path,
                    ),
                    context=context,
                )
            )
            if result.error:
                raise CodeInterpreterError(
                    f"Failed to initialize Daytona sandbox helpers: {result.error.value}"
                )
            self._setup_context_id = session.context_id
            self._setup_workspace_path = session.workspace_path
            self._submit_signature_key = None

        current_submit_signature = submit_signature_fn()
        if current_submit_signature is None:
            if self._submit_signature_key is not None:
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
                self._submit_signature_key = None
            return context

        if current_submit_signature != self._submit_signature_key:
            result = await _await_if_needed(
                session.sandbox.code_interpreter.run_code(
                    typed_submit_code(self.output_fields or []),
                    context=context,
                )
            )
            if result.error:
                raise CodeInterpreterError(
                    f"Failed to register typed SUBMIT: {result.error.value}"
                )
            self._submit_signature_key = current_submit_signature
        return context

    async def aensure_bridge(
        self,
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
        bridge = self._bridge
        if (
            bridge is None
            or self._bridge_sandbox_id != sandbox_id
            or self._bridge_context_id != context_id
        ):
            await self._aclose_bridge()
            bridge = bridge_cls(
                sandbox=session.sandbox,
                context=context,
            )
            self._bridge = bridge
            self._bridge_sandbox_id = sandbox_id
            self._bridge_context_id = context_id
        else:
            bridge.bind_context(context)
        await bridge.async_tools(tools)
        return bridge

    async def aexecute_in_session(
        self,
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
        callbacks = self._resolve_execution_callbacks(
            bridge_tools_fn=bridge_tools_fn,
            reject_unsupported_recursive_callbacks_fn=reject_unsupported_recursive_callbacks_fn,
            requires_bridge_fn=requires_bridge_fn,
            aensure_bridge_fn=aensure_bridge_fn,
            aexecute_direct_fn=aexecute_direct_fn,
            response_from_execution_fn=response_from_execution_fn,
        )
        context = await self.aensure_setup(
            session,
            submit_signature_fn=self.submit_signature,
        )
        prepared_code = self._prepare_execution_code(
            code=code,
            variables=variables,
            reject_recursive_callbacks=callbacks.reject_recursive_callbacks,
        )
        execution = await self._arun_prepared_execution(
            session=session,
            context=context,
            code=prepared_code,
            callbacks=callbacks,
        )
        return callbacks.response_from_execution(execution)

    def _resolve_execution_callbacks(
        self,
        *,
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
    ) -> _ExecutionCallbacks:
        return _ExecutionCallbacks(
            bridge_tools=bridge_tools_fn or self._bridge_tools,
            reject_recursive_callbacks=reject_unsupported_recursive_callbacks_fn
            or self._reject_unsupported_recursive_callbacks,
            requires_bridge=requires_bridge_fn or self._requires_bridge,
            ensure_bridge=aensure_bridge_fn
            or (
                lambda *, session, context, tools: self.aensure_bridge(
                    session=session,
                    context=context,
                    tools=tools,
                )
            ),
            execute_direct=aexecute_direct_fn
            or (
                lambda *, session, context, code: self.aexecute_direct(
                    session=session,
                    context=context,
                    code=code,
                )
            ),
            response_from_execution=response_from_execution_fn
            or (lambda execution: self.response_from_execution(execution)),
        )

    def _prepare_execution_code(
        self,
        *,
        code: str,
        variables: dict[str, Any],
        reject_recursive_callbacks: Callable[[str], None],
    ) -> str:
        prepared_code = self.inject_variables(code, variables)
        reject_recursive_callbacks(prepared_code)
        return prepared_code

    async def _arun_prepared_execution(
        self,
        *,
        session: DaytonaSandboxSession,
        context: Any,
        code: str,
        callbacks: _ExecutionCallbacks,
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
                timeout=int(self.execute_timeout or self.timeout),
                tool_executor=lambda name, args, kwargs: invoke_tool(
                    self,
                    name,
                    args,
                    kwargs,
                ),
            )
        return await callbacks.execute_direct(
            session=session,
            context=context,
            code=code,
        )

    async def aexecute_direct(
        self,
        *,
        session: DaytonaSandboxSession,
        context: Any,
        code: str,
        envs: dict[str, str] | None = None,
    ) -> DaytonaBridgeExecution:
        """Run *code* directly via the sandbox code interpreter.

        With Daytona SDK v0.167.0+, ``code_interpreter.run_code`` is a thin
        client wrapper around a server-side daemon endpoint, so execution
        happens inside the sandbox without local SDK machinery.
        """
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
                timeout=int(self.execute_timeout or self.timeout),
            )
        )
        return DaytonaBridgeExecution(
            result=result,
            stdout="".join(stdout_parts),
            stderr="".join(stderr_parts),
            callback_count=0,
        )

    def response_from_execution(
        self,
        execution: DaytonaBridgeExecution,
        *,
        extract_final_artifact_fn: Callable[[str], dict[str, Any] | None] | None = None,
    ) -> _DaytonaExecutionResponse:
        extract_final_artifact_fn = (
            extract_final_artifact_fn or self.extract_final_artifact
        )
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

    @staticmethod
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
        self,
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
                self,
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
                self,
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
            self,
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

    def inject_variables(self, code: str, variables: dict[str, Any]) -> str:
        if not variables:
            return code
        assignments = [
            f"{name} = {self.literal(value)}" for name, value in variables.items()
        ]
        return "\n".join(assignments) + "\n" + code

    def literal(self, value: Any) -> str:
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
            return "[" + ", ".join(self.literal(item) for item in value) + "]"
        if isinstance(value, tuple):
            inner = ", ".join(self.literal(item) for item in value)
            if len(value) == 1:
                inner += ","
            return "(" + inner + ")"
        if isinstance(value, set):
            if not value:
                return "set()"
            return "{" + ", ".join(self.literal(item) for item in value) + "}"
        if isinstance(value, dict):
            pairs = [
                f"{self.literal(key)}: {self.literal(item)}"
                for key, item in value.items()
            ]
            return "{" + ", ".join(pairs) + "}"
        raise CodeInterpreterError(f"Unsupported value type: {type(value).__name__}")


def _parent_session_for_child(interpreter: Any) -> DaytonaSandboxSession | None:
    parent_session = getattr(interpreter, "_session", None)
    if parent_session is None or getattr(parent_session, "sandbox", None) is None:
        return None
    return parent_session


def _build_child_interpreter(
    interpreter: Any,
    *,
    runtime: DaytonaSandboxRuntime,
    owns_runtime: bool,
    delete_session_on_shutdown: bool,
    delete_context_on_shutdown: bool = False,
    remaining_llm_budget: int,
) -> Any:
    return interpreter.__class__(
        runtime=runtime,
        owns_runtime=owns_runtime,
        timeout=interpreter.timeout,
        execute_timeout=interpreter.execute_timeout,
        volume_name=interpreter.volume_name,
        repo_url=interpreter.repo_url,
        repo_ref=interpreter.repo_ref,
        context_paths=list(interpreter.context_paths),
        sandbox_spec=getattr(interpreter, "sandbox_spec", None),
        delete_session_on_shutdown=delete_session_on_shutdown,
        delete_context_on_shutdown=delete_context_on_shutdown,
        sub_lm=interpreter.sub_lm,
        max_llm_calls=remaining_llm_budget,
        llm_call_timeout=interpreter.llm_call_timeout,
        default_execution_profile=ExecutionProfile.RLM_DELEGATE,
        async_execute=interpreter.async_execute,
    )


def _attach_shared_parent_session(
    child: Any,
    *,
    parent_session: DaytonaSandboxSession,
    runtime: DaytonaSandboxRuntime,
) -> None:
    child._session = DaytonaSandboxSession(
        sandbox=parent_session.sandbox,
        repo_url=parent_session.repo_url,
        ref=parent_session.ref,
        volume_name=parent_session.volume_name,
        workspace_path=parent_session.workspace_path,
        context_sources=list(parent_session.context_sources),
        volume_mount_path=parent_session.volume_mount_path,
        context_id=None,
    )
    child._session._runtime_ref = runtime
    try:
        child._session.bind_current_async_owner()
    except RuntimeError as exc:
        logger = logging.getLogger(__name__)
        logger.debug(
            "Failed to bind Daytona sandbox session to current async owner: %s",
            exc,
        )
    child._persisted_sandbox_id = parent_session.sandbox_id
    child._persisted_workspace_path = parent_session.workspace_path


def _propagate_parent_recursion_state(child: Any, parent: Any) -> None:
    from fleet_rlm.runtime.execution.interpreter_support import initialize_sub_rlm_state

    setattr(
        child,
        "_check_and_increment_llm_calls",
        parent._check_and_increment_llm_calls,
    )
    parent_depth = getattr(parent, "_sub_rlm_depth", 0)
    parent_max = getattr(parent, "_sub_rlm_max_depth", 2)
    initialize_sub_rlm_state(child, depth=parent_depth + 1, max_depth=parent_max)


def build_delegate_child(
    interpreter: Any,
    *,
    remaining_llm_budget: int,
) -> Any:
    """Build a child interpreter for sub_rlm() calls.

    Optimization: reuses the parent's sandbox session with a fresh
    execution context instead of creating a new container (~30-60s saved).
    Falls back to a new sandbox if the parent has no active session.

    Uses Daytona's ``sandbox.code_interpreter.create_context()`` for
    isolation — see https://www.daytona.io/docs/sdk-reference
    """
    parent_session = _parent_session_for_child(interpreter)
    if parent_session is not None:
        child = _build_child_interpreter(
            interpreter,
            runtime=interpreter.runtime,
            owns_runtime=False,
            delete_session_on_shutdown=False,
            delete_context_on_shutdown=True,
            remaining_llm_budget=remaining_llm_budget,
        )
        _attach_shared_parent_session(
            child,
            parent_session=parent_session,
            runtime=interpreter.runtime,
        )
    else:
        runtime = DaytonaSandboxRuntime(config=interpreter.runtime._resolved_config)
        child = _build_child_interpreter(
            interpreter,
            runtime=runtime,
            owns_runtime=True,
            delete_session_on_shutdown=True,
            remaining_llm_budget=remaining_llm_budget,
        )

    _propagate_parent_recursion_state(child, interpreter)
    return child


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
    # Prefer dspy.RLM-injected llm_query (fresh per-forward counter + sub_lm);
    # fall back to interpreter methods when running outside dspy.RLM.
    if "llm_query" not in tools:
        tools["llm_query"] = interpreter.llm_query
    if "llm_query_batched" not in tools:
        tools["llm_query_batched"] = interpreter.llm_query_batched
    # True-RLM symbolic recursion primitives (Algorithm 1, arXiv 2512.24601v2).
    # These allow sandbox code to call sub_rlm() inside loops.
    if "sub_rlm" not in tools and hasattr(interpreter, "sub_rlm"):
        tools["sub_rlm"] = interpreter.sub_rlm
    if "sub_rlm_batched" not in tools and hasattr(interpreter, "sub_rlm_batched"):
        tools["sub_rlm_batched"] = interpreter.sub_rlm_batched
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
        # Prefer dspy.RLM-injected tools (fresh counter + sub_lm per forward());
        # fall back to interpreter methods for standalone use.
        if name in interpreter._tools:
            value = interpreter._tools[name](*args, **kwargs)
        elif name == "llm_query":
            prompt = args[0] if args else kwargs.get("prompt", "")
            value = interpreter.llm_query(str(prompt))
        elif name == "llm_query_batched":
            prompts = args[0] if args else kwargs.get("prompts", [])
            if not isinstance(prompts, list):
                prompts = []
            value = interpreter.llm_query_batched([str(item) for item in prompts])
        else:
            raise RuntimeError(f"Unknown host callback: {name}")
        try:
            json.dumps(value)
            return value
        except TypeError:
            return str(value)
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


class DaytonaInterpreter(
    _DaytonaInterpreterExecutionMixin,
    LLMQueryMixin,
    StatefulWorkspaceInterpreterProtocol,
):
    """Stateful Daytona interpreter that plugs into canonical ``dspy.RLM`` flows."""

    def __init__(
        self,
        *,
        runtime: DaytonaSandboxRuntime | None = None,
        owns_runtime: bool = False,
        timeout: int = 900,
        execute_timeout: int | None = None,
        volume_name: str | None = None,
        repo_url: str | None = None,
        repo_ref: str | None = None,
        context_paths: list[str] | None = None,
        sandbox_spec: Any | None = None,
        delete_session_on_shutdown: bool = True,
        delete_context_on_shutdown: bool = False,
        sub_lm: dspy.LM | None = None,
        max_llm_calls: int = 50,
        llm_call_timeout: int = 60,
        default_execution_profile: ExecutionProfile = ExecutionProfile.RLM_DELEGATE,
        async_execute: bool = True,
    ) -> None:
        provided_runtime = runtime
        self.runtime = provided_runtime or DaytonaSandboxRuntime()
        self._owns_runtime = owns_runtime or provided_runtime is None
        self._runtime_config = getattr(self.runtime, "_resolved_config", None)
        self._runtime_closed = False
        self.timeout = timeout
        self.execute_timeout = execute_timeout or timeout
        self.volume_name = volume_name
        self.volume_mount_path = str(DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH)
        self.repo_url = repo_url
        self.repo_ref = repo_ref
        self.context_paths = dedupe_paths(list(context_paths or []))
        self.sandbox_spec = sandbox_spec  # SandboxSpec with optional Image builder
        self.delete_session_on_shutdown = delete_session_on_shutdown
        self.delete_context_on_shutdown = delete_context_on_shutdown
        self.default_execution_profile = default_execution_profile
        self.async_execute = async_execute

        initialize_llm_query_state(
            self,
            sub_lm=sub_lm,
            max_llm_calls=max_llm_calls,
            llm_call_timeout=llm_call_timeout,
        )
        initialize_sub_rlm_state(self)
        self.output_fields: list[dict[str, Any]] | None
        self._tools: dict[str, Callable[..., Any]]
        self.execution_event_callback: Callable[[dict[str, Any]], None] | None
        initialize_tool_runtime_state(self)
        self._volume = None

        self._started = False
        self._session: DaytonaSandboxSession | None = None
        self._session_source_key: (
            tuple[str | None, str | None, tuple[str, ...], str | None] | None
        ) = None
        self._persisted_sandbox_id: str | None = None
        self._persisted_workspace_path: str | None = None
        self._persisted_context_sources: list[Any] = []
        self._persisted_context_id: str | None = None
        self._persisted_volume_name: str | None = None
        self._bridge: DaytonaToolBridge | None = None
        self._bridge_sandbox_id: str | None = None
        self._bridge_context_id: str | None = None
        self._setup_context_id: str | None = None
        self._setup_workspace_path: str | None = None
        self._submit_signature_key: tuple[tuple[str, str], ...] | None = None
        self._last_sandbox_transition: str | None = None
        self._last_workspace_reconfigured = False
        self._runtime_degraded = False
        self._runtime_failure_category: str | None = None
        self._runtime_failure_phase: str | None = None
        self._runtime_fallback_used = False

    @property
    def execution_event_callback(self) -> Callable[[dict[str, Any]], None] | None:
        return getattr(self, "_execution_event_callback", None)

    @execution_event_callback.setter
    def execution_event_callback(
        self, value: Callable[[dict[str, Any]], None] | None
    ) -> None:
        self._execution_event_callback = value
        session = getattr(self, "_session", None)
        if session is not None:
            setattr(session, "execution_event_callback", value)

    def __enter__(self) -> DaytonaInterpreter:
        return _sync_enter_impl(self)

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        _ = (exc_type, exc_val, exc_tb)
        return _sync_exit_impl(self)

    async def __aenter__(self) -> DaytonaInterpreter:
        return await _async_enter_impl(self)

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> bool:
        _ = (exc_type, exc_val, exc_tb)
        return await _async_exit_impl(self)

    @property
    def tools(self) -> dict[str, Callable[..., Any]]:
        return get_registered_tools(self)

    @tools.setter
    def tools(self, value: dict[str, Callable[..., Any]]) -> None:
        set_registered_tools(self, value)

    def execution_profile(self, profile: ExecutionProfile):
        return execution_profile_context(self, profile)

    def configure_workspace(
        self,
        *,
        repo_url: str | None,
        repo_ref: str | None,
        context_paths: list[str] | None,
        volume_name: str | None,
        force_new_session: bool = False,
    ) -> None:
        (
            normalized_repo_url,
            normalized_repo_ref,
            normalized_context_paths,
            normalized_volume,
            source_key,
        ) = self._normalized_workspace_config(
            repo_url=repo_url,
            repo_ref=repo_ref,
            context_paths=context_paths,
            volume_name=volume_name,
        )
        should_recreate = force_new_session or self._session_needs_recreation(
            desired_volume=normalized_volume
        )
        if should_recreate:
            self._detach_session(delete=True)
        self._apply_workspace_config(
            repo_url=normalized_repo_url,
            repo_ref=normalized_repo_ref,
            context_paths=normalized_context_paths,
            volume_name=normalized_volume,
        )
        if not should_recreate and self._session is not None:
            self._last_sandbox_transition = "reused"
            self._last_workspace_reconfigured = self._session_source_key != source_key

    async def aconfigure_workspace(
        self,
        *,
        repo_url: str | None,
        repo_ref: str | None,
        context_paths: list[str] | None,
        volume_name: str | None,
        force_new_session: bool = False,
    ) -> None:
        (
            normalized_repo_url,
            normalized_repo_ref,
            normalized_context_paths,
            normalized_volume,
            source_key,
        ) = self._normalized_workspace_config(
            repo_url=repo_url,
            repo_ref=repo_ref,
            context_paths=context_paths,
            volume_name=volume_name,
        )
        should_recreate = force_new_session or self._session_needs_recreation(
            desired_volume=normalized_volume
        )
        if should_recreate:
            await self._adetach_session(delete=True)
        self._apply_workspace_config(
            repo_url=normalized_repo_url,
            repo_ref=normalized_repo_ref,
            context_paths=normalized_context_paths,
            volume_name=normalized_volume,
        )
        if not should_recreate and self._session is not None:
            self._last_sandbox_transition = "reused"
            self._last_workspace_reconfigured = self._session_source_key != source_key

    def _normalized_workspace_config(
        self,
        *,
        repo_url: str | None,
        repo_ref: str | None,
        context_paths: list[str] | None,
        volume_name: str | None,
    ) -> tuple[
        str | None,
        str | None,
        list[str],
        str | None,
        tuple[str | None, str | None, tuple[str, ...], str | None],
    ]:
        normalized_repo_url = str(repo_url or "").strip() or None
        normalized_repo_ref = str(repo_ref or "").strip() or None
        normalized_context_paths = dedupe_paths(list(context_paths or []))
        normalized_volume = str(volume_name or "").strip() or None
        source_key = (
            normalized_repo_url,
            normalized_repo_ref,
            tuple(normalized_context_paths),
            normalized_volume,
        )
        return (
            normalized_repo_url,
            normalized_repo_ref,
            normalized_context_paths,
            normalized_volume,
            source_key,
        )

    def _apply_workspace_config(
        self,
        *,
        repo_url: str | None,
        repo_ref: str | None,
        context_paths: list[str],
        volume_name: str | None,
    ) -> None:
        self.repo_url = repo_url
        self.repo_ref = repo_ref
        self.context_paths = context_paths
        self.volume_name = volume_name

    def _session_needs_recreation(self, *, desired_volume: str | None) -> bool:
        active_session = self._session
        if active_session is not None:
            return getattr(active_session, "volume_name", None) != desired_volume
        if self._persisted_sandbox_id is None:
            return False
        return self._persisted_volume_name != desired_volume

    @staticmethod
    def _callable_accepts_kwarg(func: Callable[..., Any] | None, name: str) -> bool:
        if not callable(func):
            return False
        try:
            signature = inspect.signature(func)
        except (TypeError, ValueError):
            return False
        if name in signature.parameters:
            return True
        return any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in signature.parameters.values()
        )

    async def _aresume_workspace_session(
        self,
        *,
        sandbox_id: str,
        repo_url: str | None,
        ref: str | None,
        workspace_path: str,
        context_sources: list[Any],
        context_id: str | None,
    ) -> DaytonaSandboxSession:
        resume_workspace_session = getattr(self.runtime, "aresume_workspace_session")
        resume_kwargs: dict[str, Any] = {
            "sandbox_id": sandbox_id,
            "repo_url": repo_url,
            "ref": ref,
            "workspace_path": workspace_path,
            "context_sources": context_sources,
            "context_id": context_id,
        }
        if self._callable_accepts_kwarg(resume_workspace_session, "volume_name"):
            resume_kwargs["volume_name"] = (
                self._persisted_volume_name or self.volume_name
            )
        return await resume_workspace_session(**resume_kwargs)

    async def _areconcile_workspace_session(
        self,
        session: DaytonaSandboxSession,
    ) -> DaytonaSandboxSession:
        reconcile_workspace_session = getattr(
            self.runtime, "areconcile_workspace_session", None
        )
        if not callable(reconcile_workspace_session):
            raise RuntimeError("Runtime does not support workspace reconciliation")
        return await reconcile_workspace_session(
            session,
            repo_url=self.repo_url,
            ref=self.repo_ref,
            context_paths=list(self.context_paths),
        )

    def _apply_imported_session_state(self, state: dict[str, Any]) -> None:
        raw_daytona = state.get("daytona", {})
        daytona_state = raw_daytona if isinstance(raw_daytona, dict) else {}
        self.repo_url = str(daytona_state.get("repo_url", "") or "").strip() or None
        self.repo_ref = str(daytona_state.get("repo_ref", "") or "").strip() or None
        self.context_paths = dedupe_paths(
            [str(item) for item in daytona_state.get("context_paths", []) or []]
        )
        self._persisted_sandbox_id = (
            str(daytona_state.get("sandbox_id", "") or "").strip() or None
        )
        self._persisted_workspace_path = (
            str(daytona_state.get("workspace_path", "") or "").strip() or None
        )
        self._persisted_context_sources = normalized_context_sources(
            daytona_state.get("context_sources", [])
        )
        self._persisted_context_id = (
            str(daytona_state.get("context_id", "") or "").strip() or None
        )
        self._persisted_volume_name = (
            str(daytona_state.get("volume_name", "") or "").strip() or None
        )
        self.volume_name = self._persisted_volume_name or self.volume_name
        self._session_source_key = (
            self.repo_url,
            self.repo_ref,
            tuple(self.context_paths),
            self.volume_name,
        )

    def export_session_state(self) -> dict[str, Any]:
        self._persist_session_snapshot()
        context_sources = (
            list(self._session.context_sources)
            if self._session is not None
            else list(self._persisted_context_sources)
        )
        return {
            "daytona": {
                "repo_url": self.repo_url,
                "repo_ref": self.repo_ref,
                "context_paths": list(self.context_paths),
                "sandbox_id": (
                    self._session.sandbox_id
                    if self._session is not None
                    else self._persisted_sandbox_id
                ),
                "workspace_path": (
                    self._session.workspace_path
                    if self._session is not None
                    else self._persisted_workspace_path
                ),
                "context_sources": [
                    item.to_dict() if hasattr(item, "to_dict") else item
                    for item in context_sources
                ],
                "context_id": (
                    self._session.context_id
                    if self._session is not None
                    else self._persisted_context_id
                ),
                "volume_name": (
                    getattr(self._session, "volume_name", None) or self.volume_name
                    if self._session is not None
                    else self._persisted_volume_name or self.volume_name
                ),
            }
        }

    def import_session_state(self, state: dict[str, Any]) -> None:
        self._detach_session(delete=False)
        self._apply_imported_session_state(state)

    async def aimport_session_state(self, state: dict[str, Any]) -> None:
        await self._adetach_session(delete=False)
        self._apply_imported_session_state(state)

    def start(self) -> None:
        _run_async_compat(self.astart)

    async def astart(self) -> None:
        if self._started:
            return
        session = await self._aensure_session_impl()
        await session.astart_driver(timeout=float(self.execute_timeout or self.timeout))
        self._started = True

    def shutdown(self) -> None:
        _run_async_compat(self.ashutdown)

    async def ashutdown(self) -> None:
        try:
            await self._adetach_session(delete=self.delete_session_on_shutdown)
        finally:
            self._started = False
            await self._aclose_runtime()

    def _ensure_session_sync(self) -> DaytonaSandboxSession:
        return _run_async_compat(self._aensure_session_impl)

    def _session_matches_current_async_owner(
        self, session: DaytonaSandboxSession
    ) -> bool:
        matches_current_owner = getattr(session, "matches_current_async_owner", None)
        if callable(matches_current_owner):
            return bool(matches_current_owner())
        return False

    def _current_session_source_key(
        self,
    ) -> tuple[str | None, str | None, tuple[str, ...], str | None]:
        return (
            self.repo_url,
            self.repo_ref,
            tuple(self.context_paths),
            self.volume_name,
        )

    def _attach_execution_callback(
        self, session: DaytonaSandboxSession | None
    ) -> DaytonaSandboxSession | None:
        if session is not None:
            session.execution_event_callback = self.execution_event_callback
        return session

    async def _afinalize_session(
        self,
        session: DaytonaSandboxSession,
        *,
        source_key: tuple[str | None, str | None, tuple[str, ...], str | None],
        transition: str,
        workspace_reconfigured: bool,
    ) -> DaytonaSandboxSession:
        self._session = self._attach_execution_callback(session)
        self._session_source_key = source_key
        await self._areset_execution_state()
        self._persist_session_snapshot()
        self._last_sandbox_transition = transition
        self._last_workspace_reconfigured = workspace_reconfigured
        return session

    async def _arelease_loop_mismatched_session(self) -> None:
        await self._adetach_session(delete=False)
        self._persisted_context_id = None

    async def _aresolve_active_session(
        self,
        *,
        source_key: tuple[str | None, str | None, tuple[str, ...], str | None],
    ) -> tuple[DaytonaSandboxSession | None, bool]:
        active_session = self._session
        if active_session is None:
            return None, False

        if not self._session_matches_current_async_owner(active_session):
            await self._arelease_loop_mismatched_session()
            return None, False

        if self._session_needs_recreation(desired_volume=self.volume_name):
            await self._adetach_session(delete=True)
            return None, True

        if self._session_source_key == source_key:
            session = await self._afinalize_session(
                active_session,
                source_key=source_key,
                transition="reused",
                workspace_reconfigured=False,
            )
            return session, False

        try:
            reconciled = await self._areconcile_workspace_session(active_session)
        except Exception as exc:
            self._mark_runtime_degradation_from_exception(exc)
            await self._adetach_session(delete=True)
            return None, True

        session = await self._afinalize_session(
            reconciled,
            source_key=source_key,
            transition="reused",
            workspace_reconfigured=True,
        )
        return session, False

    def _clear_persisted_session_for_volume_change(self) -> bool:
        if self._persisted_sandbox_id is None:
            return False
        if self._persisted_volume_name == self.volume_name:
            return False
        self._clear_persisted_session()
        return True

    @staticmethod
    def _should_reconcile_resumed_session(
        persisted_source_key: tuple[str | None, str | None, tuple[str, ...], str | None]
        | None,
        source_key: tuple[str | None, str | None, tuple[str, ...], str | None],
    ) -> bool:
        return persisted_source_key is not None and persisted_source_key != source_key

    async def _aresolve_persisted_session(
        self,
        *,
        source_key: tuple[str | None, str | None, tuple[str, ...], str | None],
    ) -> tuple[DaytonaSandboxSession | None, bool]:
        if not (self._persisted_sandbox_id and self._persisted_workspace_path):
            return None, False

        try:
            persisted_source_key = self._session_source_key
            resumed = await self._aresume_workspace_session(
                sandbox_id=self._persisted_sandbox_id,
                repo_url=self.repo_url,
                ref=self.repo_ref,
                workspace_path=self._persisted_workspace_path,
                context_sources=self._persisted_context_sources,
                context_id=self._persisted_context_id,
            )
            workspace_reconfigured = False
            if self._should_reconcile_resumed_session(persisted_source_key, source_key):
                resumed = await self._areconcile_workspace_session(resumed)
                workspace_reconfigured = True

            session = await self._afinalize_session(
                resumed,
                source_key=source_key,
                transition="resumed",
                workspace_reconfigured=workspace_reconfigured,
            )
            return session, False
        except Exception as exc:
            self._mark_runtime_degradation_from_exception(exc)
            self._clear_persisted_session()
            return None, True

    async def _acreate_session_from_runtime(
        self,
        *,
        source_key: tuple[str | None, str | None, tuple[str, ...], str | None],
        should_report_recreated: bool,
    ) -> DaytonaSandboxSession:
        session = await self.runtime.acreate_workspace_session(
            repo_url=self.repo_url,
            ref=self.repo_ref,
            context_paths=list(self.context_paths),
            volume_name=self.volume_name,
            spec=self.sandbox_spec,
        )
        return await self._afinalize_session(
            session,
            source_key=source_key,
            transition="recreated" if should_report_recreated else "created",
            workspace_reconfigured=False,
        )

    async def _aensure_session_impl(self) -> DaytonaSandboxSession:
        self._ensure_runtime_available()
        source_key = self._current_session_source_key()
        should_report_recreated = False

        active_session, active_recreated = await self._aresolve_active_session(
            source_key=source_key
        )
        if active_session is not None:
            return active_session
        should_report_recreated = should_report_recreated or active_recreated

        if self._clear_persisted_session_for_volume_change():
            should_report_recreated = True

        persisted_session, persisted_recreated = await self._aresolve_persisted_session(
            source_key=source_key
        )
        if persisted_session is not None:
            return persisted_session
        should_report_recreated = should_report_recreated or persisted_recreated

        return await self._acreate_session_from_runtime(
            source_key=source_key,
            should_report_recreated=should_report_recreated,
        )

    async def _aensure_session(self) -> DaytonaSandboxSession:
        session = await self._aensure_session_impl()
        await session.arefresh_activity()
        return session

    async def aget_session(self) -> DaytonaSandboxSession:
        """Public async accessor to ensure and return the active sandbox session."""
        return await self._aensure_session()

    def _persist_session_snapshot(
        self, session: DaytonaSandboxSession | None = None
    ) -> None:
        active_session = session or self._session
        if active_session is None:
            return
        self._persisted_sandbox_id = active_session.sandbox_id
        self._persisted_workspace_path = active_session.workspace_path
        self._persisted_context_sources = list(active_session.context_sources)
        self._persisted_context_id = active_session.context_id
        self._persisted_volume_name = (
            getattr(active_session, "volume_name", None) or self.volume_name
        )

    def _clear_persisted_session(self) -> None:
        self._persisted_sandbox_id = None
        self._persisted_workspace_path = None
        self._persisted_context_sources = []
        self._persisted_context_id = None
        self._persisted_volume_name = None

    def _detach_session(self, *, delete: bool) -> None:
        _run_async_compat(self._adetach_session, delete=delete)

    async def _adetach_session(self, *, delete: bool) -> None:
        active_session = self._session
        if active_session is None:
            if delete:
                self._clear_persisted_session()
            await self._areset_execution_state()
            self._started = False
            return

        self._persist_session_snapshot(active_session)
        await self._aclose_bridge()
        try:
            if delete:
                await active_session.adelete()
            elif self.delete_context_on_shutdown:
                await active_session.adelete_context()
            else:
                await active_session.aclose_driver()
        finally:
            if delete:
                self._clear_persisted_session()
            self._session = None
            if delete:
                self._session_source_key = None
            await self._areset_execution_state()
            self._started = False

    def _close_bridge(self) -> None:
        _run_async_compat(self._aclose_bridge)

    async def _aclose_bridge(self) -> None:
        bridge = self._bridge
        self._bridge = None
        self._bridge_sandbox_id = None
        self._bridge_context_id = None
        if bridge is not None:
            await bridge.aclose()

    async def _aclose_runtime(self) -> None:
        if not self._owns_runtime or self._runtime_closed:
            return
        await self.runtime.aclose()
        self._runtime_closed = True

    def _ensure_runtime_available(self) -> None:
        runtime = self.runtime
        if not self._owns_runtime or not isinstance(runtime, DaytonaSandboxRuntime):
            return
        if getattr(runtime, "_client", None) is not None:
            return
        if self._runtime_config is None:
            raise RuntimeError(
                "Owned Daytona runtime cannot be recreated without config"
            )
        self.runtime = DaytonaSandboxRuntime(config=self._runtime_config)
        self._runtime_closed = False

    def _reset_execution_state(self) -> None:
        _run_async_compat(self._areset_execution_state)

    async def _areset_execution_state(self) -> None:
        await self._aclose_bridge()
        self._setup_context_id = None
        self._setup_workspace_path = None
        self._submit_signature_key = None

    def reset_runtime_degradation_state(self) -> None:
        self._runtime_degraded = False
        self._runtime_failure_category = None
        self._runtime_failure_phase = None
        self._runtime_fallback_used = False

    def mark_runtime_degradation(
        self,
        *,
        category: str | None = None,
        phase: str | None = None,
        fallback_used: bool = False,
    ) -> None:
        self._runtime_degraded = True
        category_value = str(category or "").strip() or None
        phase_value = str(phase or "").strip() or None
        if self._runtime_failure_category is None and category_value is not None:
            self._runtime_failure_category = category_value
        if self._runtime_failure_phase is None and phase_value is not None:
            self._runtime_failure_phase = phase_value
        if fallback_used:
            self._runtime_fallback_used = True

    def _mark_runtime_degradation_from_exception(self, exc: BaseException) -> None:
        self.mark_runtime_degradation(
            category=str(getattr(exc, "category", "") or "").strip() or None,
            phase=str(getattr(exc, "phase", "") or "").strip() or None,
            fallback_used=True,
        )

    def current_runtime_metadata(self) -> dict[str, Any]:
        session = self._session
        metadata: dict[str, Any] = {
            "sandbox_active": session is not None,
            "workspace_reconfigured": self._last_workspace_reconfigured,
            "runtime_degraded": bool(self._runtime_degraded),
            "runtime_fallback_used": bool(self._runtime_fallback_used),
        }
        sandbox_id = (
            session.sandbox_id if session is not None else self._persisted_sandbox_id
        )
        workspace_path = (
            session.workspace_path
            if session is not None
            else self._persisted_workspace_path
        )
        volume_name = (
            getattr(session, "volume_name", None) or self.volume_name
            if session is not None
            else self._persisted_volume_name or self.volume_name
        )
        if sandbox_id:
            metadata["sandbox_id"] = sandbox_id
        if workspace_path:
            metadata["workspace_path"] = workspace_path
        if volume_name:
            metadata["volume_name"] = volume_name
        if self._last_sandbox_transition:
            metadata["sandbox_transition"] = self._last_sandbox_transition
        if self._runtime_failure_category:
            metadata["runtime_failure_category"] = self._runtime_failure_category
        if self._runtime_failure_phase:
            metadata["runtime_failure_phase"] = self._runtime_failure_phase
        return metadata

    def build_delegate_child(self, *, remaining_llm_budget: int) -> DaytonaInterpreter:
        return build_delegate_child(self, remaining_llm_budget=remaining_llm_budget)

    def _reject_unsupported_recursive_callbacks(self, code: str) -> None:
        reject_unsupported_recursive_callbacks(
            self,
            code,
            callbacks=_UNSUPPORTED_RECURSIVE_SANDBOX_CALLBACKS,
        )

    def _bridge_tools(self) -> dict[str, Callable[..., Any]]:
        return bridge_tools(self, native_tool_names=_DAYTONA_SANDBOX_NATIVE_TOOL_NAMES)

    def _requires_bridge(self, code: str, tools: dict[str, Callable[..., Any]]) -> bool:
        return requires_bridge(self, code, tools)

    def _invoke_tool(
        self,
        name: str,
        args: list[Any],
        kwargs: dict[str, Any],
    ) -> Any:
        return invoke_tool(self, name, args, kwargs)


__all__ = [
    "DaytonaInterpreter",
    "build_delegate_child",
    "bridge_tools",
    "invoke_tool",
    "reject_unsupported_recursive_callbacks",
    "requires_bridge",
]
