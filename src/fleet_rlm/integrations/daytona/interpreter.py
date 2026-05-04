"""Daytona-backed interpreter compatible with the shared ReAct + RLM runtime."""

from __future__ import annotations

import inspect
import logging
import time
from dataclasses import replace
from typing import Any, Callable, Protocol, cast

import dspy
from dspy.primitives import CodeInterpreterError, FinalOutput

from fleet_rlm.runtime.execution.interpreter_protocol import (
    RLMInterpreterProtocol,
    StatefulWorkspaceInterpreterProtocol,
)
from fleet_rlm.runtime.execution.interpreter_support import (
    SupportsExecutionEventCallback,
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
)
from fleet_rlm.runtime.execution.interpreter_support import (
    async_enter as _async_enter_impl,
)
from fleet_rlm.runtime.execution.interpreter_support import (
    async_exit as _async_exit_impl,
)
from fleet_rlm.runtime.execution.interpreter_support import (
    sync_enter as _sync_enter_impl,
)
from fleet_rlm.runtime.execution.interpreter_support import (
    sync_exit as _sync_exit_impl,
)
from fleet_rlm.runtime.execution.llm_query import LLMQueryMixin
from fleet_rlm.runtime.execution.profiles import ExecutionProfile
from fleet_rlm.utils.paths import dedupe_paths

from .async_compat import _run_async_compat
from .bridge import DaytonaBridgeExecution, DaytonaToolBridge
from .bridge_callbacks import (
    bridge_tools,
    invoke_tool,
    reject_unsupported_recursive_callbacks,
    requires_bridge,
)
from .child_isolation import (
    _UNSET,
    ChildForkFallback,
    ChildIsolationMode,
    RLMChildIsolationError,
    normalize_child_fork_fallback,
    normalize_child_isolation_mode,
)
from .child_isolation import (
    build_delegate_child as _build_delegate_child_policy,
)
from .interpreter_assets import (
    _DAYTONA_SANDBOX_NATIVE_TOOL_NAMES,
    _FINAL_OUTPUT_MARKER,
    _UNSUPPORTED_RECURSIVE_SANDBOX_CALLBACKS,
)
from .interpreter_execution import (
    DaytonaExecutionResponse as _DaytonaExecutionResponse,
)
from .interpreter_execution import (
    ExecutionCallbacks as _ExecutionCallbacks,
)
from .interpreter_execution import (
    aensure_bridge as _aensure_bridge,
)
from .interpreter_execution import (
    aensure_setup as _aensure_setup,
)
from .interpreter_execution import (
    aexecute_direct as _aexecute_direct,
)
from .interpreter_execution import (
    aexecute_in_session as _aexecute_in_session,
)
from .interpreter_execution import (
    arun_prepared_execution as _arun_prepared_execution,
)
from .interpreter_execution import (
    extract_final_artifact as _extract_final_artifact,
)
from .interpreter_execution import (
    finalize_execution_result as _finalize_execution_result,
)
from .interpreter_execution import (
    inject_variables as _inject_variables,
)
from .interpreter_execution import (
    literal as _literal,
)
from .interpreter_execution import (
    prepare_execution_code as _prepare_execution_code,
)
from .interpreter_execution import (
    resolve_execution_callbacks as _resolve_execution_callbacks,
)
from .interpreter_execution import (
    response_from_execution as _response_from_execution,
)
from .interpreter_execution import (
    safe_variables as _safe_variables,
)
from .interpreter_execution import (
    sanitize_execution_code as _sanitize_execution_code,
)
from .interpreter_execution import (
    structured_execution_error as _structured_execution_error,
)
from .interpreter_execution import (
    submit_signature as _submit_signature,
)
from .runtime import (
    DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH,
    DaytonaSandboxRuntime,
)
from .session_runtime import DaytonaSandboxSession
from .types import SandboxSpec, normalized_context_sources


class _DaytonaInterpreterLike(
    RLMInterpreterProtocol,
    SupportsExecutionEventCallback,
    Protocol,
):
    """Protocol describing the attributes accessed by the execution mixin."""

    timeout: int
    execute_timeout: int | None
    volume_name: str | None
    volume_subpath: str | None
    repo_url: str | None
    repo_ref: str | None
    context_paths: list[str]
    sandbox_spec: SandboxSpec | None
    sandbox_labels: dict[str, str]
    sub_lm: Any
    rlm_max_iterations: int
    child_isolation_mode: ChildIsolationMode
    child_fork_fallback: ChildForkFallback
    child_isolation_metadata: dict[str, Any] | None
    llm_call_timeout: float
    delete_session_on_shutdown: bool
    delete_context_on_shutdown: bool
    default_execution_profile: ExecutionProfile
    async_execute: bool
    output_fields: list[dict[str, Any]] | None
    volume_mount_path: str
    _session: DaytonaSandboxSession | None
    _persisted_sandbox_id: str | None
    _persisted_workspace_path: str | None
    _sub_rlm_depth: int
    _sub_rlm_max_depth: int
    _bridge_context_id: str | None
    _bridge: DaytonaToolBridge | None
    _bridge_sandbox_id: str | None
    _bridge_tools: Callable[..., Any]
    _reject_unsupported_recursive_callbacks: Callable[..., None]
    _requires_bridge: Callable[..., bool]
    _aensure_session_impl: Callable[..., Any]
    _aclose_bridge: Callable[..., Any]
    _check_and_increment_llm_calls: Callable[..., bool]
    _setup_context_id: str | None
    _setup_workspace_path: str | None
    _submit_signature_key: tuple[tuple[str, str], ...] | None
    extract_final_artifact: Callable[..., dict[str, Any] | None]

    def inject_variables(self, code: str, variables: dict[str, Any]) -> str:
        raise NotImplementedError


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
        volume_name: str | None | object = _UNSET,
        volume_subpath: str | None | object = _UNSET,
    ) -> Any:
        child_volume_name = (
            self.volume_name if volume_name is _UNSET else cast(str | None, volume_name)
        )
        child_volume_subpath = (
            self.volume_subpath
            if volume_subpath is _UNSET
            else cast(str | None, volume_subpath)
        )
        return cast(Any, self).__class__(
            runtime=runtime,
            owns_runtime=owns_runtime,
            timeout=self.timeout,
            execute_timeout=self.execute_timeout,
            volume_name=child_volume_name,
            volume_subpath=child_volume_subpath,
            repo_url=self.repo_url,
            repo_ref=self.repo_ref,
            context_paths=list(self.context_paths),
            sandbox_spec=getattr(self, "sandbox_spec", None),
            sandbox_labels=self.sandbox_labels,
            delete_session_on_shutdown=delete_session_on_shutdown,
            delete_context_on_shutdown=delete_context_on_shutdown,
            sub_lm=self.sub_lm,
            max_llm_calls=remaining_llm_budget,
            max_recursion_depth=self._sub_rlm_max_depth,
            rlm_max_iterations=self.rlm_max_iterations,
            child_isolation_mode=self.child_isolation_mode,
            child_fork_fallback=self.child_fork_fallback,
            delegate_max_calls_per_turn=getattr(self, "delegate_max_calls_per_turn", 8),
            delegate_result_truncation_chars=getattr(
                self, "delegate_result_truncation_chars", 8000
            ),
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
        remaining_budget = getattr(self, "_remaining_llm_budget", None)
        if callable(remaining_budget):
            setattr(child, "_remaining_llm_budget", remaining_budget)
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
        envs: dict[str, str] | None = None,
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
                envs=envs,
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
        return _safe_variables(variables)

    def submit_signature(self) -> tuple[tuple[str, str], ...] | None:
        return _submit_signature(self.output_fields)

    async def aensure_setup(
        self,
        session: DaytonaSandboxSession,
        *,
        submit_signature_fn: Callable[[], tuple[tuple[str, str], ...] | None]
        | None = None,
    ) -> Any:
        submit_signature_fn = submit_signature_fn or self.submit_signature
        return await _aensure_setup(
            self,
            session,
            submit_signature_fn=submit_signature_fn,
        )

    async def aensure_bridge(
        self,
        *,
        session: DaytonaSandboxSession,
        context: Any,
        tools: dict[str, Callable[..., Any]],
        bridge_cls: type[DaytonaToolBridge] | None = None,
    ) -> DaytonaToolBridge:
        return await _aensure_bridge(
            self,
            session=session,
            context=context,
            tools=tools,
            bridge_cls=bridge_cls or DaytonaToolBridge,
        )

    async def aexecute_in_session(
        self,
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
            [DaytonaBridgeExecution], _DaytonaExecutionResponse
        ]
        | None = None,
    ) -> _DaytonaExecutionResponse:
        return await _aexecute_in_session(
            self,
            session=session,
            code=code,
            variables=variables,
            envs=envs,
            bridge_tools_fn=bridge_tools_fn,
            reject_unsupported_recursive_callbacks_fn=reject_unsupported_recursive_callbacks_fn,
            requires_bridge_fn=requires_bridge_fn,
            aensure_bridge_fn=aensure_bridge_fn,
            aexecute_direct_fn=aexecute_direct_fn,
            response_from_execution_fn=response_from_execution_fn,
        )

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
        return _resolve_execution_callbacks(
            self,
            bridge_tools_fn=bridge_tools_fn,
            reject_unsupported_recursive_callbacks_fn=reject_unsupported_recursive_callbacks_fn,
            requires_bridge_fn=requires_bridge_fn,
            aensure_bridge_fn=aensure_bridge_fn,
            aexecute_direct_fn=aexecute_direct_fn,
            response_from_execution_fn=response_from_execution_fn,
        )

    def _prepare_execution_code(
        self,
        *,
        code: str,
        variables: dict[str, Any],
        reject_recursive_callbacks: Callable[[str], None],
    ) -> str:
        return _prepare_execution_code(
            self,
            code=code,
            variables=variables,
            reject_recursive_callbacks=reject_recursive_callbacks,
        )

    @staticmethod
    def _sanitize_execution_code(code: str) -> str:
        return _sanitize_execution_code(code)

    @staticmethod
    def _structured_execution_error(
        *, reason: str, error: str
    ) -> _DaytonaExecutionResponse:
        return _structured_execution_error(reason=reason, error=error)

    async def _arun_prepared_execution(
        self,
        *,
        session: DaytonaSandboxSession,
        context: Any,
        code: str,
        callbacks: _ExecutionCallbacks,
        envs: dict[str, str] | None = None,
    ) -> DaytonaBridgeExecution:
        return await _arun_prepared_execution(
            self,
            session=session,
            context=context,
            code=code,
            callbacks=callbacks,
            envs=envs,
        )

    async def aexecute_direct(
        self,
        *,
        session: DaytonaSandboxSession,
        context: Any,
        code: str,
        envs: dict[str, str] | None = None,
    ) -> DaytonaBridgeExecution:
        return await _aexecute_direct(
            self,
            session=session,
            context=context,
            code=code,
            envs=envs,
        )

    def response_from_execution(
        self,
        execution: DaytonaBridgeExecution,
        *,
        extract_final_artifact_fn: Callable[[str], dict[str, Any] | None] | None = None,
    ) -> _DaytonaExecutionResponse:
        return _response_from_execution(
            self,
            execution,
            extract_final_artifact_fn=extract_final_artifact_fn,
        )

    @staticmethod
    def extract_final_artifact(
        stdout: str,
        *,
        marker: str = _FINAL_OUTPUT_MARKER,
    ) -> dict[str, Any] | None:
        return _extract_final_artifact(stdout, marker=marker)

    def finalize_execution_result(
        self,
        *,
        response: _DaytonaExecutionResponse,
        started_at: float,
        execution_profile: str,
        code_hash: str,
        code_preview: str,
    ) -> str | FinalOutput:
        return _finalize_execution_result(
            self,
            response=response,
            started_at=started_at,
            execution_profile=execution_profile,
            code_hash=code_hash,
            code_preview=code_preview,
        )

    def inject_variables(self, code: str, variables: dict[str, Any]) -> str:
        return _inject_variables(self, code, variables)

    def literal(self, value: Any) -> str:
        return _literal(value)


def build_delegate_child(
    interpreter: Any,
    *,
    remaining_llm_budget: int,
) -> Any:
    """Build a recursive RLM child interpreter using the isolation policy."""
    fn = getattr(interpreter, "build_delegate_child", None)
    owner_impl = getattr(type(interpreter), "build_delegate_child", None)
    owner_func = getattr(owner_impl, "__func__", owner_impl)
    daytona_func = getattr(DaytonaInterpreter, "build_delegate_child", None)
    if callable(fn) and owner_func is not None and owner_func is not daytona_func:
        return fn(remaining_llm_budget=remaining_llm_budget)
    return _build_delegate_child_policy(
        interpreter,
        remaining_llm_budget=remaining_llm_budget,
    )


class DaytonaInterpreter(
    _DaytonaInterpreterExecutionMixin,
    LLMQueryMixin,
    StatefulWorkspaceInterpreterProtocol,
):
    """Stateful Daytona interpreter that plugs into canonical ``dspy.RLM`` flows."""

    # Host-mediated evidence bridge references (v0.5.1+). Populated by the
    # WebSocket stream layer once identity is resolved; read by
    # ``integrations.daytona.evidence_bridge``. Declared at class level so
    # type-checkers can see them.
    _host_repository: Any | None = None
    _host_identity: Any | None = None
    _host_run_id: Any | None = None

    def __init__(
        self,
        *,
        runtime: DaytonaSandboxRuntime | None = None,
        owns_runtime: bool = False,
        timeout: int = 900,
        execute_timeout: int | None = None,
        volume_name: str | None = None,
        volume_subpath: str | None = None,
        repo_url: str | None = None,
        repo_ref: str | None = None,
        context_paths: list[str] | None = None,
        sandbox_spec: Any | None = None,
        sandbox_labels: dict[str, str] | None = None,
        delete_session_on_shutdown: bool = True,
        delete_context_on_shutdown: bool = False,
        sub_lm: dspy.LM | None = None,
        max_llm_calls: int = 50,
        max_recursion_depth: int = 2,
        rlm_max_iterations: int = 30,
        child_isolation_mode: ChildIsolationMode | str = "auto",
        child_fork_fallback: ChildForkFallback | str = "clean",
        delegate_max_calls_per_turn: int = 8,
        delegate_result_truncation_chars: int = 8000,
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
        self.volume_subpath = str(volume_subpath or "").strip() or None
        self.volume_mount_path = str(DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH)
        self.repo_url = repo_url
        self.repo_ref = repo_ref
        self.context_paths = dedupe_paths(list(context_paths or []))
        self.sandbox_spec = sandbox_spec  # SandboxSpec with optional Image builder
        self.sandbox_labels = dict(sandbox_labels or {})
        self.delete_session_on_shutdown = delete_session_on_shutdown
        self.delete_context_on_shutdown = delete_context_on_shutdown
        self.default_execution_profile = default_execution_profile
        self.async_execute = async_execute
        self.rlm_max_iterations = max(1, int(rlm_max_iterations))
        self.child_isolation_mode = normalize_child_isolation_mode(child_isolation_mode)
        self.child_fork_fallback = normalize_child_fork_fallback(child_fork_fallback)
        self.delegate_max_calls_per_turn = max(1, int(delegate_max_calls_per_turn))
        self.delegate_result_truncation_chars = max(
            0, int(delegate_result_truncation_chars)
        )
        self.child_isolation_metadata: dict[str, Any] | None = None

        initialize_llm_query_state(
            self,
            sub_lm=sub_lm,
            max_llm_calls=max_llm_calls,
            llm_call_timeout=llm_call_timeout,
        )
        initialize_sub_rlm_state(self, max_depth=max_recursion_depth)
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
        sandbox_labels: dict[str, str] | None = None,
        force_new_session: bool = False,
    ) -> None:
        (
            normalized_repo_url,
            normalized_repo_ref,
            normalized_context_paths,
            normalized_volume,
            normalized_sandbox_labels,
            source_key,
        ) = self._normalized_workspace_config(
            repo_url=repo_url,
            repo_ref=repo_ref,
            context_paths=context_paths,
            volume_name=volume_name,
            sandbox_labels=sandbox_labels,
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
            sandbox_labels=normalized_sandbox_labels,
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
        sandbox_labels: dict[str, str] | None = None,
        force_new_session: bool = False,
    ) -> None:
        (
            normalized_repo_url,
            normalized_repo_ref,
            normalized_context_paths,
            normalized_volume,
            normalized_sandbox_labels,
            source_key,
        ) = self._normalized_workspace_config(
            repo_url=repo_url,
            repo_ref=repo_ref,
            context_paths=context_paths,
            volume_name=volume_name,
            sandbox_labels=sandbox_labels,
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
            sandbox_labels=normalized_sandbox_labels,
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
        sandbox_labels: dict[str, str] | None,
    ) -> tuple[
        str | None,
        str | None,
        list[str],
        str | None,
        dict[str, str],
        tuple[str | None, str | None, tuple[str, ...], str | None],
    ]:
        normalized_repo_url = str(repo_url or "").strip() or None
        normalized_repo_ref = str(repo_ref or "").strip() or None
        normalized_context_paths = dedupe_paths(list(context_paths or []))
        normalized_volume = str(volume_name or "").strip() or None
        normalized_sandbox_labels = {
            str(key): str(value)
            for key, value in (sandbox_labels or {}).items()
            if str(key).strip() and str(value).strip()
        }
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
            normalized_sandbox_labels,
            source_key,
        )

    def _apply_workspace_config(
        self,
        *,
        repo_url: str | None,
        repo_ref: str | None,
        context_paths: list[str],
        volume_name: str | None,
        sandbox_labels: dict[str, str],
    ) -> None:
        self.repo_url = repo_url
        self.repo_ref = repo_ref
        self.context_paths = context_paths
        self.volume_name = volume_name
        if sandbox_labels:
            self.sandbox_labels = dict(sandbox_labels)

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
        self.volume_subpath = (
            str(daytona_state.get("volume_subpath", "") or "").strip()
            or self.volume_subpath
        )
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
                "volume_subpath": self.volume_subpath,
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
        if self.child_isolation_metadata and session.sandbox_id:
            self.child_isolation_metadata.setdefault(
                "child_sandbox_id", session.sandbox_id
            )
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

    def _effective_sandbox_spec(self) -> SandboxSpec:
        """Return the sandbox spec with current volume and owner labels applied."""
        labels = dict(getattr(self.sandbox_spec, "labels", None) or {})
        labels.update(self.sandbox_labels)
        if isinstance(self.sandbox_spec, SandboxSpec):
            return replace(
                self.sandbox_spec,
                volume_name=self.volume_name or self.sandbox_spec.volume_name,
                volume_subpath=(
                    self.volume_subpath or self.sandbox_spec.volume_subpath
                ),
                labels=labels or None,
            )
        build_sandbox_spec = getattr(self.runtime, "build_sandbox_spec", None)
        if callable(build_sandbox_spec):
            return build_sandbox_spec(
                volume_name=self.volume_name,
                volume_subpath=self.volume_subpath,
                labels=labels or None,
            )
        return SandboxSpec(
            volume_name=self.volume_name,
            volume_mount_path=str(DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH),
            volume_subpath=self.volume_subpath,
            labels=labels or None,
        )

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
            spec=self._effective_sandbox_spec(),
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
        if not self._runtime_closed:
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
        if self.volume_subpath:
            metadata["volume_subpath"] = self.volume_subpath
        if self.child_isolation_metadata:
            metadata["child_isolation"] = dict(self.child_isolation_metadata)
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
    "RLMChildIsolationError",
    "build_delegate_child",
    "bridge_tools",
    "invoke_tool",
    "reject_unsupported_recursive_callbacks",
    "requires_bridge",
]
