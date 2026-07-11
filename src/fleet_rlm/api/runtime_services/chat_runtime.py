"""Transport-facing chat runtime preparation helpers for websocket execution."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, cast

from fleet_rlm.api.runtime_services.chat_prepare_errors import CHAT_RUNTIME_PREPARE_FAILED_MESSAGE
from fleet_rlm.integrations.database import FleetRepository
from fleet_rlm.integrations.database.repository_identity import IdentityUpsertResult
from fleet_rlm.integrations.llm_profiles.resolver import build_lm_kwargs_from_resolved, resolve_active_role_configs
from fleet_rlm.integrations.llm_profiles.store import resolve_profile_store
from fleet_rlm.runtime.events import RuntimeEvent
from fleet_rlm.runtime.execution.interpreter_protocol import ExecutionProfile
from fleet_rlm.runtime.factory import build_chat_agent
from fleet_rlm.utils.identity import sanitize_id as _sanitize_id

from ..auth import AuthError, NormalizedIdentity, resolve_admitted_identity
from ..config import ServerRuntimeConfig
from ..dependencies import ConfigDeps, DiagnosticsDeps, LmDeps, PersistenceDeps

logger = logging.getLogger(__name__)


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

    :class:`~fleet_rlm.runtime.events.RuntimeEvent` is the canonical streaming event type.
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
        selected_skill_ids: list[str] | None = None,
    ) -> AsyncIterator[RuntimeEvent]:
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


async def _resolve_identity_scoped_lms(
    *,
    cfg: ServerRuntimeConfig,
    persistence_deps: PersistenceDeps,
    identity_rows: IdentityUpsertResult | None,
) -> tuple[Any | None, Any | None]:
    if not cfg.auth_required or persistence_deps.db_manager is None or identity_rows is None:
        return None, None

    role_configs = await resolve_active_role_configs(
        resolve_profile_store(persistence_deps.db_manager, identity=identity_rows)
    )
    planner_config = role_configs.get("planner")
    if planner_config is None:
        return None, None

    import dspy

    planner_lm = await asyncio.to_thread(dspy.LM, **build_lm_kwargs_from_resolved(planner_config))
    delegate_config = role_configs.get("delegate") or role_configs.get("delegate_small")
    delegate_lm = None
    if delegate_config is not None:
        delegate_lm = await asyncio.to_thread(
            dspy.LM,
            **build_lm_kwargs_from_resolved(delegate_config, max_tokens=cfg.agent_delegate_max_tokens),
        )
    return planner_lm, delegate_lm


async def _resolve_persisted_identity(
    *,
    cfg: ServerRuntimeConfig,
    repository: FleetRepository,
    identity: NormalizedIdentity,
) -> IdentityUpsertResult:
    if cfg.auth_required:
        return await resolve_admitted_identity(repository, identity)
    return await repository.upsert_identity(
        entra_tenant_id=identity.tenant_claim,
        entra_user_id=identity.user_claim,
        email=identity.email,
        full_name=identity.name,
    )


async def prepare_chat_runtime(
    *,
    config_deps: ConfigDeps,
    lm_deps: LmDeps,
    persistence_deps: PersistenceDeps,
    diagnostics_deps: DiagnosticsDeps,
    identity: NormalizedIdentity,
    send_error: Callable[..., Awaitable[bool]],
    close_websocket: Callable[..., Awaitable[None]],
) -> PreparedChatRuntime | None:
    cfg = config_deps.config
    try:
        planner_lm, delegate_lm = await _ensure_runtime_models(lm_deps, config_deps, diagnostics_deps)
    except Exception:
        logger.exception("Planner initialization failed")
        if await send_error(
            code="planner_initialization_failed",
            message=CHAT_RUNTIME_PREPARE_FAILED_MESSAGE,
        ):
            await close_websocket(code=1011)
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
                code="tenant_forbidden" if exc.status_code == 403 else "auth_failed",
                message=exc.message,
            ):
                await close_websocket(code=1008)
            return None
    elif persistence_required:
        if await send_error(
            code="durable_state_unavailable",
            message="Database repository is required but unavailable",
        ):
            await close_websocket(code=1011)
        return None
    else:
        # Local-store mode: derive a synthetic identity from the HTTP claims
        identity_rows = await persistence.upsert_identity(
            entra_tenant_id=identity.tenant_claim,
            entra_user_id=identity.user_claim,
            email=identity.email,
            full_name=identity.name,
        )

    scoped_planner_lm, scoped_delegate_lm = await _resolve_identity_scoped_lms(
        cfg=cfg,
        persistence_deps=persistence_deps,
        identity_rows=identity_rows,
    )
    planner_lm = scoped_planner_lm or planner_lm
    delegate_lm = scoped_delegate_lm or delegate_lm

    if planner_lm is None:
        if await send_error(
            code="planner_missing",
            message=("Planner LM not configured. Check DSPY_LM_MODEL and DSPY_LLM_API_KEY env vars."),
        ):
            await close_websocket()
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
        "rlm_max_iterations": runtime.cfg.rlm_max_iterations,
        "rlm_max_llm_calls": runtime.cfg.rlm_max_llm_calls,
        "rlm_max_output_chars": runtime.cfg.agent_max_output_chars,
        "rlm_action_max_tokens": runtime.cfg.rlm_action_max_tokens,
        "planner_lm": runtime.planner_lm,
        "delegate_lm": runtime.delegate_lm,
        "repository": runtime.repository,
    }


class _ManagedAgentContext:
    """Wraps an agent so interpreter lifecycle is owned by InterpreterPool or self."""

    def __init__(
        self,
        agent: Any,
        interpreter: Any | None,
        pool: Any,
        custom_interpreter: bool = False,
    ) -> None:
        self._agent = agent
        self._interpreter = interpreter
        self._pool = pool
        self._custom_interpreter = custom_interpreter

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
            if self._custom_interpreter:
                try:
                    await self._interpreter.__aexit__(exc_type, exc_val, exc_tb)
                except Exception as exc:
                    logger.warning("Error exiting custom Daytona interpreter: %s", exc)
            else:
                await self._pool.release(self._interpreter)
        return False


async def attach_workspace_skill_activations(
    agent: Any,
    *,
    persistence: Any,
    identity_rows: IdentityUpsertResult | None,
) -> dict[str, str]:
    """Preload workspace Skill activations onto the chat agent (ADR-0006).

    Fail-closed: missing identity, unsupported local store, or resolve errors
    leave the agent on catalog defaults.
    """
    from fleet_rlm.quality.activation_resolve import (
        apply_activated_skill_markdown,
        load_workspace_skill_activation_map,
    )

    if agent is None or identity_rows is None or persistence is None:
        return {}
    tenant_id = getattr(identity_rows, "tenant_id", None)
    workspace_id = getattr(identity_rows, "workspace_id", None)
    user_id = getattr(identity_rows, "user_id", None)
    if tenant_id is None or workspace_id is None:
        return {}
    try:
        mapping = await load_workspace_skill_activation_map(
            persistence,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            created_by_user_id=user_id,
        )
    except Exception as exc:
        logger.warning("Could not load workspace skill activations: %s", type(exc).__name__)
        mapping = {}
    apply_activated_skill_markdown(agent, mapping)
    return mapping


async def build_chat_agent_context(runtime: PreparedChatRuntime, *, pool: Any | None = None) -> Any:
    kwargs = _chat_agent_builder_kwargs(runtime)

    # Check for per-user custom Daytona config
    custom_config = None
    if (
        runtime.repository is not None
        and runtime.identity_rows is not None
        and runtime.identity_rows.workspace_id is not None
    ):
        try:
            db_settings = await runtime.repository.get_workspace_runtime_setting(
                tenant_id=runtime.identity_rows.tenant_id,
                workspace_id=runtime.identity_rows.workspace_id,
            )
            api_key_encrypted = db_settings.get("DAYTONA_API_KEY", "").strip()
            api_url = db_settings.get("DAYTONA_API_URL", "").strip()
            target = db_settings.get("DAYTONA_TARGET", "").strip() or None

            if api_key_encrypted and api_url:
                from fleet_rlm.daytona.diagnostics import ResolvedDaytonaConfig
                from fleet_rlm.integrations.llm_profiles.crypto import decrypt_api_key

                secret_key = runtime.cfg.secret_encryption_key
                api_key = decrypt_api_key(api_key_encrypted, secret=secret_key)
                if api_key:
                    custom_config = ResolvedDaytonaConfig(
                        api_key=api_key,
                        api_url=api_url,
                        target=target,
                    )
        except Exception as exc:
            logger.warning("Could not load per-user Daytona configuration: %s", exc)

    if custom_config is not None:
        logger.info("Initializing custom per-user Daytona interpreter sandbox...")
        from fleet_rlm.daytona.interpreter import DaytonaInterpreter
        from fleet_rlm.daytona.sandbox import DaytonaSandboxRuntime, build_sandbox_spec

        sandbox_runtime = DaytonaSandboxRuntime(config=custom_config)
        sandbox_spec = build_sandbox_spec(
            volume_name=runtime.cfg.volume_name,
            recoverable=True,
            runner_tags=runtime.cfg.daytona_runner_tags,
        )
        interpreter = DaytonaInterpreter(
            runtime=sandbox_runtime,
            owns_runtime=True,
            volume_name=runtime.cfg.volume_name,
            timeout=runtime.cfg.timeout,
            max_llm_calls=runtime.cfg.rlm_max_llm_calls,
            max_recursion_depth=runtime.cfg.rlm_max_depth,
            rlm_max_iterations=runtime.cfg.rlm_max_iterations,
            child_isolation_mode=runtime.cfg.rlm_child_isolation_mode,
            child_fork_fallback=runtime.cfg.rlm_child_fork_fallback,
            delegate_max_calls_per_turn=runtime.cfg.delegate_max_calls_per_turn,
            delegate_result_truncation_chars=runtime.cfg.delegate_result_truncation_chars,
            delegate_execution_timeout=runtime.cfg.delegate_execution_timeout,
            delegate_max_iterations=runtime.cfg.delegate_max_iterations,
            delegate_adapter=runtime.cfg.delegate_adapter,
            broker_health_timeout=runtime.cfg.daytona_broker_health_timeout,
            broker_tool_call_timeout=runtime.cfg.daytona_broker_tool_call_timeout,
            broker_start_retries=runtime.cfg.daytona_broker_start_retries,
            async_execute=runtime.cfg.interpreter_async_execute,
            sandbox_spec=sandbox_spec,
        )
        await interpreter.__aenter__()
        kwargs["interpreter"] = interpreter
        try:
            agent = build_chat_agent(**kwargs)
            await attach_workspace_skill_activations(
                agent,
                persistence=runtime.persistence,
                identity_rows=runtime.identity_rows,
            )
        except Exception:
            await interpreter.__aexit__(None, None, None)
            raise
        return _ManagedAgentContext(agent, interpreter, pool=None, custom_interpreter=True)

    if pool is None:
        from .interpreter_pool import InterpreterPool

        pool = InterpreterPool(runtime.cfg)

    interpreter = await pool.acquire(runtime.cfg)
    if interpreter is not None:
        kwargs["interpreter"] = interpreter
    try:
        agent = build_chat_agent(**kwargs)
        await attach_workspace_skill_activations(
            agent,
            persistence=runtime.persistence,
            identity_rows=runtime.identity_rows,
        )
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
    "attach_workspace_skill_activations",
    "build_chat_agent_context",
    "new_chat_session_state",
    "prepare_chat_runtime",
    "set_interpreter_default_profile",
]
