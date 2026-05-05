"""Daytona-backed interpreter compatible with the shared ReAct + RLM runtime."""

from __future__ import annotations

from typing import Any, Callable

import dspy

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
from fleet_rlm.utils.paths import dedupe_paths

from .bridge import DaytonaToolBridge
from .bridge_callbacks import (
    bridge_tools,
    invoke_tool,
    reject_unsupported_recursive_callbacks,
    requires_bridge,
)
from .child_isolation import (
    ChildForkFallback,
    ChildIsolationMode,
    RLMChildIsolationError,
    normalize_child_fork_fallback,
    normalize_child_isolation_mode,
)
from .interpreter_assets import (
    _DAYTONA_SANDBOX_NATIVE_TOOL_NAMES,
    _UNSUPPORTED_RECURSIVE_SANDBOX_CALLBACKS,
)
from .interpreter_child import DaytonaInterpreterChildMixin, build_delegate_child
from .interpreter_execution import DaytonaInterpreterExecutionMixin
from .interpreter_session import DaytonaInterpreterSessionMixin
from .interpreter_state import DaytonaInterpreterStateMixin
from .runtime import (
    DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH,
    DaytonaSandboxRuntime,
)
from .session_runtime import DaytonaSandboxSession


class DaytonaInterpreter(
    DaytonaInterpreterExecutionMixin,
    DaytonaInterpreterSessionMixin,
    DaytonaInterpreterStateMixin,
    DaytonaInterpreterChildMixin,
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
