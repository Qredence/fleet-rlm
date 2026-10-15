"""The FastAPI application: lifespan resources, routers, and the health route."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from importlib.metadata import version

import mlflow
from fastapi import FastAPI

from fleet_rlm.api.routes import memory, sessions, skills, turns, workspace
from fleet_rlm.api.schemas import Health
from fleet_rlm.config import FleetSettings, get_settings
from fleet_rlm.launcher import mlflow_is_healthy
from fleet_rlm.observability import configure_mlflow_observability
from fleet_rlm.session import SessionRuntimeRegistry
from fleet_rlm.store import Store

_LOGGER = logging.getLogger("fleet_rlm.api")
VERSION = version("fleet-rlm")


def create_app(
    *,
    settings: FleetSettings | None = None,
    initialize_db: bool = True,
    configure_observability: bool = True,
    store: Store | None = None,
) -> FastAPI:
    """Build the API. External resources are acquired by the lifespan, not at construction."""
    resolved = settings or get_settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        # The pool opens in the background so a cold Lakebase connect does not block startup.
        application.state.store = store or (Store.open(resolved.fleet_database_url) if initialize_db else None)
        if initialize_db and application.state.store is not None:
            await asyncio.to_thread(application.state.store.initialize)
        application.state.mlflow_experiment_id = (
            await asyncio.to_thread(_configure_tracing, resolved) if configure_observability else None
        )
        application.state.session_registry = SessionRuntimeRegistry(resolved, application.state.store)
        try:
            yield
        finally:
            await asyncio.to_thread(application.state.session_registry.close)
            if application.state.store is not None and store is None:
                await asyncio.to_thread(application.state.store.close)
            if application.state.mlflow_experiment_id:
                # uvicorn re-raises SIGTERM after a graceful stop, which skips atexit and MLflow's exit-time flush.
                with suppress(Exception):
                    await asyncio.to_thread(mlflow.flush_trace_async_logging)

    app = FastAPI(title="Fleet-RLM Lite API", version=VERSION, lifespan=lifespan)
    app.state.settings = resolved
    app.state.store = store
    app.state.session_registry = None
    app.state.mlflow_experiment_id = None

    @app.get("/health", response_model=Health, tags=["health"])
    def health() -> dict:
        return {"status": "ok", "version": VERSION}

    for module in (sessions, workspace, memory, skills, turns):
        app.include_router(module.router)
    return app


def _configure_tracing(settings: FleetSettings) -> str | None:
    target = settings.mlflow_target()
    if target.mode == "local" and target.tracking_uri.startswith("http") and not mlflow_is_healthy(target.tracking_uri):
        _LOGGER.warning(
            "Local MLflow is not reachable at %s; tracing is off. Start it with `fleet cli` "
            "or set FLEET_MLFLOW_MODE=databricks.",
            target.tracking_uri,
        )
        return None
    try:
        return configure_mlflow_observability(
            target.experiment_name,
            target.tracking_uri,
            trace_catalog=settings.mlflow_trace_catalog,
            trace_schema=settings.mlflow_trace_schema,
            trace_table_prefix=settings.mlflow_trace_table_prefix,
            tracing_sql_warehouse_id=settings.mlflow_tracing_sql_warehouse_id,
        )
    except Exception as exc:
        _LOGGER.warning("MLflow tracing could not be configured (%s)", type(exc).__name__)
        return None
