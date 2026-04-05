"""Observability-specific bootstrap helpers for the FastAPI server."""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

from fleet_rlm import __version__
from fleet_rlm.integrations.observability.config import MlflowConfig, PostHogConfig

from .dependencies import ServerState

logger = logging.getLogger(__name__)

_MLFLOW_AUTO_START_TRUTHY = {"1", "true", "yes"}
_MLFLOW_AUTO_START_FALSEY = {"0", "false", "no"}
_MLFLOW_STARTUP_TIMEOUT_SECONDS = 60
_MLFLOW_STARTUP_POLL_INTERVAL_SECONDS = 1


def set_optional_service_status(
    state: ServerState,
    service: str,
    status: str,
    *,
    error: str | None = None,
) -> None:
    """Update optional-service readiness/error state."""
    state.optional_service_status[service] = status
    if error:
        state.optional_service_errors[service] = error
    else:
        state.optional_service_errors.pop(service, None)


def terminate_process(proc: subprocess.Popen) -> None:
    """Terminate a subprocess, escalating to kill() if needed, then reap it."""
    try:
        if proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            logger.warning(
                "Process (pid=%d) did not exit after terminate(); sending kill()",
                proc.pid,
            )
            proc.kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                logger.warning(
                    "Process (pid=%d) did not exit promptly after kill()",
                    proc.pid,
                )
    except ProcessLookupError:
        logger.debug("Process (pid=%d) already exited", proc.pid)


def is_local_mlflow_tracking_uri(tracking_uri: str) -> bool:
    """Return whether *tracking_uri* targets the local MLflow OSS server."""

    candidate = tracking_uri.strip()
    if not candidate:
        return False

    try:
        parsed = urlsplit(candidate)
    except ValueError:
        return False

    return parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}


def _mlflow_startup_socket_ready(*, port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(1)
    try:
        return sock.connect_ex(("127.0.0.1", port)) == 0
    finally:
        sock.close()


def resolve_mlflow_auto_start_enabled(
    *,
    app_env: str,
    mlflow_enabled: bool,
    tracking_uri: str,
    auto_start_env: str | None = None,
) -> bool:
    """Return the effective MLflow auto-start decision for the current runtime."""

    raw_value = (
        auto_start_env if auto_start_env is not None else os.getenv("MLFLOW_AUTO_START")
    )
    normalized = (raw_value or "").strip().lower()
    if normalized in _MLFLOW_AUTO_START_TRUTHY:
        return True
    if normalized in _MLFLOW_AUTO_START_FALSEY:
        return False

    return bool(
        mlflow_enabled
        and app_env == "local"
        and is_local_mlflow_tracking_uri(tracking_uri)
    )


async def start_mlflow_server(
    *,
    app_env: str,
    tracking_uri: str,
) -> subprocess.Popen | None:
    """Start a local MLflow tracking server if configured and not already running."""
    tracking_uri = tracking_uri.strip()
    if not is_local_mlflow_tracking_uri(tracking_uri):
        logger.debug(
            "MLflow tracking URI is not localhost; skipping auto-start: %s",
            tracking_uri,
        )
        return None

    if app_env != "local":
        return None

    try:
        import re

        match = re.search(r":(\d+)", tracking_uri)
        port = int(match.group(1)) if match else 5001
    except (ValueError, AttributeError):
        port = 5001

    if _mlflow_startup_socket_ready(port=port):
        logger.info("MLflow tracking server already running at %s", tracking_uri)
        return None

    logger.info("Starting MLflow tracking server on port %d...", port)
    try:
        Path(".data").mkdir(exist_ok=True)
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "mlflow",
                "server",
                "--backend-store-uri",
                "sqlite:///.data/mlruns.db",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--workers",
                "1",
            ],
            start_new_session=True,
        )

        attempts = max(
            1, _MLFLOW_STARTUP_TIMEOUT_SECONDS // _MLFLOW_STARTUP_POLL_INTERVAL_SECONDS
        )
        for attempt in range(attempts):
            await asyncio.sleep(_MLFLOW_STARTUP_POLL_INTERVAL_SECONDS)
            exit_code = proc.poll()
            if exit_code is not None:
                logger.warning(
                    "MLflow server exited before becoming ready (pid=%d, exit_code=%d).",
                    proc.pid,
                    exit_code,
                )
                return None
            if _mlflow_startup_socket_ready(port=port):
                logger.info(
                    "MLflow tracking server started (pid=%d) at %s",
                    proc.pid,
                    tracking_uri,
                )
                return proc
            logger.info(
                "Waiting for MLflow server... (attempt %d/%d)",
                attempt + 1,
                attempts,
            )

        logger.warning(
            "MLflow server process started but port %d not responding after %ds",
            port,
            _MLFLOW_STARTUP_TIMEOUT_SECONDS,
        )
        terminate_process(proc)
        return None
    except (ValueError, OSError):
        logger.warning("Failed to start MLflow tracking server", exc_info=True)
        return None


def emit_posthog_startup_event(
    *, app_env: str, auth_mode: str, database_required: bool
) -> bool:
    """Emit a startup event when PostHog runtime analytics is configured."""
    posthog_cfg = PostHogConfig.from_env()
    from fleet_rlm.integrations.observability.client import get_posthog_client

    client = get_posthog_client(posthog_cfg)
    if client is None:
        return False

    try:
        client.capture(
            "posthog_analytics_initialized",
            distinct_id=(os.getenv("POSTHOG_DISTINCT_ID") or "fleet-server").strip(),
            properties={
                "component": "server",
                "app_env": app_env,
                "auth_mode": auth_mode,
                "database_required": database_required,
                "version": __version__,
            },
        )
        return True
    except Exception:
        logger.warning("posthog_startup_event_failed", exc_info=True)
        return False


async def initialize_mlflow_runtime_service(
    state: ServerState,
    *,
    app_env: str,
) -> None:
    """Initialize MLflow runtime and optional local tracking server."""
    mlflow_cfg = MlflowConfig.from_env()
    if not mlflow_cfg.enabled:
        set_optional_service_status(state, "mlflow", "disabled")
        return

    auto_start_enabled = resolve_mlflow_auto_start_enabled(
        app_env=app_env,
        mlflow_enabled=mlflow_cfg.enabled,
        tracking_uri=mlflow_cfg.tracking_uri,
    )
    state.mlflow_server_process = (
        await start_mlflow_server(app_env=app_env, tracking_uri=mlflow_cfg.tracking_uri)
        if auto_start_enabled
        else None
    )

    from fleet_rlm.integrations.observability.mlflow_runtime import initialize_mlflow

    initialized = await asyncio.to_thread(initialize_mlflow, mlflow_cfg)
    set_optional_service_status(
        state,
        "mlflow",
        "ready" if initialized else "degraded",
        error=None if initialized else "MLflow runtime initialization unavailable",
    )


async def initialize_posthog_runtime_service(
    state: ServerState,
    *,
    app_env: str,
    auth_mode: str,
    database_required: bool,
) -> None:
    """Initialize PostHog runtime analytics startup state."""
    posthog_cfg = PostHogConfig.from_env()
    if not posthog_cfg.enabled or not posthog_cfg.api_key:
        set_optional_service_status(state, "posthog", "disabled")
        return

    emitted = await asyncio.to_thread(
        emit_posthog_startup_event,
        app_env=app_env,
        auth_mode=auth_mode,
        database_required=database_required,
    )
    set_optional_service_status(
        state,
        "posthog",
        "ready" if emitted else "degraded",
        error=None if emitted else "PostHog startup event unavailable",
    )
