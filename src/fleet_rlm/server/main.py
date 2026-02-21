"""FastAPI application factory with lifespan and Scalar docs."""

from contextlib import asynccontextmanager
import logging
import os

from fastapi import FastAPI, APIRouter
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path

from fleet_rlm import __version__
from fleet_rlm.analytics.client import get_posthog_client, shutdown_posthog_client
from fleet_rlm.analytics.config import PostHogConfig
from fleet_rlm.core.config import get_planner_lm_from_env
from fleet_rlm.db import DatabaseManager, FleetRepository

from .auth import build_auth_provider
from .config import ServerRuntimeConfig
from .deps import server_state
from .execution_events import ExecutionEventEmitter
from .middleware import add_middlewares
from .routers import (
    chat,
    health,
    sessions,
    tasks,
    ws,
    taxonomy,
    analytics,
    auth,
    search,
    memory,
    sandbox,
)
from .database import init_db

logger = logging.getLogger(__name__)


def _emit_posthog_startup_event(cfg: ServerRuntimeConfig) -> bool:
    """Emit a startup event when PostHog runtime analytics is configured.

    This uses the shared analytics client lifecycle so runtime and LLM analytics
    don't create duplicate PostHog clients.
    """
    posthog_cfg = PostHogConfig.from_env()
    client = get_posthog_client(posthog_cfg)
    if client is None:
        return False

    try:
        client.capture(
            "posthog_analytics_initialized",
            distinct_id=(os.getenv("POSTHOG_DISTINCT_ID") or "fleet-server").strip(),
            properties={
                "component": "server",
                "app_env": cfg.app_env,
                "auth_mode": cfg.auth_mode,
                "database_required": cfg.database_required,
                "version": __version__,
            },
        )
        return True
    except Exception:
        logger.warning("posthog_startup_event_failed", exc_info=True)
        return False


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = server_state.config
    server_state.db_manager = None
    server_state.repository = None

    if cfg.enable_legacy_sqlite_routes:
        await init_db()

    if cfg.database_url:
        db_manager = DatabaseManager(cfg.database_url, echo=cfg.db_echo)
        if cfg.db_validate_on_startup or cfg.database_required:
            await db_manager.ping()
        server_state.db_manager = db_manager
        server_state.repository = FleetRepository(db_manager)
    elif cfg.database_required:
        raise RuntimeError("DATABASE_URL is required when database_required=true")
    else:
        logger.warning(
            "runtime_persistence_disabled",
            extra={
                "database_required": cfg.database_required,
                "app_env": cfg.app_env,
            },
        )

    model_name = server_state.config.agent_model if server_state.config else None
    if model_name is None:
        server_state.planner_lm = get_planner_lm_from_env()
    else:
        server_state.planner_lm = get_planner_lm_from_env(model_name=model_name)

    _emit_posthog_startup_event(cfg)
    yield
    server_state.planner_lm = None
    shutdown_posthog_client()
    if server_state.db_manager is not None:
        await server_state.db_manager.dispose()
    server_state.db_manager = None
    server_state.repository = None


def create_app(*, config: ServerRuntimeConfig | None = None) -> FastAPI:
    cfg = config or ServerRuntimeConfig()
    cfg.validate_startup_or_raise()
    server_state.config = cfg
    server_state.execution_event_emitter = ExecutionEventEmitter(
        max_queue=cfg.ws_execution_max_queue,
        drop_policy=cfg.ws_execution_drop_policy,
    )
    server_state.auth_provider = build_auth_provider(
        auth_mode=cfg.auth_mode,
        dev_jwt_secret=cfg.dev_jwt_secret,
        allow_debug_auth=cfg.allow_debug_auth,
        allow_query_auth_tokens=cfg.allow_query_auth_tokens,
        entra_jwks_url=cfg.entra_jwks_url,
        entra_issuer=cfg.entra_issuer,
        entra_audience=cfg.entra_audience,
    )

    app = FastAPI(
        title="fleet-rlm",
        version=__version__,
        lifespan=lifespan,
    )

    add_middlewares(app, cfg)

    # Health endpoints remain un-prefixed
    app.include_router(health.router)

    # Group all API routes under /api/v1
    api_router = APIRouter(prefix="/api/v1")
    api_router.include_router(auth.router)
    api_router.include_router(chat.router)
    api_router.include_router(ws.router)
    api_router.include_router(tasks.router)
    api_router.include_router(sessions.router)
    api_router.include_router(taxonomy.router)
    api_router.include_router(analytics.router)
    api_router.include_router(search.router)
    api_router.include_router(memory.router)
    api_router.include_router(sandbox.router)

    app.include_router(api_router)

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

    # Mount and Serve Frontend
    ui_dir = Path(__file__).parent.parent / "ui" / "dist"
    if ui_dir.exists():
        assets_dir = ui_dir / "assets"
        if assets_dir.exists():
            app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

        ui_root = ui_dir.resolve()

        @app.get("/{full_path:path}", include_in_schema=False)
        async def serve_spa(full_path: str):
            # Assets are mounted at /assets via StaticFiles above.
            # All non-asset routes should return index.html for SPA routing.
            _ = full_path
            index_path = ui_root / "index.html"
            if index_path.exists():
                return FileResponse(index_path)

            return {"error": "UI build not found. Run 'bun run build' in src/frontend."}

    return app


app = create_app()
