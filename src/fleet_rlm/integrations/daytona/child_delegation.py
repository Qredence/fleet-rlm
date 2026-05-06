"""Child interpreter construction for Daytona recursive RLM delegation."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, Protocol, cast

from fleet_rlm.runtime.execution.interpreter_protocol import ExecutionProfile
from fleet_rlm.runtime.execution.interpreter_support import initialize_sub_rlm_state

from .child_isolation import _UNSET
from .child_isolation import build_delegate_child as _build_delegate_child_policy
from .runtime import DaytonaSandboxRuntime
from .session_runtime import DaytonaSandboxSession


class ChildWorkspace(Protocol):
    """Workspace surface needed to derive parent sessions for children."""

    session: DaytonaSandboxSession | None


class ChildDelegateOwner(Protocol):
    """Interpreter/facade surface required by recursive child construction."""

    runtime: DaytonaSandboxRuntime
    timeout: int
    execute_timeout: int | None
    volume_name: str | None
    volume_subpath: str | None
    repo_url: str | None
    repo_ref: str | None
    context_paths: list[str]
    sandbox_spec: Any | None
    sandbox_labels: dict[str, str]
    sub_lm: Any | None
    _sub_rlm_max_depth: int
    rlm_max_iterations: int
    child_isolation_mode: Any
    child_fork_fallback: Any
    delegate_max_calls_per_turn: int
    delegate_result_truncation_chars: int
    llm_call_timeout: int
    async_execute: bool
    child_isolation_metadata: dict[str, Any] | None
    _check_and_increment_llm_calls: Callable[..., Any]


class ChildDelegation:
    """Build recursive child interpreters using the shared isolation policy."""

    def __init__(
        self,
        *,
        workspace: ChildWorkspace,
        executor: Any,
        callback_owner: object,
    ) -> None:
        self._workspace = workspace
        self._executor = executor
        self._owner = cast(ChildDelegateOwner, callback_owner)

    @property
    def isolation_metadata(self) -> dict[str, Any] | None:
        return self._owner.child_isolation_metadata

    def _parent_session_for_child(self) -> DaytonaSandboxSession | None:
        parent_session = self._workspace.session
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
        owner = self._owner
        child_volume_name = owner.volume_name if volume_name is _UNSET else cast(str | None, volume_name)
        child_volume_subpath = owner.volume_subpath if volume_subpath is _UNSET else cast(str | None, volume_subpath)
        child_cls = cast(Callable[..., Any], owner.__class__)
        return child_cls(
            runtime=runtime,
            owns_runtime=owns_runtime,
            timeout=owner.timeout,
            execute_timeout=owner.execute_timeout,
            volume_name=child_volume_name,
            volume_subpath=child_volume_subpath,
            repo_url=owner.repo_url,
            repo_ref=owner.repo_ref,
            context_paths=list(owner.context_paths),
            sandbox_spec=owner.sandbox_spec,
            sandbox_labels=owner.sandbox_labels,
            delete_session_on_shutdown=delete_session_on_shutdown,
            delete_context_on_shutdown=delete_context_on_shutdown,
            sub_lm=owner.sub_lm,
            max_llm_calls=remaining_llm_budget,
            max_recursion_depth=owner._sub_rlm_max_depth,
            rlm_max_iterations=owner.rlm_max_iterations,
            child_isolation_mode=owner.child_isolation_mode,
            child_fork_fallback=owner.child_fork_fallback,
            delegate_max_calls_per_turn=owner.delegate_max_calls_per_turn,
            delegate_result_truncation_chars=owner.delegate_result_truncation_chars,
            llm_call_timeout=owner.llm_call_timeout,
            default_execution_profile=ExecutionProfile.RLM_DELEGATE,
            async_execute=owner.async_execute,
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
        owner = self._owner
        child._check_and_increment_llm_calls = owner._check_and_increment_llm_calls
        remaining_budget = getattr(owner, "_remaining_llm_budget", None)
        if callable(remaining_budget):
            child._remaining_llm_budget = remaining_budget
        parent_depth = getattr(owner, "_sub_rlm_depth", 0)
        parent_max = owner._sub_rlm_max_depth
        initialize_sub_rlm_state(child, depth=parent_depth + 1, max_depth=parent_max)
        for attr in ("_host_repository", "_host_identity", "_host_run_id"):
            parent_val = getattr(owner, attr, None)
            if parent_val is not None:
                setattr(child, attr, parent_val)

    def build_delegate_child(self, *, remaining_llm_budget: int) -> Any:
        return _build_delegate_child_policy(
            self._owner,
            remaining_llm_budget=remaining_llm_budget,
        )


def build_delegate_child(
    interpreter: Any,
    *,
    remaining_llm_budget: int,
) -> Any:
    """Build a recursive RLM child interpreter using the isolation policy."""
    if isinstance(interpreter, ChildDelegation):
        return interpreter.build_delegate_child(remaining_llm_budget=remaining_llm_budget)
    fn = getattr(interpreter, "build_delegate_child", None)
    owner_impl = getattr(type(interpreter), "build_delegate_child", None)
    owner_func = getattr(owner_impl, "__func__", owner_impl)
    daytona_func = getattr(ChildDelegation, "build_delegate_child", None)
    if callable(fn) and owner_func is not None and owner_func is not daytona_func:
        return fn(remaining_llm_budget=remaining_llm_budget)
    return _build_delegate_child_policy(
        interpreter,
        remaining_llm_budget=remaining_llm_budget,
    )


__all__ = ["ChildDelegation", "build_delegate_child"]
