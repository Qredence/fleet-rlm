"""Child interpreter construction hooks for Daytona recursive RLM delegation."""

from __future__ import annotations

import logging
from typing import Any, cast

from fleet_rlm.runtime.execution.interpreter_support import initialize_sub_rlm_state
from fleet_rlm.runtime.execution.profiles import ExecutionProfile

from .child_isolation import _UNSET
from .child_isolation import build_delegate_child as _build_delegate_child_policy
from .runtime import DaytonaSandboxRuntime
from .session_runtime import DaytonaSandboxSession


class DaytonaInterpreterChildMixin:
    def _parent_session_for_child(self: Any) -> DaytonaSandboxSession | None:
        parent_session = getattr(self, "_session", None)
        if parent_session is None or getattr(parent_session, "sandbox", None) is None:
            return None
        return parent_session

    def _build_child_interpreter(
        self: Any,
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
        return self.__class__(
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
        self: Any,
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

    def _propagate_parent_recursion_state(self: Any, child: Any) -> None:
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

    def build_delegate_child(self: Any, *, remaining_llm_budget: int) -> Any:
        return build_delegate_child(self, remaining_llm_budget=remaining_llm_budget)


def build_delegate_child(
    interpreter: Any,
    *,
    remaining_llm_budget: int,
) -> Any:
    """Build a recursive RLM child interpreter using the isolation policy."""
    fn = getattr(interpreter, "build_delegate_child", None)
    owner_impl = getattr(type(interpreter), "build_delegate_child", None)
    owner_func = getattr(owner_impl, "__func__", owner_impl)
    daytona_func = getattr(DaytonaInterpreterChildMixin, "build_delegate_child", None)
    if callable(fn) and owner_func is not None and owner_func is not daytona_func:
        return fn(remaining_llm_budget=remaining_llm_budget)
    return _build_delegate_child_policy(
        interpreter,
        remaining_llm_budget=remaining_llm_budget,
    )


__all__ = ["DaytonaInterpreterChildMixin", "build_delegate_child"]
