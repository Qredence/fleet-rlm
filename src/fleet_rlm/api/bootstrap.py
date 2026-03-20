"""Runtime bootstrap helpers for the FastAPI server."""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

from fleet_rlm import __version__
from fleet_rlm.core.config import get_delegate_lm_from_env, get_planner_lm_from_env
from fleet_rlm.features.analytics.mlflow_runtime import (
    initialize_mlflow,
    shutdown_mlflow,
)
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

logger = logging.getLogger(__name__)

_MLFLOW_SERVER_PROCESS: subprocess.Popen | None = None


def resolve_runtime_config(
    config: ServerRuntimeConfig | None = None,
) -> ServerRuntimeConfig:
    """Resolve the runtime config, loading `.env` when needed."""
    if config is not None:
        return config

    env_path = resolve_env_path(
        start_paths=[
            Path(__file__).resolve().parent,
            Path.cwd(),
        ]
    )
    app_env = (os.getenv("APP_ENV") or "local").strip().lower()
    load_dotenv(dotenv_path=str(env_path), override=app_env == "local")
    return ServerRuntimeConfig(env_path=env_path)


def prime_runtime_env(cfg: ServerRuntimeConfig) -> None:
    """Load configured .env into process env before runtime initialization."""
    load_dotenv(
        dotenv_path=str(cfg.env_path),
        override=cfg.app_env == "local",
    )


async def start_mlflow_server(cfg: ServerRuntimeConfig) -> subprocess.Popen | None:
    """Start a local MLflow tracking server if configured and not already running."""
    mlflow_cfg = MlflowConfig.from_env()

    if not mlflow_cfg.enabled:
        logger.info("MLflow integration disabled; skipping server startup.")
        return None

    tracking_uri = mlflow_cfg.tracking_uri.strip()
    if not tracking_uri.startswith("http://127.0.0.1") and not tracking_uri.startswith(
        "http://localhost"
    ):
        logger.debug(
            "MLflow tracking URI is not localhost; skipping auto-start: %s",
            tracking_uri,
        )
        return None

    try:
        import re

        match = re.search(r":(\d+)", tracking_uri)
        port = int(match.group(1)) if match else 5001
    except (ValueError, AttributeError):
        port = 5001

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(1)
    try:
        result = sock.connect_ex(("127.0.0.1", port))
        if result == 0:
            logger.info("MLflow tracking server already running at %s", tracking_uri)
            return None
    finally:
        sock.close()

    logger.info("Starting MLflow tracking server on port %d...", port)
    try:
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "mlflow",
                "server",
                "--backend-store-uri",
                "sqlite:///mlruns.db",
                "--port",
                str(port),
            ],
            start_new_session=True,
        )

        max_attempts = 20
        for attempt in range(max_attempts):
            await asyncio.sleep(1)
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

        logger.warning(
            "MLflow server process started but port %d not responding after %ds",
            port,
            max_attempts,
        )
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            logger.warning(
                "MLflow server process (pid=%d) did not exit after terminate(); "
                "sending kill()",
                proc.pid,
            )
            proc.kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                logger.warning(
                    "MLflow server process (pid=%d) did not exit promptly after kill()",
                    proc.pid,
                )
        return None
    except Exception:
        logger.warning("Failed to start MLflow tracking server", exc_info=True)
        return None


def emit_posthog_startup_event(cfg: ServerRuntimeConfig) -> bool:
    """Emit a startup event when PostHog runtime analytics is configured."""
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


def build_server_state(cfg: ServerRuntimeConfig) -> ServerState:
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


async def initialize_persistence(state: ServerState, cfg: ServerRuntimeConfig) -> None:
    """Initialize persistence paths based on runtime config."""
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


def initialize_lms(state: ServerState, cfg: ServerRuntimeConfig) -> None:
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


async def startup_server_state(state: ServerState, cfg: ServerRuntimeConfig) -> None:
    """Run startup initialization for server state and runtime services."""
    global _MLFLOW_SERVER_PROCESS

    prime_runtime_env(cfg)
    _MLFLOW_SERVER_PROCESS = await start_mlflow_server(cfg)
    initialize_mlflow(MlflowConfig.from_env())
    await initialize_persistence(state, cfg)
    initialize_lms(state, cfg)
    emit_posthog_startup_event(cfg)


async def shutdown_server_state(state: ServerState) -> None:
    """Tear down runtime services and persistence resources."""
    global _MLFLOW_SERVER_PROCESS

    state.planner_lm = None
    state.delegate_lm = None
    shutdown_mlflow()
    shutdown_posthog_client()

    if _MLFLOW_SERVER_PROCESS is not None:
        proc = _MLFLOW_SERVER_PROCESS
        _MLFLOW_SERVER_PROCESS = None
        logger.info(
            "Stopping MLflow tracking server (pid=%d)...",
            proc.pid,
        )
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
        except ProcessLookupError:
            logger.debug("MLflow tracking server process already exited")

    if state.db_manager is not None:
        await state.db_manager.dispose()
    state.db_manager = None
    state.repository = None
