"""Daytona-backed interpreter compatible with the shared ReAct + RLM runtime."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

from fleet_rlm.integrations.observability.mlflow_context import (
    mlflow_child_span,
    set_mlflow_span_outputs,
)
from fleet_rlm.runtime.execution.interpreter_protocol import (
    ExecutionProfile,
    StatefulWorkspaceInterpreterProtocol,
)
from fleet_rlm.runtime.execution.interpreter_support import (
    async_enter as _async_enter_impl,
)
from fleet_rlm.runtime.execution.interpreter_support import (
    async_exit as _async_exit_impl,
)
from fleet_rlm.runtime.execution.interpreter_support import (
    execution_profile_context,
    get_registered_tools,
    initialize_llm_query_state,
    initialize_sub_rlm_state,
    initialize_tool_runtime_state,
    set_registered_tools,
)
from fleet_rlm.runtime.execution.interpreter_support import (
    sync_enter as _sync_enter_impl,
)
from fleet_rlm.runtime.execution.interpreter_support import (
    sync_exit as _sync_exit_impl,
)
from fleet_rlm.runtime.execution.llm_query import LLMQueryMixin

from .bridge import (
    DaytonaToolBridge,
    bridge_tools,
    invoke_tool,
    reject_unsupported_recursive_callbacks,
    requires_bridge,
)
from .isolation import (
    ChildDelegation,
    ChildForkFallback,
    ChildIsolationMode,
    RLMChildIsolationError,
    build_delegate_child,
    normalize_child_fork_fallback,
    normalize_child_isolation_mode,
)
from .log_stream import LogStreamParser, SandboxEvent
from .models import (
    ReconfigureOutcome,
    WorkspaceConfig,
)
from .runtime import (
    DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH,
    DaytonaSandboxRuntime,
    get_sandbox_id_from_interpreter,
)
from .sandbox_executor import SandboxExecutor
from .session_runtime import DaytonaSandboxSession
from .workspace_manager import _UNSET, WorkspaceManager


class DaytonaInterpreter(
    LLMQueryMixin,
    StatefulWorkspaceInterpreterProtocol,
):
    """Stateful Daytona interpreter that plugs into canonical ``dspy.RLM`` flows."""

    # Host-mediated evidence bridge references (v0.5.1+). Populated by the
    # WebSocket stream layer once identity is resolved; read by
    # ``integrations.daytona.isolation``. Declared at class level so
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
        sub_lm: Any | None = None,
        max_llm_calls: int = 50,
        max_recursion_depth: int = 2,
        rlm_max_iterations: int = 30,
        child_isolation_mode: ChildIsolationMode | str = "auto",
        child_fork_fallback: ChildForkFallback | str = "clean",
        delegate_max_calls_per_turn: int = 8,
        delegate_result_truncation_chars: int = 8000,
        delegate_max_iterations: int = 8,
        delegate_execution_timeout: int | None = 300,
        broker_health_timeout: float = 20.0,
        broker_tool_call_timeout: float = 180.0,
        broker_start_retries: int = 1,
        delegate_adapter: str = "json",
        llm_call_timeout: int = 60,
        default_execution_profile: ExecutionProfile = ExecutionProfile.RLM_DELEGATE,
        async_execute: bool = True,
    ) -> None:
        provided_runtime = runtime
        self.timeout = timeout
        self.execute_timeout = execute_timeout or timeout
        self.volume_mount_path = str(DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH)
        self._executor: SandboxExecutor | None = None
        self._workspace_config = WorkspaceConfig.from_kwargs(
            repo_url=repo_url,
            repo_ref=repo_ref,
            context_paths=context_paths,
            volume_name=volume_name,
            sandbox_labels=sandbox_labels,
        )
        self.default_execution_profile = default_execution_profile
        self.async_execute = async_execute
        self.rlm_max_iterations = max(1, int(rlm_max_iterations))
        self.child_isolation_mode = normalize_child_isolation_mode(child_isolation_mode)
        self.child_fork_fallback = normalize_child_fork_fallback(child_fork_fallback)
        self.delegate_max_iterations = max(1, int(delegate_max_iterations))
        self.delegate_max_calls_per_turn = max(1, int(delegate_max_calls_per_turn))
        self.delegate_result_truncation_chars = max(0, int(delegate_result_truncation_chars))
        resolved_delegate_timeout = (
            self.execute_timeout if delegate_execution_timeout is None else delegate_execution_timeout
        )
        self.delegate_execution_timeout = max(1, min(int(resolved_delegate_timeout), int(self.execute_timeout)))
        self.broker_health_timeout = max(1.0, float(broker_health_timeout))
        self.broker_tool_call_timeout = max(1.0, float(broker_tool_call_timeout))
        self.broker_start_retries = max(0, int(broker_start_retries))
        self.semantic_callbacks_enabled = True
        self.delegate_adapter = delegate_adapter
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

        runtime_instance = provided_runtime or DaytonaSandboxRuntime()

        async def _reset_executor() -> None:
            if self._executor is not None:
                await self._executor.areset()

        async def _close_executor() -> None:
            if self._executor is not None:
                await self._executor.aclose_bridge()

        self._workspace = WorkspaceManager(
            runtime=runtime_instance,
            owns_runtime=owns_runtime or provided_runtime is None,
            initial_config=self._workspace_config,
            volume_subpath=volume_subpath,
            sandbox_spec=sandbox_spec,
            sandbox_labels=dict(sandbox_labels or {}),
            timeout=self.timeout,
            execute_timeout=self.execute_timeout,
            delete_session_on_shutdown=delete_session_on_shutdown,
            delete_context_on_shutdown=delete_context_on_shutdown,
            execution_event_callback_ref=lambda: self.execution_event_callback,
            child_isolation_metadata_ref=lambda: self.child_isolation_metadata,
            reset_executor=_reset_executor,
            close_executor=_close_executor,
        )
        self._executor = SandboxExecutor(
            workspace=self._workspace,
            callback_owner=self,
            async_execute=self.async_execute,
            timeout=self.timeout,
            execute_timeout=self.execute_timeout,
            broker_health_timeout=self.broker_health_timeout,
            broker_tool_call_timeout=self.broker_tool_call_timeout,
            broker_start_retries=self.broker_start_retries,
            output_fields=self.output_fields,
            volume_mount_path=self.volume_mount_path,
            default_execution_profile=self.default_execution_profile,
        )
        self._executor.tools = get_registered_tools(self)
        self._delegation = ChildDelegation(
            workspace=self._workspace,
            executor=self._executor,
            callback_owner=self,
        )
        # Log stream parser for categorized sandbox event streaming
        self._log_stream_parser: LogStreamParser | None = None

    @property
    def runtime(self) -> DaytonaSandboxRuntime:
        return self._workspace.runtime

    @runtime.setter
    def runtime(self, value: DaytonaSandboxRuntime) -> None:
        self._workspace.runtime = value

    @property
    def repo_url(self) -> str | None:
        return self._workspace.repo_url

    @property
    def repo_ref(self) -> str | None:
        return self._workspace.repo_ref

    @property
    def context_paths(self) -> list[str]:
        return self._workspace.context_paths

    @property
    def volume_name(self) -> str | None:
        return self._workspace.volume_name

    @property
    def volume_subpath(self) -> str | None:
        return self._workspace.volume_subpath

    @property
    def sandbox_spec(self) -> Any | None:
        return self._workspace.sandbox_spec

    @property
    def sandbox_labels(self) -> dict[str, str]:
        return self._workspace.sandbox_labels

    @property
    def delete_session_on_shutdown(self) -> bool:
        return self._workspace.delete_session_on_shutdown

    @property
    def delete_context_on_shutdown(self) -> bool:
        return self._workspace.delete_context_on_shutdown

    @property
    def _session(self) -> DaytonaSandboxSession | None:
        return self._workspace.session

    @_session.setter
    def _session(self, value: DaytonaSandboxSession | None) -> None:
        self._workspace._session = value

    @property
    def _persisted_sandbox_id(self) -> str | None:
        return self._workspace._persisted_sandbox_id

    @_persisted_sandbox_id.setter
    def _persisted_sandbox_id(self, value: str | None) -> None:
        self._workspace._persisted_sandbox_id = value

    @property
    def _runtime_closed(self) -> bool:
        return self._workspace._runtime_closed

    @_runtime_closed.setter
    def _runtime_closed(self, value: bool) -> None:
        self._workspace._runtime_closed = value

    @property
    def default_execution_profile(self) -> ExecutionProfile:
        return self._default_execution_profile

    @default_execution_profile.setter
    def default_execution_profile(self, value: ExecutionProfile) -> None:
        self._default_execution_profile = value
        if self._executor is not None:
            self._executor.default_execution_profile = value

    @property
    def _last_sandbox_transition(self) -> ReconfigureOutcome | None:
        return self._workspace.last_sandbox_transition

    @property
    def _last_workspace_reconfigured(self) -> bool:
        return self._workspace.last_workspace_reconfigured

    @property
    def output_fields(self) -> list[dict[str, Any]] | None:
        return getattr(self, "_output_fields", None)

    @output_fields.setter
    def output_fields(self, value: list[dict[str, Any]] | None) -> None:
        self._output_fields = value
        if self._executor is not None:
            self._executor.output_fields = value

    @property
    def execution_event_callback(self) -> Callable[[dict[str, Any]], None] | None:
        return getattr(self, "_execution_event_callback", None)

    @execution_event_callback.setter
    def execution_event_callback(self, value: Callable[[dict[str, Any]], None] | None) -> None:
        self._execution_event_callback = value
        session = self._workspace.session if hasattr(self, "_workspace") else None
        if session is not None:
            session.execution_event_callback = value
        if self._executor is not None:
            self._executor.execution_event_callback = value

    @property
    def _active_executor(self) -> SandboxExecutor:
        if self._executor is None:
            raise RuntimeError("Daytona interpreter executor has not been initialized")
        return self._executor

    @property
    def log_stream_parser(self) -> LogStreamParser:
        """Lazily-created :class:`LogStreamParser` for sandbox log events.

        Feed raw sandbox log lines via :meth:`feed_sandbox_log`; parsed
        :class:`~fleet_rlm.integrations.daytona.log_stream.SandboxEvent`
        objects are relayed to the ``_turn_step_callback`` so the frontend
        sees real sandbox activity instead of a generic progress heartbeat.
        """
        parser = getattr(self, "_log_stream_parser", None)
        if parser is None:
            parser = LogStreamParser(interpreter=self)
            self._log_stream_parser = parser
        return parser

    def feed_sandbox_log(self, line: str) -> SandboxEvent | None:
        """Parse and relay one Daytona sandbox log line.

        Returns the parsed :class:`SandboxEvent` (or ``None`` for blank lines)
        so callers can inspect the categorization. Safe to call before the
        parser is started; events are buffered until :meth:`drain_sandbox_logs`.
        """
        return self.log_stream_parser.feed_line(line)

    def drain_sandbox_logs(self) -> list[SandboxEvent]:
        """Return and clear all buffered sandbox log events."""
        parser = getattr(self, "_log_stream_parser", None)
        if parser is None:
            return []
        return parser.drain()

    def __enter__(self) -> DaytonaInterpreter:
        return _sync_enter_impl(self)

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        _ = (exc_type, exc_val, exc_tb)
        return _sync_exit_impl(self)

    async def __aenter__(self) -> DaytonaInterpreter:
        with mlflow_child_span(
            "fleet_rlm.daytona_sandbox_setup",
            span_type="TOOL",
            attributes={
                "fleet_rlm.sandbox_origin": "custom_interpreter",
            },
        ) as setup_span:
            try:
                res = await _async_enter_impl(self)
                sandbox_id = get_sandbox_id_from_interpreter(self)
                if sandbox_id and setup_span is not None:
                    setup_span.set_attribute("fleet_rlm.sandbox_id", sandbox_id)
                set_mlflow_span_outputs(setup_span, {"status": "ok", "sandbox_id": str(sandbox_id or "")})
                return res
            except Exception as exc:
                set_mlflow_span_outputs(setup_span, {"status": "error", "error": str(exc)})
                raise

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> bool:
        sandbox_id = get_sandbox_id_from_interpreter(self)
        with mlflow_child_span(
            "fleet_rlm.daytona_sandbox_teardown",
            span_type="TOOL",
            attributes={
                "fleet_rlm.sandbox_origin": "custom_interpreter",
                "fleet_rlm.sandbox_id": str(sandbox_id or ""),
            },
        ) as teardown_span:
            try:
                res = await _async_exit_impl(self)
                set_mlflow_span_outputs(teardown_span, {"status": "ok", "sandbox_id": str(sandbox_id or "")})
                return res
            except Exception as exc:
                set_mlflow_span_outputs(
                    teardown_span, {"status": "error", "error": str(exc), "sandbox_id": str(sandbox_id or "")}
                )
                raise

    @property
    def tools(self) -> dict[str, Callable[..., Any]]:
        return get_registered_tools(self)

    @tools.setter
    def tools(self, value: dict[str, Callable[..., Any]]) -> None:
        set_registered_tools(self, value)
        if self._executor is not None:
            self._executor.tools = value

    def execution_profile(self, profile: ExecutionProfile):
        return execution_profile_context(self, profile)

    def start(self) -> None:
        self._workspace.start()

    async def astart(self) -> None:
        await self._workspace.astart()

    def shutdown(self) -> None:
        self._workspace.shutdown()

    async def ashutdown(self) -> None:
        await self._workspace.ashutdown()

    async def arelease_idle_session(self) -> None:
        """Release the current Daytona sandbox while preserving volume-backed state."""
        await self._workspace.arelease_idle_session()

    def execute(
        self,
        code: str,
        variables: dict[str, Any] | None = None,
        *,
        execution_profile: ExecutionProfile | None = None,
    ):
        return self._active_executor.execute(code, variables, execution_profile=execution_profile)

    async def aexecute(
        self,
        code: str,
        variables: dict[str, Any] | None = None,
        *,
        execution_profile: ExecutionProfile | None = None,
    ):
        return await self._active_executor.aexecute(code, variables, execution_profile=execution_profile)

    def safe_variables(self, variables: dict[str, Any] | None) -> dict[str, Any]:
        return self._active_executor.safe_variables(variables)

    def submit_signature(self) -> tuple[tuple[str, str], ...] | None:
        return self._active_executor.submit_signature()

    def configure_workspace(
        self,
        *,
        repo_url: str | None,
        repo_ref: str | None,
        context_paths: list[str] | None,
        volume_name: str | None,
        sandbox_labels: dict[str, str] | None = None,
        force_new_session: bool = False,
        snapshot: str | None | object = _UNSET,
    ) -> ReconfigureOutcome:
        return self._workspace.configure_workspace(
            repo_url=repo_url,
            repo_ref=repo_ref,
            context_paths=context_paths,
            volume_name=volume_name,
            sandbox_labels=sandbox_labels,
            force_new_session=force_new_session,
            snapshot=snapshot,
        )

    async def aconfigure_workspace(
        self,
        *,
        repo_url: str | None,
        repo_ref: str | None,
        context_paths: list[str] | None,
        volume_name: str | None,
        sandbox_labels: dict[str, str] | None = None,
        force_new_session: bool = False,
        snapshot: str | None | object = _UNSET,
    ) -> ReconfigureOutcome:
        return await self._workspace.aconfigure_workspace(
            repo_url=repo_url,
            repo_ref=repo_ref,
            context_paths=context_paths,
            volume_name=volume_name,
            sandbox_labels=sandbox_labels,
            force_new_session=force_new_session,
            snapshot=snapshot,
        )

    def export_session_state(self) -> dict[str, Any]:
        return self._workspace.export_session_state()

    def import_session_state(self, state: dict[str, Any]) -> None:
        self._workspace.import_session_state(state)

    async def aimport_session_state(self, state: dict[str, Any]) -> None:
        await self._workspace.aimport_session_state(state)

    def current_runtime_metadata(self) -> dict[str, Any]:
        return self._workspace.current_runtime_metadata()

    def reset_runtime_degradation_state(self) -> None:
        self._workspace.reset_runtime_degradation_state()

    def mark_runtime_degradation(
        self,
        *,
        category: str | None = None,
        phase: str | None = None,
        fallback_used: bool = False,
    ) -> None:
        self._workspace.mark_runtime_degradation(
            category=category,
            phase=phase,
            fallback_used=fallback_used,
        )

    def _ensure_session_sync(self) -> DaytonaSandboxSession:
        return self._workspace.ensure_session()

    async def aget_session(self) -> DaytonaSandboxSession:
        return await self._workspace.aget_session()

    def _ensure_runtime_available(self) -> None:
        self._workspace._ensure_runtime_available()

    def _parent_session_for_child(self) -> DaytonaSandboxSession | None:
        return self._delegation._parent_session_for_child()

    def _build_child_interpreter(self, **kwargs: Any) -> Any:
        return self._delegation._build_child_interpreter(**kwargs)

    def _attach_shared_parent_session(self, child: Any, **kwargs: Any) -> None:
        self._delegation._attach_shared_parent_session(child, **kwargs)

    def _propagate_parent_recursion_state(self, child: Any) -> None:
        self._delegation._propagate_parent_recursion_state(child)

    def build_delegate_child(self, *, remaining_llm_budget: int) -> Any:
        return self._delegation.build_delegate_child(remaining_llm_budget=remaining_llm_budget)

    async def areset_for_pool(self) -> None:
        """Lightweight reset for interpreter pool reuse.

        Preserves the sandbox VM and broker process (expensive to recreate).
        Clears per-request metadata and REPL state so the next request
        gets a fresh execution context.
        """
        self._host_repository = None
        self._host_identity = None
        self._host_run_id = None
        self.execution_event_callback = None
        self.child_isolation_metadata = None
        self.output_fields = None
        self._llm_call_count = 0
        self._log_stream_parser = None
        # Reset recursion depth so a pooled interpreter that hit depth N on a
        # prior request does not start the next request at depth N and
        # immediately fall back to llm_query. Keep max_depth (config, not
        # per-request state).
        initialize_sub_rlm_state(self, depth=0, max_depth=self._sub_rlm_max_depth)

        if self._executor is not None:
            await self._executor.asoft_reset()

    def _reject_unsupported_recursive_callbacks(self, code: str) -> None:
        self._active_executor._reject_unsupported_recursive_callbacks(code)

    def _bridge_tools(self) -> dict[str, Callable[..., Any]]:
        return self._active_executor._bridge_tools()

    def _requires_bridge(self, code: str, tools: dict[str, Callable[..., Any]]) -> bool:
        return self._active_executor._requires_bridge(code, tools)

    def _invoke_tool(
        self,
        name: str,
        args: list[Any],
        kwargs: dict[str, Any],
    ) -> Any:
        return self._active_executor._invoke_tool(name, args, kwargs)


__all__ = [
    "DaytonaInterpreter",
    "DaytonaToolBridge",
    "RLMChildIsolationError",
    "bridge_tools",
    "build_delegate_child",
    "invoke_tool",
    "reject_unsupported_recursive_callbacks",
    "requires_bridge",
]
