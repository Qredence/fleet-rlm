"""Fleet-specific RLM child sandbox isolation, delegation, evidence tracing, and context staging.

Merges:
- child_isolation.py   — recursive child sandbox isolation policy
- child_delegation.py  — child interpreter construction / ChildDelegation class
- evidence_bridge.py   — host-mediated evidence persistence for sandbox RLM loops
- context_staging.py   — host-context staging helpers for Daytona workspace sessions
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Child isolation policy
# ---------------------------------------------------------------------------
import json
import logging
import os
import re
import uuid
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Protocol, cast

from fleet_rlm.runtime.content.ingestion import read_document_content
from fleet_rlm.runtime.execution.interpreter_protocol import ExecutionProfile
from fleet_rlm.runtime.execution.interpreter_support import initialize_sub_rlm_state
from fleet_rlm.runtime.execution.llm_query import SUB_RLM_MAX_DEPTH
from fleet_rlm.utils.paths import is_local_path

from .async_compat import _run_async_compat
from .errors import DaytonaDiagnosticError
from .models import ContextSource, SandboxSpec
from .runtime import DaytonaSandboxRuntime
from .sdk_ops import ensure_remote_directory as _ensure_remote_directory
from .session_runtime import DaytonaSandboxSession, _run_admin_code

logger = logging.getLogger(__name__)

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
    volume_name: str | object | None = _UNSET,
    volume_subpath: str | object | None = _UNSET,
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
        getattr(interpreter, "volume_name", None) if volume_name is _UNSET else cast(str | None, volume_name)
    )
    child_volume_subpath = (
        getattr(interpreter, "volume_subpath", None) if volume_subpath is _UNSET else cast(str | None, volume_subpath)
    )
    interpreter_execute_timeout = int(getattr(interpreter, "execute_timeout", None) or interpreter.timeout)
    delegate_execution_timeout = int(
        getattr(interpreter, "delegate_execution_timeout", interpreter_execute_timeout) or interpreter_execute_timeout
    )
    child_execute_timeout = max(1, min(interpreter_execute_timeout, delegate_execution_timeout))
    return interpreter.__class__(
        runtime=runtime,
        owns_runtime=owns_runtime,
        timeout=interpreter.timeout,
        execute_timeout=child_execute_timeout,
        volume_name=child_volume_name,
        volume_subpath=child_volume_subpath,
        repo_url=interpreter.repo_url,
        repo_ref=interpreter.repo_ref,
        context_paths=list(interpreter.context_paths),
        sandbox_spec=cast(SandboxSpec | None, getattr(interpreter, "sandbox_spec", None)),
        sandbox_labels=interpreter.sandbox_labels,
        delete_session_on_shutdown=delete_session_on_shutdown,
        delete_context_on_shutdown=delete_context_on_shutdown,
        sub_lm=interpreter.sub_lm,
        max_llm_calls=remaining_llm_budget,
        max_recursion_depth=getattr(interpreter, "_sub_rlm_max_depth", SUB_RLM_MAX_DEPTH),
        rlm_max_iterations=getattr(interpreter, "rlm_max_iterations", 30),
        child_isolation_mode=getattr(interpreter, "child_isolation_mode", "auto"),
        child_fork_fallback=getattr(interpreter, "child_fork_fallback", "clean"),
        delegate_max_calls_per_turn=getattr(interpreter, "delegate_max_calls_per_turn", 8),
        delegate_result_truncation_chars=getattr(interpreter, "delegate_result_truncation_chars", 8000),
        delegate_execution_timeout=delegate_execution_timeout,
        broker_health_timeout=getattr(interpreter, "broker_health_timeout", 20.0),
        broker_tool_call_timeout=getattr(interpreter, "broker_tool_call_timeout", 180.0),
        broker_start_retries=getattr(interpreter, "broker_start_retries", 1),
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
    parent_max = getattr(parent, "_sub_rlm_max_depth", SUB_RLM_MAX_DEPTH)
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
    mode = normalize_child_isolation_mode(getattr(interpreter, "child_isolation_mode", "auto"))
    fallback = normalize_child_fork_fallback(getattr(interpreter, "child_fork_fallback", "clean"))
    parent_session = parent_session_for_child(interpreter)
    runtime_obj = getattr(interpreter, "runtime", None)
    if runtime_obj is None:
        raise RLMChildIsolationError(
            "Cannot create recursive RLM child without a Daytona runtime",
            metadata={"mode": mode, "strategy": "unavailable"},
        )
    runtime = cast(DaytonaSandboxRuntime, runtime_obj)
    parent_sandbox_id = getattr(parent_session, "sandbox_id", None)
    effective_volume_name = getattr(parent_session, "volume_name", None) or getattr(interpreter, "volume_name", None)

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
    return f"{_CHILD_VOLUME_SUBPATH_ROOT}/{_safe_child_path_token(parent_id)}/{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------------------
# Child delegation
# ---------------------------------------------------------------------------

# Internal alias used by ChildDelegation._build_child_interpreter and the
# module-level build_delegate_child dispatcher.
_build_delegate_child_policy = build_delegate_child


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

    def __getattr__(self, name: str) -> Any:
        return getattr(self._owner, name)

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
        volume_name: str | object | None = _UNSET,
        volume_subpath: str | object | None = _UNSET,
    ) -> Any:
        owner = self._owner
        child_volume_name = owner.volume_name if volume_name is _UNSET else cast(str | None, volume_name)
        child_volume_subpath = owner.volume_subpath if volume_subpath is _UNSET else cast(str | None, volume_subpath)
        child_cls = cast(Callable[..., Any], owner.__class__)
        owner_execute_timeout = int(owner.execute_timeout or owner.timeout)
        delegate_execution_timeout = int(
            getattr(owner, "delegate_execution_timeout", owner_execute_timeout) or owner_execute_timeout
        )
        child_execute_timeout = max(1, min(owner_execute_timeout, delegate_execution_timeout))
        return child_cls(
            runtime=runtime,
            owns_runtime=owns_runtime,
            timeout=owner.timeout,
            execute_timeout=child_execute_timeout,
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
            delegate_execution_timeout=delegate_execution_timeout,
            broker_health_timeout=getattr(owner, "broker_health_timeout", 20.0),
            broker_tool_call_timeout=getattr(owner, "broker_tool_call_timeout", 180.0),
            broker_start_retries=getattr(owner, "broker_start_retries", 1),
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
            logging.getLogger(__name__).debug(
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
            self,
            remaining_llm_budget=remaining_llm_budget,
        )


def build_delegate_child(  # type: ignore[no-redef]
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


# ---------------------------------------------------------------------------
# Evidence bridge
# ---------------------------------------------------------------------------
#
# Host-mediated evidence persistence for sandbox RLM loops.
#
# Provides ``store_evidence``, ``fetch_evidence``, and ``list_evidence``
# functions that sandbox code calls through the Daytona bridge.  Each
# function delegates to the host-side ``FleetRepository`` using the
# ``_host_repository`` and ``_host_identity`` attributes attached to the
# interpreter by the websocket session layer.
#
# This keeps ``DATABASE_URL`` out of the sandbox while giving RLM child
# runs durable cross-child evidence sharing via NeonDB.

# ---------------------------------------------------------------------------
# Evidence payload bounds
# ---------------------------------------------------------------------------
_EVIDENCE_MAX_KEY_BYTES = 512
_EVIDENCE_MAX_CONTENT_BYTES = 1_000_000  # 1 MiB
_EVIDENCE_MAX_TAGS = 32
_EVIDENCE_MAX_TAG_BYTES = 256
_EVIDENCE_MAX_LIMIT = 500

# Regex pattern to detect potential credential strings in error messages.
# Ordered from most-specific to least-specific to avoid partial matches.
_CREDENTIAL_PATTERN = re.compile(
    r"("
    # Full database/service URLs (match scheme + everything to next whitespace or comma)
    r"(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|neon(?:db)?|redis|amqp)://[^\s,;'\"]*"
    r"|password=[^\s,;'\"&]*"
    r"|sslpassword=[^\s,;'\"&]*"
    r"|host=[^\s,;'\"&]+"
    # Named environment variable references
    r"|DAYTONA_API_KEY"
    r"|DATABASE(?:_ADMIN)?_URL"
    r"|(?:LLM_|OPENAI_|ANTHROPIC_|AZURE_)?API_KEY"
    r"|SECRET_KEY"
    r"|ACCESS_TOKEN"
    # JWT-like base64 tokens (at least 20 chars in the header section)
    r"|eyJ[A-Za-z0-9+/\-_]{20,}"
    r")",
    re.IGNORECASE,
)


def _redact_error_message(message: str) -> str:
    """Replace potential credential-bearing patterns with a safe placeholder."""
    return _CREDENTIAL_PATTERN.sub("[REDACTED]", message)


def _safe_error(exc: Exception) -> str:
    """Return a redacted, sandbox-safe error string from an exception."""
    return _redact_error_message(str(exc))


def _validate_evidence_payload(
    *,
    key: str,
    content: str,
    kind: str,
    scope: str,
    tags: list[str] | None,
) -> dict[str, Any] | None:
    """Return a structured validation error dict, or None when the payload is valid."""
    if len(key.encode("utf-8")) > _EVIDENCE_MAX_KEY_BYTES:
        return {
            "status": "error",
            "reason": "validation_error",
            "error": f"Evidence key exceeds maximum {_EVIDENCE_MAX_KEY_BYTES} bytes.",
        }
    if len(content.encode("utf-8")) > _EVIDENCE_MAX_CONTENT_BYTES:
        return {
            "status": "error",
            "reason": "validation_error",
            "error": f"Evidence content exceeds maximum {_EVIDENCE_MAX_CONTENT_BYTES} bytes.",
        }
    effective_tags = list(tags or [])
    if len(effective_tags) > _EVIDENCE_MAX_TAGS:
        return {
            "status": "error",
            "reason": "validation_error",
            "error": f"Evidence tag count exceeds maximum {_EVIDENCE_MAX_TAGS}.",
        }
    for tag in effective_tags:
        if not isinstance(tag, str):
            return {
                "status": "error",
                "reason": "validation_error",
                "error": "Evidence tags must be strings.",
            }
        if len(tag.encode("utf-8")) > _EVIDENCE_MAX_TAG_BYTES:
            return {
                "status": "error",
                "reason": "validation_error",
                "error": f"Evidence tag exceeds maximum {_EVIDENCE_MAX_TAG_BYTES} bytes.",
            }

    from fleet_rlm.integrations.database.models_enums import MemoryKind, MemoryScope

    try:
        MemoryScope(scope)
    except ValueError:
        return {
            "status": "error",
            "reason": "validation_error",
            "error": f"Invalid evidence scope: {scope!r}.",
        }
    try:
        MemoryKind(kind)
    except ValueError:
        return {
            "status": "error",
            "reason": "validation_error",
            "error": f"Invalid evidence kind: {kind!r}.",
        }

    if not isinstance(content, str):
        return {
            "status": "error",
            "reason": "validation_error",
            "error": "Evidence content must be a string.",
        }
    return None


def _host_refs(interpreter: Any) -> tuple[Any, Any, Any]:
    repository = getattr(interpreter, "_host_repository", None)
    identity = getattr(interpreter, "_host_identity", None)
    run_id = getattr(interpreter, "_host_run_id", None)
    return repository, identity, run_id


def store_evidence(
    interpreter: Any,
    key: str,
    content: str,
    kind: str = "context",
    scope: str = "run",
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Persist evidence from sandbox code into NeonDB via the host repository.

    Validates payload bounds and kind/scope enum values before calling the
    repository, and redacts credential-bearing values from error messages so
    that DATABASE_URL, API keys, and other secrets never cross the sandbox
    boundary in error payloads.
    """
    validation_error = _validate_evidence_payload(key=key, content=content, kind=kind, scope=scope, tags=tags)
    if validation_error is not None:
        return validation_error

    repository, identity, run_id = _host_refs(interpreter)
    if repository is None or identity is None:
        return {"status": "skipped", "reason": "no_repository"}

    from fleet_rlm.integrations.database.models_enums import (
        MemoryKind,
        MemoryScope,
        MemorySource,
    )
    from fleet_rlm.integrations.database.repository_memory import (
        MemoryItemCreateRequest,
    )

    try:
        item = _run_async_compat(
            repository.store_memory_item,
            MemoryItemCreateRequest(
                tenant_id=identity.tenant_id,
                workspace_id=identity.workspace_id,
                user_id=identity.user_id,
                run_id=run_id,
                scope=MemoryScope(scope),
                scope_id=str(key),
                kind=MemoryKind(kind),
                source=MemorySource.TOOL,
                content_text=str(content),
                tags=list(tags or []),
            ),
        )
    except Exception as exc:
        logger.warning("store_evidence failed: %s", exc)
        return {"status": "error", "reason": "store_failed", "error": _safe_error(exc)}
    return {"status": "ok", "id": str(item.id), "key": key}


def fetch_evidence(
    interpreter: Any,
    scope: str = "run",
    scope_id: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """Fetch evidence items from NeonDB for use inside sandbox code.

    Error messages are redacted to prevent credential leakage.
    """
    repository, identity, _ = _host_refs(interpreter)
    if repository is None or identity is None:
        return {"status": "skipped", "items": []}

    effective_limit = max(1, min(limit, _EVIDENCE_MAX_LIMIT))

    from fleet_rlm.integrations.database.models_enums import MemoryScope

    try:
        MemoryScope(scope)
    except ValueError:
        return {
            "status": "error",
            "items": [],
            "reason": "validation_error",
            "error": f"Invalid evidence scope: {scope!r}.",
        }

    try:
        items = _run_async_compat(
            repository.list_memory_items,
            tenant_id=identity.tenant_id,
            workspace_id=identity.workspace_id,
            user_id=identity.user_id,
            scope=MemoryScope(scope),
            scope_id=scope_id,
            limit=effective_limit,
        )
    except Exception as exc:
        logger.warning("fetch_evidence failed: %s", exc)
        return {"status": "error", "items": [], "reason": "fetch_failed", "error": _safe_error(exc)}
    return {
        "status": "ok",
        "items": [
            {
                "id": str(i.id),
                "scope_id": i.scope_id,
                "content": i.content_text,
                "kind": str(i.kind.value),
            }
            for i in items
        ],
    }


def list_evidence(
    interpreter: Any,
    scope: str = "run",
    limit: int = 50,
) -> dict[str, Any]:
    """List available evidence handles (metadata only, no full content).

    Error messages are redacted to prevent credential leakage.
    """
    repository, identity, _ = _host_refs(interpreter)
    if repository is None or identity is None:
        return {"status": "skipped", "items": []}

    effective_limit = max(1, min(limit, _EVIDENCE_MAX_LIMIT))

    from fleet_rlm.integrations.database.models_enums import MemoryScope

    try:
        MemoryScope(scope)
    except ValueError:
        return {
            "status": "error",
            "items": [],
            "reason": "validation_error",
            "error": f"Invalid evidence scope: {scope!r}.",
        }

    try:
        items = _run_async_compat(
            repository.list_memory_items,
            tenant_id=identity.tenant_id,
            workspace_id=identity.workspace_id,
            user_id=identity.user_id,
            scope=MemoryScope(scope),
            limit=effective_limit,
        )
    except Exception as exc:
        logger.warning("list_evidence failed: %s", exc)
        return {"status": "error", "items": [], "reason": "list_failed", "error": _safe_error(exc)}
    return {
        "status": "ok",
        "items": [
            {
                "id": str(i.id),
                "scope_id": i.scope_id,
                "kind": str(i.kind.value),
                "importance": i.importance,
            }
            for i in items
        ],
    }


_EVIDENCE_TOOL_NAMES = frozenset({"store_evidence", "fetch_evidence", "list_evidence"})


class DaytonaEvidenceSink:
    """Adapts module-level evidence functions to the EvidenceSink protocol.

    Used by runtime modules that accept an ``EvidenceSink`` without knowing
    Daytona exists. The module-level functions stay the public API for sandbox
    code registered via ``bridge_callbacks``; this class is the shape the
    runtime business logic sees.
    """

    def __init__(self, interpreter: Any) -> None:
        self._interpreter = interpreter

    def store(
        self,
        *,
        key: str,
        content: str,
        kind: str = "context",
        scope: str = "run",
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        return store_evidence(
            self._interpreter,
            key=key,
            content=content,
            kind=kind,
            scope=scope,
            tags=tags,
        )

    def list_items(self, *, scope: str = "run", limit: int = 50) -> dict[str, Any]:
        return list_evidence(self._interpreter, scope=scope, limit=limit)


# ---------------------------------------------------------------------------
# Context staging
# ---------------------------------------------------------------------------
#
# Host-context staging helpers for Daytona workspace sessions.


def _safe_context_slug(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-")
    return cleaned or "context"


def _resolve_local_context_path(path: str) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    resolved = candidate.resolve()
    if not resolved.exists():
        raise DaytonaDiagnosticError(
            f"Context path does not exist: {path}",
            category="context_stage_error",
            phase="context_stage",
        )
    if not os.access(resolved, os.R_OK):
        raise DaytonaDiagnosticError(
            f"Context path is not readable: {resolved}",
            category="context_stage_error",
            phase="context_stage",
        )
    return resolved


def _ensure_remote_parent(fs: Any, remote_path: PurePosixPath) -> None:
    _ensure_remote_directory(fs, remote_path.parent)


def _clear_staged_context_paths(
    *,
    sandbox: Any,
    workspace_path: str,
) -> None:
    context_root = PurePosixPath(workspace_path) / ".fleet-rlm" / "context"
    _run_admin_code(
        sandbox=sandbox,
        phase="context_stage",
        category="context_stage_error",
        error_prefix="Daytona context reset failure",
        code=f"""
import pathlib as _pathlib
import shutil as _shutil

context_root = _pathlib.Path({str(context_root)!r})
if context_root.exists():
    _shutil.rmtree(context_root)
print(str(context_root))
""".strip(),
    )


def _upload_remote_text(
    fs: Any,
    remote_path: PurePosixPath,
    content: str,
) -> None:
    _ensure_remote_parent(fs, remote_path)
    fs.upload_file(content.encode("utf-8"), str(remote_path))


def _read_document_content(path: Path) -> tuple[str, dict[str, Any]]:
    text, metadata = read_document_content(path)
    return text, metadata if isinstance(metadata, dict) else {}


def _build_staged_filename(*, source_path: Path, source_type: str) -> str:
    return source_path.name if source_type == "text" else f"{source_path.name}.extracted.txt"


def _stage_local_file(
    *,
    fs: Any,
    resolved_path: Path,
    staged_root: PurePosixPath,
    source_id: str,
) -> ContextSource:
    text, metadata = _read_document_content(resolved_path)
    source_type = str(metadata.get("source_type") or "text")
    staged_relative = staged_root / _build_staged_filename(
        source_path=resolved_path,
        source_type=source_type,
    )
    _upload_remote_text(fs, staged_relative, text)
    return ContextSource(
        source_id=source_id,
        kind="file",
        host_path=str(resolved_path),
        staged_path=str(staged_relative),
        source_type=source_type,
        extraction_method=str(metadata.get("extraction_method") or "") or None,
        file_count=1,
    )


def _stage_local_directory(
    *,
    fs: Any,
    resolved_path: Path,
    staged_root: PurePosixPath,
    source_id: str,
) -> ContextSource:
    warnings: list[str] = []
    staged_count = 0
    skipped_count = 0
    extraction_methods: set[str] = set()
    source_types: set[str] = set()

    for local_file in sorted(path for path in resolved_path.rglob("*") if path.is_file()):
        relative_path = local_file.relative_to(resolved_path)
        try:
            text, metadata = _read_document_content(local_file)
        except Exception as exc:
            skipped_count += 1
            warnings.append(f"Skipped {relative_path.as_posix()}: {exc}")
            continue

        source_type = str(metadata.get("source_type") or "text")
        extraction_method = str(metadata.get("extraction_method") or "") or None
        source_types.add(source_type)
        if extraction_method:
            extraction_methods.add(extraction_method)
        destination_name = _build_staged_filename(
            source_path=local_file,
            source_type=source_type,
        )
        staged_relative = staged_root / relative_path.parent / destination_name
        _upload_remote_text(fs, staged_relative, text)
        staged_count += 1

    if staged_count == 0:
        raise DaytonaDiagnosticError(
            f"No supported readable files found in directory: {resolved_path}",
            category="context_stage_error",
            phase="context_stage",
        )

    extraction_method_value = (
        "mixed" if len(extraction_methods) > 1 else next(iter(extraction_methods), None) or "directory_walk"
    )
    source_type_value = "mixed" if len(source_types) > 1 else next(iter(source_types), None) or "text"
    return ContextSource(
        source_id=source_id,
        kind="directory",
        host_path=str(resolved_path),
        staged_path=str(staged_root),
        source_type=source_type_value,
        extraction_method=extraction_method_value,
        file_count=staged_count,
        skipped_count=skipped_count,
        warnings=warnings,
    )


def stage_context_paths(
    *,
    sandbox: Any,
    workspace_path: str,
    context_paths: list[str] | None,
    reset_existing: bool = False,
) -> list[ContextSource]:
    raw_paths = [
        stripped for item in (context_paths or []) if (stripped := str(item).strip()) and is_local_path(stripped)
    ]
    if reset_existing:
        _clear_staged_context_paths(
            sandbox=sandbox,
            workspace_path=workspace_path,
        )
    if not raw_paths:
        return []

    fs = sandbox.fs
    context_root = PurePosixPath(workspace_path) / ".fleet-rlm" / "context"
    _ensure_remote_directory(fs, context_root)
    staged_sources: list[ContextSource] = []

    for index, raw_path in enumerate(raw_paths, start=1):
        source_id = f"context-{index}"
        display_path = raw_path
        try:
            resolved = _resolve_local_context_path(raw_path)
            display_path = str(resolved)
            staged_root = context_root / f"{index:02d}-{_safe_context_slug(resolved.stem or resolved.name)}"
            if resolved.is_dir():
                staged_sources.append(
                    _stage_local_directory(
                        fs=fs,
                        resolved_path=resolved,
                        staged_root=staged_root,
                        source_id=source_id,
                    )
                )
            else:
                staged_sources.append(
                    _stage_local_file(
                        fs=fs,
                        resolved_path=resolved,
                        staged_root=staged_root,
                        source_id=source_id,
                    )
                )
        except DaytonaDiagnosticError as exc:
            # A non-existent or unreadable host context path is recoverable:
            # skip it, warn, and stage the remaining paths rather than aborting
            # the whole turn. Inferred context paths captured from chat text
            # (e.g. URL routes like /docs) routinely don't exist on the host,
            # and a missing optional context source must not block the turn.
            logger.warning("Skipping unreachable context path %r: %s", raw_path, exc)
            continue
        except Exception as exc:
            raise DaytonaDiagnosticError(
                f"Failed to stage context path '{display_path}': {exc}",
                category="context_stage_error",
                phase="context_stage",
            ) from exc

    manifest_path = context_root / "manifest.json"
    _upload_remote_text(
        fs,
        manifest_path,
        json.dumps(
            {"context_sources": [item.to_dict() for item in staged_sources]},
            ensure_ascii=False,
            indent=2,
        ),
    )
    return staged_sources


# Backward-compat alias (matches original context_staging.py)
_astage_context_paths = stage_context_paths


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    # child isolation
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
    # child delegation
    "ChildDelegation",
    "ChildDelegateOwner",
    "ChildWorkspace",
    # evidence bridge
    "DaytonaEvidenceSink",
    "fetch_evidence",
    "list_evidence",
    "store_evidence",
    "_EVIDENCE_TOOL_NAMES",
    # context staging
    "stage_context_paths",
    "_astage_context_paths",
]
