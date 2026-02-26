"""FastAPI dependency injection helpers."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, AsyncGenerator, AsyncIterator

from fastapi import Depends, HTTPException, Request, WebSocket
from sqlmodel.ext.asyncio.session import AsyncSession

from fleet_rlm.db import DatabaseManager, FleetRepository

from .auth import AuthError, AuthProvider, NormalizedIdentity
from .config import ServerRuntimeConfig
from .legacy_compat import get_db_session
from .execution_events import ExecutionEventEmitter

if TYPE_CHECKING:
    from fleet_rlm.react.agent import RLMReActChatAgent

logger = logging.getLogger(__name__)


class ServerState:
    """Shared server state, set during lifespan."""

    def __init__(
        self,
        *,
        config: ServerRuntimeConfig | None = None,
        execution_event_emitter: ExecutionEventEmitter | None = None,
    ) -> None:
        self.config = config or ServerRuntimeConfig()
        self.planner_lm: Any | None = None
        self.delegate_lm: Any | None = None
        self.sessions: dict[str, dict[str, Any]] = {}
        self.runtime_test_results: dict[str, dict[str, Any]] = {}
        self.execution_event_emitter = (
            execution_event_emitter or ExecutionEventEmitter()
        )
        self.db_manager: DatabaseManager | None = None
        self.repository: FleetRepository | None = None
        self.auth_provider: AuthProvider | None = None

    @property
    def is_ready(self) -> bool:
        planner_ready = self.planner_lm is not None
        db_ready = not self.config.database_required or self.repository is not None
        return planner_ready and db_ready


def _require_server_state(app: Any) -> ServerState:
    candidate = getattr(getattr(app, "state", None), "server_state", None)
    if isinstance(candidate, ServerState):
        return candidate
    raise RuntimeError(
        "Server state is not initialized. Ensure FastAPI lifespan startup has completed."
    )


def get_server_state(request: Request) -> ServerState:
    try:
        return _require_server_state(request.app)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def get_server_state_from_websocket(websocket: WebSocket) -> ServerState:
    return _require_server_state(websocket.app)


def get_config(request: Request) -> ServerRuntimeConfig:
    return get_server_state(request).config


def get_server_config(request: Request) -> ServerRuntimeConfig:
    """Compatibility alias for FastAPI DI callers that expect a request-bound getter."""
    return get_config(request)


def get_planner_lm(request: Request) -> Any:
    return get_server_state(request).planner_lm


def get_delegate_lm(request: Request) -> Any:
    return get_server_state(request).delegate_lm


def get_db_manager(request: Request) -> DatabaseManager | None:
    return get_server_state(request).db_manager


def get_repository(request: Request) -> FleetRepository | None:
    return get_server_state(request).repository


def get_auth_provider(request: Request) -> AuthProvider | None:
    return get_server_state(request).auth_provider


def build_unauthenticated_identity(
    config: ServerRuntimeConfig | None = None,
) -> NormalizedIdentity:
    cfg = config or ServerRuntimeConfig()
    return NormalizedIdentity(
        tenant_claim=cfg.ws_default_workspace_id,
        user_claim=cfg.ws_default_user_id,
        name="Dev Anonymous",
        raw_claims={"auth": "disabled"},
    )


def require_repository(request: Request) -> FleetRepository:
    repository = get_repository(request)
    if repository is None:
        raise HTTPException(
            status_code=503,
            detail="Database repository unavailable. Configure DATABASE_URL for server runtime.",
        )
    return repository


async def require_http_identity(request: Request) -> NormalizedIdentity:
    state = get_server_state(request)
    provider = state.auth_provider
    cfg = state.config
    if provider is None:
        if cfg.auth_required:
            raise HTTPException(
                status_code=503, detail="Auth provider is not configured"
            )
        identity = build_unauthenticated_identity(cfg)
        request.state.identity = identity
        return identity
    try:
        identity = await provider.authenticate_http(request)
    except AuthError as exc:
        if cfg.auth_required:
            raise HTTPException(
                status_code=exc.status_code, detail=exc.message
            ) from exc
        logger.debug("HTTP auth optional; continuing without auth: %s", exc.message)
        identity = build_unauthenticated_identity(cfg)
    request.state.identity = identity
    return identity


def get_request_identity(request: Request) -> NormalizedIdentity | None:
    identity = getattr(request.state, "identity", None)
    if isinstance(identity, NormalizedIdentity):
        return identity
    return None


async def get_db(
    request: Request,
) -> AsyncGenerator[AsyncSession, None]:
    """Dependency for providing database sessions."""
    try:
        async for session in get_db_session():
            yield session
    except RuntimeError as exc:
        path = request.url.path
        if "/tasks" in path:
            detail = (
                "Legacy SQLite task routes are disabled. "
                "Use Neon-backed runtime APIs instead."
            )
        elif "/sessions" in path:
            detail = (
                "Legacy SQLite session routes are disabled. "
                "Use WS session state and Neon-backed APIs instead."
            )
        else:
            detail = str(exc)
        raise HTTPException(status_code=410, detail=detail) from exc


async def get_react_agent(
    request: Request,
    config: ServerRuntimeConfig = Depends(get_server_config),
) -> AsyncIterator["RLMReActChatAgent"]:
    """Provide a configured RLMReActChatAgent for the request lifecycle."""
    from fleet_rlm.core.config import get_delegate_lm_from_env, get_planner_lm_from_env
    from fleet_rlm.core.interpreter import ModalInterpreter
    from fleet_rlm.react.agent import RLMReActChatAgent
    from fleet_rlm.react.tools_rlm_delegate import build_rlm_delegate_tools
    import dspy

    state = get_server_state(request)

    # Use the globally configured planner_lm if available, otherwise fetch a fresh one
    planner_lm = state.planner_lm or get_planner_lm_from_env(
        model_name=config.agent_model
    )
    if planner_lm is None:
        raise HTTPException(status_code=503, detail="Planner LM not configured")
    delegate_lm = state.delegate_lm or get_delegate_lm_from_env(
        model_name=config.agent_delegate_model,
        default_max_tokens=config.agent_delegate_max_tokens,
    )

    interpreter = ModalInterpreter(app_name="fleet-rlm")
    dspy.settings.configure(lm=planner_lm)
    agent = RLMReActChatAgent(
        interpreter=interpreter,
        max_depth=config.rlm_max_depth,
        react_max_iters=config.react_max_iters,
        deep_react_max_iters=config.deep_react_max_iters,
        enable_adaptive_iters=config.enable_adaptive_iters,
        rlm_max_iterations=config.rlm_max_iterations,
        rlm_max_llm_calls=config.rlm_max_llm_calls,
        interpreter_async_execute=config.interpreter_async_execute,
        guardrail_mode=config.agent_guardrail_mode,
        max_output_chars=config.agent_max_output_chars,
        min_substantive_chars=config.agent_min_substantive_chars,
        delegate_lm=delegate_lm,
        delegate_max_calls_per_turn=config.delegate_max_calls_per_turn,
        delegate_result_truncation_chars=config.delegate_result_truncation_chars,
    )

    # Lazily mount tools to prevent circular module dependencies during import
    from fleet_rlm.react import tools_sandbox

    agent.tools.extend(tools_sandbox.build_sandbox_tools(agent))
    agent.tools.extend(build_rlm_delegate_tools(agent))

    try:
        yield agent
    finally:
        if agent.interpreter:
            try:
                agent.interpreter.shutdown()
            except Exception as e:
                logging.getLogger("fleet_rlm").error(
                    "Failed to shutdown interpreter: %s", e
                )


def session_key(workspace_id: str, user_id: str, session_id: str | None = None) -> str:
    """Build a stable in-memory key for a stateful user/workspace session."""
    resolved_session_id = (session_id or "").strip() or "__default__"
    return f"{workspace_id}:{user_id}:{resolved_session_id}"
