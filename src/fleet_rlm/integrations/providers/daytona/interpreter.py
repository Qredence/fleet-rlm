"""Daytona-backed interpreter compatible with the shared ReAct + RLM runtime."""

from __future__ import annotations

import inspect
from typing import Any, Callable

import dspy
from dspy.primitives import FinalOutput

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
from fleet_rlm.runtime.execution.profiles import ExecutionProfile
from fleet_rlm.runtime.tools.llm_tools import LLMQueryMixin

from . import interpreter_execution as _execution
from .bridge import DaytonaBridgeExecution, DaytonaToolBridge
from .interpreter_assets import (
    _DAYTONA_SANDBOX_NATIVE_TOOL_NAMES,
    _FINAL_OUTPUT_MARKER,
    _UNSUPPORTED_RECURSIVE_SANDBOX_CALLBACKS,
    _base_setup_code,
    _typed_submit_code,
)
from .runtime import (
    DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH,
    DaytonaSandboxRuntime,
    DaytonaSandboxSession,
)
from .runtime_helpers import _run_async_compat
from .state import dedupe_paths, normalized_context_sources


class DaytonaInterpreter(LLMQueryMixin):
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
        await session.astart_driver(timeout=float(self.execute_timeout))
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

    async def _aensure_session_impl(self) -> DaytonaSandboxSession:
        self._ensure_runtime_available()
        source_key = (
            self.repo_url,
            self.repo_ref,
            tuple(self.context_paths),
            self.volume_name,
        )
        should_report_recreated = False
        if self._session is not None:
            if not self._session_matches_current_async_owner(self._session):
                await self._adetach_session(delete=False)
                # Clear the persisted context_id so the resumed session
                # creates a fresh interpreter context on the current loop
                # instead of reusing one bound to the old loop.
                # (Fixes "Future attached to a different loop" errors when
                # child dspy.RLM modules run from worker threads.)
                self._persisted_context_id = None
            elif self._session_needs_recreation(desired_volume=self.volume_name):
                should_report_recreated = True
                await self._adetach_session(delete=True)
            elif self._session_source_key == source_key:
                self._last_sandbox_transition = "reused"
                self._last_workspace_reconfigured = False
                self._persist_session_snapshot()
                return self._session
            else:
                try:
                    self._session = await self._areconcile_workspace_session(
                        self._session
                    )
                except Exception as exc:
                    self._mark_runtime_degradation_from_exception(exc)
                    should_report_recreated = True
                    await self._adetach_session(delete=True)
                else:
                    self._session_source_key = source_key
                    await self._areset_execution_state()
                    self._persist_session_snapshot()
                    self._last_sandbox_transition = "reused"
                    self._last_workspace_reconfigured = True
                    return self._session

        if (
            self._persisted_sandbox_id is not None
            and self._persisted_volume_name != self.volume_name
        ):
            should_report_recreated = True
            self._clear_persisted_session()

        if self._persisted_sandbox_id and self._persisted_workspace_path:
            try:
                persisted_source_key = self._session_source_key
                self._session = await self._aresume_workspace_session(
                    sandbox_id=self._persisted_sandbox_id,
                    repo_url=self.repo_url,
                    ref=self.repo_ref,
                    workspace_path=self._persisted_workspace_path,
                    context_sources=self._persisted_context_sources,
                    context_id=self._persisted_context_id,
                )
                if (
                    persisted_source_key is not None
                    and persisted_source_key != source_key
                ):
                    self._session = await self._areconcile_workspace_session(
                        self._session
                    )
                    self._last_workspace_reconfigured = True
                else:
                    self._last_workspace_reconfigured = False
                self._session_source_key = source_key
                await self._areset_execution_state()
                self._persist_session_snapshot()
                self._last_sandbox_transition = "resumed"
                return self._session
            except Exception as exc:
                self._mark_runtime_degradation_from_exception(exc)
                self._clear_persisted_session()
                should_report_recreated = True

        self._session = await self.runtime.acreate_workspace_session(
            repo_url=self.repo_url,
            ref=self.repo_ref,
            context_paths=list(self.context_paths),
            volume_name=self.volume_name,
            spec=self.sandbox_spec,
        )
        self._session_source_key = source_key
        await self._areset_execution_state()
        self._persist_session_snapshot()
        self._last_sandbox_transition = (
            "recreated" if should_report_recreated else "created"
        )
        self._last_workspace_reconfigured = False
        return self._session

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
        return _execution.build_delegate_child(
            self, remaining_llm_budget=remaining_llm_budget
        )

    def execute(
        self,
        code: str,
        variables: dict[str, Any] | None = None,
        *,
        execution_profile: ExecutionProfile | None = None,
    ) -> str | FinalOutput:
        return _execution.execute(
            self,
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
        return await _execution.aexecute(
            self,
            code,
            variables,
            execution_profile=execution_profile,
        )

    def _safe_variables(self, variables: dict[str, Any] | None) -> dict[str, Any]:
        return _execution.safe_variables(self, variables)

    def _submit_signature(self) -> tuple[tuple[str, str], ...] | None:
        return _execution.submit_signature(self)

    async def _aensure_setup(self, session: DaytonaSandboxSession) -> Any:
        return await _execution.aensure_setup(
            self,
            session,
            base_setup_code=_base_setup_code,
            typed_submit_code=_typed_submit_code,
            submit_signature_fn=self._submit_signature,
        )

    async def _aensure_bridge(
        self,
        *,
        session: DaytonaSandboxSession,
        context: Any,
        tools: dict[str, Callable[..., Any]],
    ) -> DaytonaToolBridge:
        return await _execution.aensure_bridge(
            self,
            session=session,
            context=context,
            tools=tools,
            bridge_cls=DaytonaToolBridge,
        )

    async def _aexecute_in_session(
        self,
        *,
        session: DaytonaSandboxSession,
        code: str,
        variables: dict[str, Any],
    ) -> _execution._DaytonaExecutionResponse:
        return await _execution.aexecute_in_session(
            self,
            session=session,
            code=code,
            variables=variables,
            bridge_tools_fn=self._bridge_tools,
            reject_unsupported_recursive_callbacks_fn=self._reject_unsupported_recursive_callbacks,
            requires_bridge_fn=self._requires_bridge,
            aensure_bridge_fn=self._aensure_bridge,
            aexecute_direct_fn=self._aexecute_direct,
            response_from_execution_fn=self._response_from_execution,
        )

    async def _aexecute_direct(
        self,
        *,
        session: DaytonaSandboxSession,
        context: Any,
        code: str,
    ) -> DaytonaBridgeExecution:
        return await _execution.aexecute_direct(
            self,
            session=session,
            context=context,
            code=code,
        )

    def _reject_unsupported_recursive_callbacks(self, code: str) -> None:
        _execution.reject_unsupported_recursive_callbacks(
            self,
            code,
            callbacks=_UNSUPPORTED_RECURSIVE_SANDBOX_CALLBACKS,
        )

    def _bridge_tools(self) -> dict[str, Callable[..., Any]]:
        return _execution.bridge_tools(
            self,
            native_tool_names=_DAYTONA_SANDBOX_NATIVE_TOOL_NAMES,
        )

    def _requires_bridge(self, code: str, tools: dict[str, Callable[..., Any]]) -> bool:
        return _execution.requires_bridge(self, code, tools)

    def _invoke_tool(
        self,
        name: str,
        args: list[Any],
        kwargs: dict[str, Any],
    ) -> Any:
        return _execution.invoke_tool(self, name, args, kwargs)

    def _response_from_execution(
        self,
        execution: DaytonaBridgeExecution,
    ) -> _execution._DaytonaExecutionResponse:
        return _execution.response_from_execution(
            self,
            execution,
            extract_final_artifact_fn=self._extract_final_artifact,
        )

    def _extract_final_artifact(self, stdout: str) -> dict[str, Any] | None:
        return _execution.extract_final_artifact(stdout, marker=_FINAL_OUTPUT_MARKER)

    def _finalize_execution_result(
        self,
        *,
        response: _execution._DaytonaExecutionResponse,
        started_at: float,
        execution_profile: str,
        code_hash: str,
        code_preview: str,
    ) -> str | FinalOutput:
        return _execution.finalize_execution_result(
            self,
            response=response,
            started_at=started_at,
            execution_profile=execution_profile,
            code_hash=code_hash,
            code_preview=code_preview,
        )

    def _inject_variables(self, code: str, variables: dict[str, Any]) -> str:
        return _execution.inject_variables(self, code, variables)

    def _literal(self, value: Any) -> str:
        return _execution.literal(self, value)


__all__ = ["DaytonaInterpreter"]
