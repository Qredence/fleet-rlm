"""FastAPI application factory with lifespan and Scalar docs."""

from contextlib import asynccontextmanager
import logging

from fastapi import Depends, FastAPI

from fleet_rlm import __version__
from fleet_rlm.core.config import get_planner_lm_from_env
from fleet_rlm.db import DatabaseManager, FleetRepository

from .auth import build_auth_provider
from .config import ServerRuntimeConfig
from .deps import require_http_identity, server_state
from .execution_events import ExecutionEventEmitter
from .middleware import add_middlewares
from .routers import auth, chat, health, sessions, tasks, ws

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = server_state.config
    if cfg.auth_mode == "entra":
        raise RuntimeError(
            "AUTH_MODE=entra is configured, but Entra JWKS verification is not wired yet. "
            "Use AUTH_MODE=dev until Entra verifier implementation is complete."
        )

    model_name = server_state.config.agent_model if server_state.config else None
    if model_name is None:
        server_state.planner_lm = get_planner_lm_from_env()
    else:
        server_state.planner_lm = get_planner_lm_from_env(model_name=model_name)
    server_state.auth_provider = build_auth_provider(
        auth_mode=cfg.auth_mode,
        dev_jwt_secret=cfg.dev_jwt_secret,
        entra_jwks_url=cfg.entra_jwks_url,
        entra_issuer=cfg.entra_issuer,
        entra_audience=cfg.entra_audience,
    )

    if cfg.database_url:
        server_state.db_manager = DatabaseManager(cfg.database_url, echo=cfg.db_echo)
        server_state.repository = FleetRepository(server_state.db_manager)
        if cfg.db_validate_on_startup:
            await server_state.db_manager.ping()
    elif cfg.db_validate_on_startup:
        raise RuntimeError(
            "DATABASE_URL is required when db_validate_on_startup=true (serve-api)."
        )

    yield
    server_state.planner_lm = None
    if server_state.db_manager is not None:
        await server_state.db_manager.dispose()
    server_state.db_manager = None
    server_state.repository = None
    server_state.auth_provider = None


def create_app(*, config: ServerRuntimeConfig | None = None) -> FastAPI:
    cfg = config or ServerRuntimeConfig()
    server_state.config = cfg
    server_state.execution_event_emitter = ExecutionEventEmitter()
    if cfg.auth_mode == "entra":
        logger.warning(
            "AUTH_MODE=entra selected. Server startup will fail until Entra JWKS verification is implemented."
        )
    server_state.auth_provider = build_auth_provider(
        auth_mode=cfg.auth_mode,
        dev_jwt_secret=cfg.dev_jwt_secret,
        entra_jwks_url=cfg.entra_jwks_url,
        entra_issuer=cfg.entra_issuer,
        entra_audience=cfg.entra_audience,
    )

    app = FastAPI(
        title="fleet-rlm",
        version=__version__,
        lifespan=lifespan,
    )

    add_middlewares(app)

    app.include_router(health.router)
    app.include_router(
        chat.router,
        dependencies=[Depends(require_http_identity)],
    )
    app.include_router(ws.router)
    app.include_router(
        tasks.router,
        dependencies=[Depends(require_http_identity)],
    )
    app.include_router(
        sessions.router,
        dependencies=[Depends(require_http_identity)],
    )
    app.include_router(
        auth.router,
        dependencies=[Depends(require_http_identity)],
    )

    try:
        from scalar_fastapi import get_scalar_api_reference

        @app.get("/scalar", include_in_schema=False)
        async def scalar_docs():
            return get_scalar_api_reference(
                openapi_url=app.openapi_url,
                title=app.title,
            )
    except ImportError:
        # Scalar docs are optional and only enabled when scalar_fastapi is installed.
        pass

    return app


app = create_app()
