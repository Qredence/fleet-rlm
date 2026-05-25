"""Transport-facing chat runtime preparation helpers for websocket execution."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, cast

from fastapi import WebSocket

from fleet_rlm.integrations.database import FleetRepository
from fleet_rlm.integrations.database.repository_identity import IdentityUpsertResult
from fleet_rlm.runtime.execution.interpreter_protocol import ExecutionProfile
from fleet_rlm.runtime.factory import build_chat_agent
from fleet_rlm.utils.identity import sanitize_id as _sanitize_id

from ..auth import AuthError, NormalizedIdentity, resolve_admitted_identity
from ..config import ServerRuntimeConfig
from ..dependencies import ConfigDeps, DiagnosticsDeps, LmDeps, PersistenceDeps


@dataclass(slots=True)
class PreparedChatRuntime:
    cfg: ServerRuntimeConfig
    planner_lm: object
    delegate_lm: object | None
    repository: FleetRepository | None
    persistence: Any
    persistence_required: bool
    identity_rows: IdentityUpsertResult | None


@dataclass(slots=True)
class ChatSessionState:
    canonical_workspace_id: str
    canonical_user_id: str
    owner_tenant_claim: str
    owner_user_claim: str
    cancel_flag: dict[str, bool]
    active_key: str | None = None
    active_manifest_path: str | None = None
    session_record: dict[str, object] | None = None
    active_run_db_id: uuid.UUID | None = None
    lifecycle: Any | None = None
    last_loaded_docs_path: str | None = None
    orchestration_session: Any | None = None


LocalPersistFn = Callable[..., Awaitable[None]]
PreStreamSetupFn = Callable[[], Awaitable[None]]


@dataclass(slots=True)
class SessionContext:
    """Simplified session context for websocket streaming."""

    workspace_id: str | None = None
    user_id: str | None = None
    session_id: str | None = None
    session_record: dict[str, Any] | None = None


class StreamEventLike(Protocol):
    """Minimal worker-event surface required by WS terminal/completion helpers.

    ``WorkspaceEvent`` satisfies this protocol directly, and transport-created
    compatibility events can do the same without depending on runtime models.
    """

    @property
    def kind(self) -> str:
        raise NotImplementedError

    @property
    def text(self) -> str:
        pass

    @property
    def payload(self) -> dict[str, Any]:
        raise NotImplementedError

    @property
    def timestamp(self) -> datetime:
        raise NotImplementedError


class MaintenanceInterpreterProtocol(Protocol):
    """Interpreter capability needed for session manifest volume I/O."""

    # Host-mediated evidence bridge references — populated by the WS stream
    # layer once identity is resolved; read by integrations.daytona.isolation.
    _host_repository: Any | None
    _host_identity: Any | None
    _host_run_id: Any | None

    async def aexecute(
        self,
        code: str,
        variables: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> object:
        pass

    def execution_profile(self, profile: object) -> AbstractContextManager[object]:
        pass


class ChatAgentProtocol(Protocol):
    """Subset of chat-agent behavior used by websocket runtime helpers."""

    interpreter: MaintenanceInterpreterProtocol | None
    _db_session_id: str | object | None
    _repository: Any | None
    _identity_rows: Any | None

    async def __aenter__(self) -> ChatAgentProtocol:
        pass

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object | None,
    ) -> bool:
        pass

    def history_turns(self) -> int:
        pass

    def set_execution_mode(self, execution_mode: str) -> None:
        pass

    def aiter_chat_turn_stream(
        self,
        message: str,
        trace: bool = True,
        cancel_check: Callable[[], bool] | None = None,
        *,
        docs_path: str | None = None,
        repo_url: str | None = None,
        repo_ref: str | None = None,
        context_paths: list[str] | None = None,
        batch_concurrency: int | None = None,
        volume_name: str | None = None,
    ) -> AsyncIterator[object]:
        pass

    def load_document(self, path: str, alias: str = "active") -> None:
        pass

    def export_session_state(self) -> dict[str, Any]:
        pass

    def import_session_state(self, state: dict[str, Any]) -> object:
        pass

    async def aimport_session_state(self, state: dict[str, Any]) -> object:
        pass

    def reset(self, *, clear_sandbox_buffers: bool = True) -> object:
        pass

    async def areset(self, *, clear_sandbox_buffers: bool = True) -> object:
        pass

    async def execute_command(self, command: str, args: dict[str, Any]) -> dict[str, Any] | object:
        pass


def set_interpreter_default_profile(interpreter: object | None, cfg: ServerRuntimeConfig) -> None:
    if interpreter is None:
        return
    runtime_interpreter = cast(Any, interpreter)
    try:
        runtime_interpreter.default_execution_profile = ExecutionProfile(cfg.ws_default_execution_profile)
    except ValueError:
        runtime_interpreter.default_execution_profile = ExecutionProfile.ROOT_INTERLOCUTOR


async def _ensure_runtime_models(
    lm_deps: LmDeps, config_deps: ConfigDeps, diagnostics_deps: Any
) -> tuple[Any | None, Any | None]:
    from ..bootstrap import ensure_runtime_models

    return await ensure_runtime_models(lm_deps, config_deps, diagnostics_deps)


async def _resolve_persisted_identity(
    *,
    cfg: ServerRuntimeConfig,
    repository: FleetRepository,
    identity: NormalizedIdentity,
) -> IdentityUpsertResult:
    if cfg.auth_mode == "entra":
        return await resolve_admitted_identity(repository, identity)
    return await repository.upsert_identity(
        entra_tenant_id=identity.tenant_claim,
        entra_user_id=identity.user_claim,
        email=identity.email,
        full_name=identity.name,
    )


async def prepare_chat_runtime(
    *,
    websocket: WebSocket,
    config_deps: ConfigDeps,
    lm_deps: LmDeps,
    persistence_deps: PersistenceDeps,
    diagnostics_deps: DiagnosticsDeps,
    identity: NormalizedIdentity,
    send_error,
    close_websocket,
) -> PreparedChatRuntime | None:
    cfg = config_deps.config
    try:
        planner_lm, delegate_lm = await _ensure_runtime_models(lm_deps, config_deps, diagnostics_deps)
    except Exception as exc:
        if await send_error(
            websocket,
            code="planner_initialization_failed",
            message=f"Planner initialization failed: {exc}",
        ):
            await close_websocket(websocket, code=1011)
        return None

    repository = persistence_deps.repository
    persistence = repository
    if persistence is None:
        persistence = persistence_deps.local_store
    if persistence is None:
        from fleet_rlm.integrations.local_store import LocalStore

        persistence = LocalStore()
    persistence_required = cfg.database_required
    identity_rows = None

    if repository is not None:
        try:
            identity_rows = await _resolve_persisted_identity(
                cfg=cfg,
                repository=repository,
                identity=identity,
            )
        except AuthError as exc:
            if await send_error(
                websocket,
                code="tenant_forbidden" if exc.status_code == 403 else "auth_failed",
                message=exc.message,
            ):
                await close_websocket(websocket, code=1008)
            return None
    elif persistence_required:
        if await send_error(
            websocket,
            code="durable_state_unavailable",
            message="Database repository is required but unavailable",
        ):
            await close_websocket(websocket, code=1011)
        return None
    else:
        # Local-store mode: derive a synthetic identity from the HTTP claims
        identity_rows = await persistence.upsert_identity(
            entra_tenant_id=identity.tenant_claim,
            entra_user_id=identity.user_claim,
            email=identity.email,
            full_name=identity.name,
        )

    if planner_lm is None:
        if await send_error(
            websocket,
            code="planner_missing",
            message=("Planner LM not configured. Check DSPY_LM_MODEL and DSPY_LLM_API_KEY env vars."),
        ):
            await close_websocket(websocket)
        return None

    return PreparedChatRuntime(
        cfg=cfg,
        planner_lm=planner_lm,
        delegate_lm=delegate_lm,
        repository=repository,
        persistence=persistence,
        persistence_required=persistence_required,
        identity_rows=identity_rows,
    )


def _chat_agent_builder_kwargs(runtime: PreparedChatRuntime) -> dict[str, Any]:
    return {
        "react_max_iters": runtime.cfg.react_max_iters,
        "planner_lm": runtime.planner_lm,
        "delegate_lm": runtime.delegate_lm,
        "repository": runtime.repository,
    }


class _ManagedAgentContext:
    """Wraps an agent so interpreter lifecycle is owned by InterpreterPool."""

    def __init__(
        self,
        agent: Any,
        interpreter: Any | None,
        pool: Any,
    ) -> None:
        self._agent = agent
        self._interpreter = interpreter
        self._pool = pool

    async def __aenter__(self) -> Any:
        return self._agent

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any | None,
    ) -> bool:
        if self._interpreter is not None:
            self._agent.interpreter = None
            await self._pool.release(self._interpreter)
        return False


async def build_chat_agent_context(runtime: PreparedChatRuntime, *, pool: Any | None = None) -> Any:
    kwargs = _chat_agent_builder_kwargs(runtime)
    if pool is None:
        from .interpreter_pool import InterpreterPool

        pool = InterpreterPool(runtime.cfg)
    interpreter = await pool.acquire(runtime.cfg)
    if interpreter is not None:
        kwargs["interpreter"] = interpreter
    try:
        agent = build_chat_agent(**kwargs)
    except Exception:
        await pool.release(interpreter)
        raise
    return _ManagedAgentContext(agent, interpreter, pool)


def new_chat_session_state(runtime: PreparedChatRuntime, identity: NormalizedIdentity) -> ChatSessionState:
    return ChatSessionState(
        canonical_workspace_id=_sanitize_id(identity.tenant_claim, runtime.cfg.ws_default_workspace_id),
        canonical_user_id=_sanitize_id(identity.user_claim, runtime.cfg.ws_default_user_id),
        owner_tenant_claim=identity.tenant_claim,
        owner_user_claim=identity.user_claim,
        cancel_flag={"cancelled": False},
    )


__all__ = [
    "ChatAgentProtocol",
    "ChatSessionState",
    "LocalPersistFn",
    "MaintenanceInterpreterProtocol",
    "PreStreamSetupFn",
    "PreparedChatRuntime",
    "SessionContext",
    "StreamEventLike",
    "build_chat_agent_context",
    "new_chat_session_state",
    "prepare_chat_runtime",
    "set_interpreter_default_profile",
]
