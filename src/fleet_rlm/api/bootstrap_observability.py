"""Observability-specific bootstrap helpers for the FastAPI server."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import signal
import socket
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

from fleet_rlm import __version__
from fleet_rlm.integrations.observability.config import (
    MlflowConfig,
    PostHogConfig,
    PROJECT_MLFLOW_LOCAL_BACKEND_STORE_URI,
)

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
    """Terminate a subprocess tree, escalating to SIGKILL if needed, then reap it."""

    uses_dedicated_process_group = False
    target_pgid: int | None = None

    try:
        if hasattr(os, "killpg"):
            with contextlib.suppress(ProcessLookupError):
                candidate_pgid = os.getpgid(proc.pid)
                if candidate_pgid != os.getpgrp():
                    target_pgid = candidate_pgid
                    uses_dedicated_process_group = True

        if uses_dedicated_process_group and target_pgid is not None:
            os.killpg(target_pgid, signal.SIGTERM)
        elif proc.poll() is None:
            proc.terminate()

        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            logger.warning(
                "Process (pid=%d) did not exit after terminate(); sending kill()",
                proc.pid,
            )
            if uses_dedicated_process_group and target_pgid is not None:
                os.killpg(target_pgid, signal.SIGKILL)
            else:
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


def resolve_mlflow_tracking_port(tracking_uri: str) -> int:
    """Return the TCP port targeted by *tracking_uri*, defaulting to 5001."""

    try:
        match = re.search(r":(\d+)", tracking_uri.strip())
        return int(match.group(1)) if match else 5001
    except (ValueError, AttributeError):
        return 5001


def build_local_mlflow_unavailable_error(
    *,
    tracking_uri: str,
    backend_store_uri: str,
) -> str:
    """Return actionable guidance when local MLflow auto-start remains unavailable."""

    resolved_tracking_uri = tracking_uri.strip() or "http://127.0.0.1:5001"
    resolved_backend_store_uri = (
        backend_store_uri.strip() or PROJECT_MLFLOW_LOCAL_BACKEND_STORE_URI
    )
    return (
        f"Local MLflow auto-start did not become reachable at {resolved_tracking_uri}. "
        f"Backend store: {resolved_backend_store_uri}. "
        "If startup logs mention an out-of-date schema, run "
        f"'uv run mlflow db upgrade {resolved_backend_store_uri}' or recreate the "
        "local backend store if that history is disposable."
    )


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
    backend_store_uri: str,
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

    port = resolve_mlflow_tracking_port(tracking_uri)
    resolved_backend_store_uri = (
        backend_store_uri.strip() or PROJECT_MLFLOW_LOCAL_BACKEND_STORE_URI
    )

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
                resolved_backend_store_uri,
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
    tracking_port = resolve_mlflow_tracking_port(mlflow_cfg.tracking_uri)
    state.mlflow_server_process = (
        await start_mlflow_server(
            app_env=app_env,
            tracking_uri=mlflow_cfg.tracking_uri,
            backend_store_uri=mlflow_cfg.local_backend_store_uri,
        )
        if auto_start_enabled
        else None
    )

    from fleet_rlm.integrations.observability.mlflow_runtime import initialize_mlflow

    initialized = await asyncio.to_thread(initialize_mlflow, mlflow_cfg)
    startup_error = None
    if not initialized:
        startup_error = "MLflow runtime initialization unavailable"
        if auto_start_enabled and not _mlflow_startup_socket_ready(port=tracking_port):
            startup_error = build_local_mlflow_unavailable_error(
                tracking_uri=mlflow_cfg.tracking_uri,
                backend_store_uri=mlflow_cfg.local_backend_store_uri,
            )
    set_optional_service_status(
        state,
        "mlflow",
        "ready" if initialized else "degraded",
        error=startup_error,
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
