"""Recursive RLM child sandbox isolation policy for Daytona interpreters."""

from __future__ import annotations

import logging
import uuid
from typing import Any, Literal, cast

from fleet_rlm.runtime.execution.interpreter_protocol import ExecutionProfile
from fleet_rlm.runtime.execution.interpreter_support import initialize_sub_rlm_state

from .runtime import DaytonaSandboxRuntime
from .sandbox_spec import SandboxSpec
from .session_runtime import DaytonaSandboxSession

ChildIsolationMode = Literal["auto", "context"]
ChildForkFallback = Literal["clean", "fail"]

_CHILD_VOLUME_SUBPATH_ROOT = "meta/rlm-children"
_UNSET = object()


class RLMChildIsolationError(RuntimeError):
    """Raised when an isolated recursive RLM child sandbox cannot be created."""

    def __init__(self, message: str, *, metadata: dict[str, Any]) -> None:
        super().__init__(message)
        self.metadata = dict(metadata)


def normalize_child_isolation_mode(value: Any) -> ChildIsolationMode:
    """Normalize and validate recursive child isolation mode config."""
    normalized = str(value or "auto").strip().lower()
    if normalized not in {"auto", "context"}:
        raise ValueError("RLM child isolation mode must be one of: 'auto', 'context'")
    return cast(ChildIsolationMode, normalized)


def normalize_child_fork_fallback(value: Any) -> ChildForkFallback:
    """Normalize and validate recursive child fork fallback config."""
    normalized = str(value or "clean").strip().lower()
    if normalized not in {"clean", "fail"}:
        raise ValueError("RLM child fork fallback must be one of: 'clean', 'fail'")
    return cast(ChildForkFallback, normalized)


def record_child_isolation_metadata(child: Any, **metadata: Any) -> None:
    """Merge child-isolation metadata onto an interpreter-like object."""
    current = dict(getattr(child, "child_isolation_metadata", None) or {})
    current.update({key: value for key, value in metadata.items() if value is not None})
    child.child_isolation_metadata = current


def parent_session_for_child(interpreter: Any) -> DaytonaSandboxSession | None:
    """Return the parent session when it can safely seed a child."""
    fn = getattr(interpreter, "_parent_session_for_child", None)
    if callable(fn) and hasattr(type(interpreter), "_parent_session_for_child"):
        return fn()
    parent_session = getattr(interpreter, "_session", None)
    if parent_session is None or getattr(parent_session, "sandbox", None) is None:
        return None
    return parent_session


def build_child_interpreter(
    interpreter: Any,
    *,
    runtime: DaytonaSandboxRuntime,
    owns_runtime: bool,
    delete_session_on_shutdown: bool,
    delete_context_on_shutdown: bool = False,
    remaining_llm_budget: int,
    volume_name: str | None | object = _UNSET,
    volume_subpath: str | None | object = _UNSET,
) -> Any:
    """Build a child interpreter, preferring the concrete interpreter hook."""
    fn = getattr(interpreter, "_build_child_interpreter", None)
    if callable(fn) and hasattr(type(interpreter), "_build_child_interpreter"):
        return fn(
            runtime=runtime,
            owns_runtime=owns_runtime,
            delete_session_on_shutdown=delete_session_on_shutdown,
            delete_context_on_shutdown=delete_context_on_shutdown,
            remaining_llm_budget=remaining_llm_budget,
            volume_name=volume_name,
            volume_subpath=volume_subpath,
        )
    child_volume_name = (
        getattr(interpreter, "volume_name", None)
        if volume_name is _UNSET
        else cast(str | None, volume_name)
    )
    child_volume_subpath = (
        getattr(interpreter, "volume_subpath", None)
        if volume_subpath is _UNSET
        else cast(str | None, volume_subpath)
    )
    return interpreter.__class__(
        runtime=runtime,
        owns_runtime=owns_runtime,
        timeout=interpreter.timeout,
        execute_timeout=interpreter.execute_timeout,
        volume_name=child_volume_name,
        volume_subpath=child_volume_subpath,
        repo_url=interpreter.repo_url,
        repo_ref=interpreter.repo_ref,
        context_paths=list(interpreter.context_paths),
        sandbox_spec=cast(
            SandboxSpec | None, getattr(interpreter, "sandbox_spec", None)
        ),
        sandbox_labels=interpreter.sandbox_labels,
        delete_session_on_shutdown=delete_session_on_shutdown,
        delete_context_on_shutdown=delete_context_on_shutdown,
        sub_lm=interpreter.sub_lm,
        max_llm_calls=remaining_llm_budget,
        max_recursion_depth=getattr(interpreter, "_sub_rlm_max_depth", 2),
        rlm_max_iterations=getattr(interpreter, "rlm_max_iterations", 30),
        child_isolation_mode=getattr(interpreter, "child_isolation_mode", "auto"),
        child_fork_fallback=getattr(interpreter, "child_fork_fallback", "clean"),
        delegate_max_calls_per_turn=getattr(
            interpreter, "delegate_max_calls_per_turn", 8
        ),
        delegate_result_truncation_chars=getattr(
            interpreter, "delegate_result_truncation_chars", 8000
        ),
        llm_call_timeout=interpreter.llm_call_timeout,
        default_execution_profile=ExecutionProfile.RLM_DELEGATE,
        async_execute=interpreter.async_execute,
    )


def attach_shared_parent_session(
    child: Any,
    *,
    parent_session: DaytonaSandboxSession,
    runtime: DaytonaSandboxRuntime,
) -> None:
    """Attach a fresh child context to the parent sandbox for context mode."""
    fn = getattr(child, "_attach_shared_parent_session", None)
    if callable(fn) and hasattr(type(child), "_attach_shared_parent_session"):
        fn(child, parent_session=parent_session, runtime=runtime)
        return
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
        logging.getLogger(__name__).debug(
            "Failed to bind Daytona sandbox session to current async owner: %s",
            exc,
        )
    child._persisted_sandbox_id = parent_session.sandbox_id
    child._persisted_workspace_path = parent_session.workspace_path


def propagate_parent_recursion_state(child: Any, parent: Any) -> None:
    """Share recursion depth and semantic-call budget state with a child."""
    fn = getattr(parent, "_propagate_parent_recursion_state", None)
    if callable(fn) and hasattr(type(parent), "_propagate_parent_recursion_state"):
        fn(child)
        return
    setattr(
        child,
        "_check_and_increment_llm_calls",
        parent._check_and_increment_llm_calls,
    )
    remaining_budget = getattr(parent, "_remaining_llm_budget", None)
    if callable(remaining_budget):
        setattr(child, "_remaining_llm_budget", remaining_budget)
    parent_depth = getattr(parent, "_sub_rlm_depth", 0)
    parent_max = getattr(parent, "_sub_rlm_max_depth", 2)
    initialize_sub_rlm_state(child, depth=parent_depth + 1, max_depth=parent_max)

    # Propagate host-mediated evidence bridge references to children
    for attr in ("_host_repository", "_host_identity", "_host_run_id"):
        parent_val = getattr(parent, attr, None)
        if parent_val is not None:
            setattr(child, attr, parent_val)


def build_delegate_child(
    interpreter: Any,
    *,
    remaining_llm_budget: int,
) -> Any:
    """Build a recursive RLM child interpreter using the isolation policy."""
    mode = normalize_child_isolation_mode(
        getattr(interpreter, "child_isolation_mode", "auto")
    )
    fallback = normalize_child_fork_fallback(
        getattr(interpreter, "child_fork_fallback", "clean")
    )
    parent_session = parent_session_for_child(interpreter)
    runtime_obj = getattr(interpreter, "runtime", None)
    if runtime_obj is None:
        raise RLMChildIsolationError(
            "Cannot create recursive RLM child without a Daytona runtime",
            metadata={"mode": mode, "strategy": "unavailable"},
        )
    runtime = cast(DaytonaSandboxRuntime, runtime_obj)
    parent_sandbox_id = getattr(parent_session, "sandbox_id", None)
    effective_volume_name = getattr(parent_session, "volume_name", None) or getattr(
        interpreter, "volume_name", None
    )

    def _clean_child(
        *,
        reason: str,
        volume_name: str | None,
        volume_subpath: str | None,
        fallback_from: str | None = None,
    ) -> Any:
        try:
            child_runtime = DaytonaSandboxRuntime(config=runtime._resolved_config)
            child = build_child_interpreter(
                interpreter,
                runtime=child_runtime,
                owns_runtime=True,
                delete_session_on_shutdown=True,
                delete_context_on_shutdown=False,
                remaining_llm_budget=remaining_llm_budget,
                volume_name=volume_name,
                volume_subpath=volume_subpath,
            )
        except Exception as exc:
            metadata = {
                "mode": mode,
                "strategy": "clean",
                "reason": reason,
                "parent_sandbox_id": parent_sandbox_id,
                "volume_name": volume_name,
                "volume_subpath": volume_subpath,
                "fallback_from": fallback_from,
                "fallback_status": "failed" if fallback_from else None,
                "error": str(exc),
            }
            raise RLMChildIsolationError(
                "Failed to create isolated clean child Daytona sandbox",
                metadata=metadata,
            ) from exc

        record_child_isolation_metadata(
            child,
            mode=mode,
            strategy="clean",
            reason=reason,
            parent_sandbox_id=parent_sandbox_id,
            volume_name=volume_name,
            volume_subpath=volume_subpath,
            fallback_from=fallback_from,
            fallback_status="used" if fallback_from else None,
        )
        return child

    if mode == "context":
        if parent_session is None:
            child = _clean_child(
                reason="context_no_parent_session",
                volume_name=effective_volume_name,
                volume_subpath=getattr(interpreter, "volume_subpath", None),
            )
        else:
            child = build_child_interpreter(
                interpreter,
                runtime=runtime,
                owns_runtime=False,
                delete_session_on_shutdown=False,
                delete_context_on_shutdown=True,
                remaining_llm_budget=remaining_llm_budget,
            )
            attach_shared_parent_session(
                child,
                parent_session=parent_session,
                runtime=runtime,
            )
            record_child_isolation_metadata(
                child,
                mode=mode,
                strategy="context",
                parent_sandbox_id=parent_sandbox_id,
                child_sandbox_id=parent_sandbox_id,
                volume_name=effective_volume_name,
                volume_subpath=getattr(interpreter, "volume_subpath", None),
            )
        propagate_parent_recursion_state(child, interpreter)
        return child

    if effective_volume_name:
        child = _clean_child(
            reason="durable_volume_mounted",
            volume_name=effective_volume_name,
            volume_subpath=_child_volume_subpath(parent_session),
        )
        propagate_parent_recursion_state(child, interpreter)
        return child

    if parent_session is not None:
        try:
            fork_sandbox = getattr(runtime, "fork_sandbox")
            forked_session = fork_sandbox(
                parent_session,
                name=_child_sandbox_name("fork"),
                timeout=float(getattr(interpreter, "timeout", 60)),
            )
            child = build_child_interpreter(
                interpreter,
                runtime=runtime,
                owns_runtime=False,
                delete_session_on_shutdown=True,
                delete_context_on_shutdown=False,
                remaining_llm_budget=remaining_llm_budget,
                volume_name=None,
                volume_subpath=None,
            )
            child._session = forked_session
            child._session._runtime_ref = runtime
            try:
                child._session.bind_current_async_owner()
            except RuntimeError as exc:
                logging.getLogger(__name__).debug(
                    "Failed to bind forked Daytona sandbox session: %s",
                    exc,
                )
            child._persisted_sandbox_id = forked_session.sandbox_id
            child._persisted_workspace_path = forked_session.workspace_path
            child._persisted_context_sources = list(forked_session.context_sources)
            child._persisted_context_id = forked_session.context_id
            child._persisted_volume_name = None
            record_child_isolation_metadata(
                child,
                mode=mode,
                strategy="fork",
                parent_sandbox_id=parent_sandbox_id,
                child_sandbox_id=forked_session.sandbox_id,
                volume_name=None,
            )
            propagate_parent_recursion_state(child, interpreter)
            return child
        except Exception as exc:
            if fallback == "fail":
                metadata = {
                    "mode": mode,
                    "strategy": "fork",
                    "parent_sandbox_id": parent_sandbox_id,
                    "fallback_status": "disabled",
                    "error": str(exc),
                }
                raise RLMChildIsolationError(
                    "Failed to fork isolated child Daytona sandbox",
                    metadata=metadata,
                ) from exc
            child = _clean_child(
                reason="fork_failed",
                volume_name=None,
                volume_subpath=None,
                fallback_from="fork",
            )
            propagate_parent_recursion_state(child, interpreter)
            return child

    child = _clean_child(
        reason="no_parent_session",
        volume_name=None,
        volume_subpath=None,
    )
    propagate_parent_recursion_state(child, interpreter)
    return child


def _safe_child_path_token(value: Any) -> str:
    raw = str(value or "unknown").strip()
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in raw)
    safe = safe.strip("-_")
    return safe[:80] or "unknown"


def _child_sandbox_name(strategy: str) -> str:
    suffix = uuid.uuid4().hex[:12]
    return f"fleet-rlm-{strategy}-child-{suffix}"


def _child_volume_subpath(parent_session: DaytonaSandboxSession | None) -> str:
    parent_id = getattr(parent_session, "sandbox_id", None) if parent_session else None
    return (
        f"{_CHILD_VOLUME_SUBPATH_ROOT}/"
        f"{_safe_child_path_token(parent_id)}/"
        f"{uuid.uuid4().hex[:12]}"
    )


__all__ = [
    "ChildForkFallback",
    "ChildIsolationMode",
    "RLMChildIsolationError",
    "_UNSET",
    "attach_shared_parent_session",
    "build_child_interpreter",
    "build_delegate_child",
    "normalize_child_fork_fallback",
    "normalize_child_isolation_mode",
    "parent_session_for_child",
    "propagate_parent_recursion_state",
    "record_child_isolation_metadata",
]
