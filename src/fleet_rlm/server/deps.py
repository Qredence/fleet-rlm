"""FastAPI dependency injection helpers."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, AsyncGenerator, AsyncIterator

from fastapi import Depends, HTTPException, Request
from sqlmodel.ext.asyncio.session import AsyncSession

from fleet_rlm.db import DatabaseManager, FleetRepository

from .auth import AuthError, AuthProvider, NormalizedIdentity
from .config import ServerRuntimeConfig
from .database import get_db_session
from .execution_events import ExecutionEventEmitter

if TYPE_CHECKING:
    from fleet_rlm.react.agent import RLMReActChatAgent

logger = logging.getLogger(__name__)


class ServerState:
    """Shared server state, set during lifespan."""

    def __init__(self) -> None:
        self.config = ServerRuntimeConfig()
        self.planner_lm: Any | None = None
        self.sessions: dict[str, dict[str, Any]] = {}
        self.runtime_test_results: dict[str, dict[str, Any]] = {}
        self.execution_event_emitter = ExecutionEventEmitter()
        self.db_manager: DatabaseManager | None = None
        self.repository: FleetRepository | None = None
        self.auth_provider: AuthProvider | None = None

    @property
    def is_ready(self) -> bool:
        planner_ready = self.planner_lm is not None
        db_ready = (not self.config.database_required) or self.repository is not None
        return planner_ready and db_ready


server_state = ServerState()


def get_config() -> ServerRuntimeConfig:
    return server_state.config


def get_server_config(request: Request) -> ServerRuntimeConfig:
    """Compatibility alias for FastAPI DI callers that expect a request-bound getter."""
    _ = request
    return get_config()


def get_planner_lm() -> Any:
    return server_state.planner_lm


def get_db_manager() -> DatabaseManager | None:
    return server_state.db_manager


def get_repository() -> FleetRepository | None:
    return server_state.repository


def get_auth_provider() -> AuthProvider | None:
    return server_state.auth_provider


def build_unauthenticated_identity(
    config: ServerRuntimeConfig | None = None,
) -> NormalizedIdentity:
    cfg = config or server_state.config
    return NormalizedIdentity(
        tenant_claim=cfg.ws_default_workspace_id,
        user_claim=cfg.ws_default_user_id,
        name="Dev Anonymous",
        raw_claims={"auth": "disabled"},
    )


def require_repository() -> FleetRepository:
    repository = get_repository()
    if repository is None:
        raise HTTPException(
            status_code=503,
            detail="Database repository unavailable. Configure DATABASE_URL for server runtime.",
        )
    return repository


async def require_http_identity(request: Request) -> NormalizedIdentity:
    provider = get_auth_provider()
    cfg = server_state.config
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
    session: AsyncSession = Depends(get_db_session),
) -> AsyncGenerator[AsyncSession, None]:
    """Dependency for providing database sessions."""
    yield session


async def get_react_agent(
    config: ServerRuntimeConfig = Depends(get_server_config),
) -> AsyncIterator["RLMReActChatAgent"]:
    """Provide a configured RLMReActChatAgent for the request lifecycle."""
    from fleet_rlm.core.config import get_planner_lm_from_env
    from fleet_rlm.core.interpreter import ModalInterpreter
    from fleet_rlm.react.agent import RLMReActChatAgent
    from fleet_rlm.react.tools_rlm_delegate import build_rlm_delegate_tools
    import dspy

    # Use the globally configured planner_lm if available, otherwise fetch a fresh one
    planner_lm = getattr(server_state, "planner_lm", None) or get_planner_lm_from_env(
        model_name=config.agent_model
    )

    interpreter = ModalInterpreter(app_name="fleet-rlm")
    dspy.settings.configure(lm=planner_lm)
    agent = RLMReActChatAgent(interpreter=interpreter, max_depth=config.rlm_max_depth)

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
