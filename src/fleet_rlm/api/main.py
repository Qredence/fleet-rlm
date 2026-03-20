"""FastAPI application factory with lifespan and Scalar docs."""

import logging
import os
import subprocess
from contextlib import asynccontextmanager
from importlib import import_module
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import APIRouter, FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from fleet_rlm import __version__
from fleet_rlm.core.config import get_delegate_lm_from_env, get_planner_lm_from_env
from fleet_rlm.features.analytics import initialize_mlflow, shutdown_mlflow
from fleet_rlm.features.analytics.client import (
    get_posthog_client,
    shutdown_posthog_client,
)
from fleet_rlm.features.analytics.config import MlflowConfig, PostHogConfig
from fleet_rlm.infrastructure.config.runtime_settings import resolve_env_path
from fleet_rlm.infrastructure.database import DatabaseManager, FleetRepository

from .auth import build_auth_provider
from .config import ServerRuntimeConfig
from .dependencies import ServerState
from .execution import ExecutionEventEmitter
from .middleware import add_middlewares
from .routers import (
    auth,
    health,
    runtime,
    sessions,
    traces,
    ws,
)

logger = logging.getLogger(__name__)

_MLFLOW_SERVER_PROCESS: subprocess.Popen | None = None


def _start_mlflow_server(cfg: ServerRuntimeConfig) -> subprocess.Popen | None:
    """Start a local MLflow tracking server if configured and not already running.

    Returns the subprocess handle if a server was started, None otherwise.
    """
    mlflow_cfg = MlflowConfig.from_env()

    if not mlflow_cfg.enabled:
        logger.info("MLflow integration disabled; skipping server startup.")
        return None

    tracking_uri = mlflow_cfg.tracking_uri.strip()

    # Only auto-start for localhost URIs
    if not tracking_uri.startswith("http://127.0.0.1") and not tracking_uri.startswith(
        "http://localhost"
    ):
        logger.debug(
            "MLflow tracking URI is not localhost; skipping auto-start: %s",
            tracking_uri,
        )
        return None

    # Parse port from URI
    try:
        import re

        match = re.search(r":(\d+)", tracking_uri)
        port = int(match.group(1)) if match else 5001
    except (ValueError, AttributeError):
        port = 5001

    # Check if server is already running
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(1)
    try:
        result = sock.connect_ex(("127.0.0.1", port))
        if result == 0:
            logger.info("MLflow tracking server already running at %s", tracking_uri)
            return None
    finally:
        sock.close()

    # Start the MLflow server
    logger.info("Starting MLflow tracking server on port %d...", port)
    try:
        # Don't capture stdout/stderr - let MLflow write to its own logs
        proc = subprocess.Popen(
            [
                "uv",
                "run",
                "mlflow",
                "server",
                "--backend-store-uri",
                "sqlite:///mlruns.db",
                "--port",
                str(port),
            ],
            start_new_session=True,
        )

        # Wait for server to become ready (MLflow takes ~12-15s to initialize workers)
        import time

        max_attempts = 20
        for attempt in range(max_attempts):
            time.sleep(1)
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            try:
                result = sock.connect_ex(("127.0.0.1", port))
                if result == 0:
                    logger.info(
                        "MLflow tracking server started (pid=%d) at %s",
                        proc.pid,
                        tracking_uri,
                    )
                    return proc
            finally:
                sock.close()
            logger.info(
                "Waiting for MLflow server... (attempt %d/%d)",
                attempt + 1,
                max_attempts,
            )

        # Server didn't start in time
        logger.warning(
            "MLflow server process started but port %d not responding after %ds",
            port,
            max_attempts,
        )
        proc.terminate()
        return None
    except Exception:
        logger.warning("Failed to start MLflow tracking server", exc_info=True)
        return None


def _prime_runtime_env(cfg: ServerRuntimeConfig) -> None:
    """Load configured .env into process env before runtime initialization.

    Local app sessions prioritize `.env` values to prevent stale inherited shell
    variables from overriding persisted runtime settings.
    """
    load_dotenv(
        dotenv_path=str(cfg.env_path),
        override=cfg.app_env == "local",
    )


def _resolve_ui_dist_dir() -> Path | None:
    """Return the frontend build directory if one exists.

    In source checkouts, prefer `src/frontend/dist` so `fleet web` reflects the
    latest local frontend build. For installed packages, fall back to in-package
    assets at `fleet_rlm/ui/dist`.
    """
    repo_root = Path(__file__).resolve().parents[3]
    candidates = [
        repo_root / "src" / "frontend" / "dist",  # current repo layout
        Path(__file__).parent.parent / "ui" / "dist",  # packaged fallback
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


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


def _build_server_state(cfg: ServerRuntimeConfig) -> ServerState:
    """Build initialized in-memory server state container."""
    state = ServerState(
        config=cfg,
        execution_event_emitter=ExecutionEventEmitter(
            max_queue=cfg.ws_execution_max_queue,
            drop_policy=cfg.ws_execution_drop_policy,
        ),
    )
    state.runtime_test_results = {}
    state.auth_provider = build_auth_provider(
        auth_mode=cfg.auth_mode,
        dev_jwt_secret=cfg.dev_jwt_secret,
        allow_debug_auth=cfg.allow_debug_auth,
        allow_query_auth_tokens=cfg.allow_query_auth_tokens,
        entra_jwks_url=cfg.entra_jwks_url,
        entra_issuer_template=cfg.entra_issuer_template,
        entra_audience=cfg.entra_audience,
    )
    state.db_manager = None
    state.repository = None
    return state


async def _initialize_persistence(state: ServerState, cfg: ServerRuntimeConfig) -> None:
    """Initialize Neon persistence paths based on runtime config."""
    if cfg.database_url:
        db_manager = DatabaseManager(cfg.database_url, echo=cfg.db_echo)
        if cfg.db_validate_on_startup or cfg.database_required:
            await db_manager.ping()
        state.db_manager = db_manager
        state.repository = FleetRepository(db_manager)
        return

    if cfg.database_required:
        raise RuntimeError("DATABASE_URL is required when database_required=true")

    logger.warning(
        "runtime_persistence_disabled",
        extra={
            "database_required": cfg.database_required,
            "app_env": cfg.app_env,
        },
    )


def _initialize_lms(state: ServerState, cfg: ServerRuntimeConfig) -> None:
    """Load planner/delegate LMs into process state."""
    model_name = cfg.agent_model
    if model_name is None:
        state.planner_lm = get_planner_lm_from_env(env_file=cfg.env_path)
    else:
        state.planner_lm = get_planner_lm_from_env(
            env_file=cfg.env_path,
            model_name=model_name,
        )
    state.delegate_lm = get_delegate_lm_from_env(
        env_file=cfg.env_path,
        model_name=cfg.agent_delegate_model,
        default_max_tokens=cfg.agent_delegate_max_tokens,
    )


def _register_api_routes(app: FastAPI) -> None:
    """Register health and /api/v1 route groups on app."""
    app.include_router(health.router)

    api_router = APIRouter(prefix="/api/v1")
    api_router.include_router(auth.router)
    api_router.include_router(ws.router)
    api_router.include_router(sessions.router)
    api_router.include_router(runtime.router)
    api_router.include_router(traces.router)
    app.include_router(api_router)


def _mount_spa(app: FastAPI, ui_dir: Path) -> None:
    """Mount built frontend assets and SPA fallback route."""
    assets_dir = ui_dir / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")
    branding_dir = ui_dir / "branding"
    if branding_dir.exists():
        app.mount(
            "/branding", StaticFiles(directory=str(branding_dir)), name="branding"
        )

    ui_root = ui_dir.resolve()

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str):
        _ = full_path
        index_path = ui_root / "index.html"
        if index_path.exists():
            return FileResponse(index_path)

        return {"error": "UI build not found. Run 'pnpm run build' in src/frontend."}


def create_app(*, config: ServerRuntimeConfig | None = None) -> FastAPI:
    if config is None:
        env_path = resolve_env_path(
            start_paths=[
                Path(__file__).resolve().parent,
                Path.cwd(),
            ]
        )
        app_env = (os.getenv("APP_ENV") or "local").strip().lower()
        load_dotenv(dotenv_path=str(env_path), override=app_env == "local")
        cfg = ServerRuntimeConfig(env_path=env_path)
    else:
        cfg = config

    cfg.validate_startup_or_raise()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        global _MLFLOW_SERVER_PROCESS
        state = _build_server_state(cfg)
        app.state.server_state = state

        _prime_runtime_env(cfg)

        # Start MLflow server if enabled and not already running
        _MLFLOW_SERVER_PROCESS = _start_mlflow_server(cfg)

        # Initialize MLflow integration
        initialize_mlflow(MlflowConfig.from_env())

        await _initialize_persistence(state, cfg)
        _initialize_lms(state, cfg)

        _emit_posthog_startup_event(cfg)
        yield
        state.planner_lm = None
        state.delegate_lm = None
        shutdown_mlflow()
        shutdown_posthog_client()

        # Stop MLflow server if we started it
        if _MLFLOW_SERVER_PROCESS is not None:
            logger.info(
                "Stopping MLflow tracking server (pid=%d)...",
                _MLFLOW_SERVER_PROCESS.pid,
            )
            _MLFLOW_SERVER_PROCESS.terminate()
            try:
                _MLFLOW_SERVER_PROCESS.wait(timeout=5)
            except subprocess.TimeoutExpired:
                _MLFLOW_SERVER_PROCESS.kill()
            _MLFLOW_SERVER_PROCESS = None

        if state.db_manager is not None:
            await state.db_manager.dispose()
        state.db_manager = None
        state.repository = None

    app = FastAPI(
        title="fleet-rlm",
        version=__version__,
        lifespan=lifespan,
    )

    add_middlewares(app, cfg)
    _register_api_routes(app)

    try:
        get_scalar_api_reference: Any = import_module(
            "scalar_fastapi"
        ).get_scalar_api_reference

        @app.get("/scalar", include_in_schema=False)
        async def scalar_docs():
            return get_scalar_api_reference(
                openapi_url=app.openapi_url,
                title=app.title,
            )
    except ImportError:
        # Scalar docs are optional and only enabled when scalar_fastapi is installed.
        pass

    ui_dir = _resolve_ui_dist_dir()
    if ui_dir is not None:
        _mount_spa(app, ui_dir)

    return app


app = create_app()
