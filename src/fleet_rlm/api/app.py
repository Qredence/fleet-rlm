"""The FastAPI application: lifespan resources, routers, and the health route."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress
from importlib.metadata import version

import mlflow
from fastapi import FastAPI

from fleet_rlm.api.routes import sessions, turns, workspace
from fleet_rlm.api.schemas import Health
from fleet_rlm.config import FleetSettings, get_settings
from fleet_rlm.launcher import mlflow_is_healthy
from fleet_rlm.observability import configure_mlflow_observability
from fleet_rlm.session import SessionRuntimeRegistry
from fleet_rlm.store import Store

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
    async def lifespan(application: FastAPI) -> AsyncGenerator[None, None]:
        # The pool opens in the background so a cold Lakebase connect does not block startup.
        application.state.turn_executor = turns.create_turn_executor()
        application.state.store = store or (
            Store.open(
                resolved.fleet_database_url,
                lakebase_endpoint=resolved.fleet_lakebase_endpoint,
                migration_dsn=resolved.fleet_database_migration_url,
                schema=resolved.fleet_database_schema,
            )
            if initialize_db
            else None
        )
        try:
            if initialize_db and application.state.store is not None:
                try:
                    await asyncio.to_thread(application.state.store.initialize)
                finally:
                    await asyncio.to_thread(application.state.store.close_migration_pool)
            application.state.mlflow_experiment_id = (
                await asyncio.to_thread(_configure_tracing, resolved) if configure_observability else None
            )
            application.state.session_registry = SessionRuntimeRegistry(resolved, application.state.store)
            yield
        finally:
            # Queued Turns must not start during shutdown; running ones end when their sandboxes close.
            application.state.turn_executor.shutdown(wait=False, cancel_futures=True)
            try:
                if application.state.session_registry is not None:
                    await asyncio.to_thread(application.state.session_registry.close)
            finally:
                try:
                    if application.state.store is not None and store is None:
                        await asyncio.to_thread(application.state.store.close)
                finally:
                    if application.state.mlflow_experiment_id:
                        # uvicorn re-raises SIGTERM after graceful stop, skipping MLflow's exit-time flush.
                        with suppress(Exception):
                            await asyncio.to_thread(mlflow.flush_trace_async_logging)

    app = FastAPI(title="Fleet-RLM Lite API", version=VERSION, lifespan=lifespan)
    app.state.settings = resolved
    app.state.store = store
    app.state.session_registry = None
    app.state.turn_executor = None
    app.state.mlflow_experiment_id = None

    @app.get("/health", response_model=Health, tags=["health"])
    def health() -> dict:
        return {
            "status": "ok",
            "version": VERSION,
            "mlflow": {
                **resolved.mlflow_descriptor(),
                "experiment_id": app.state.mlflow_experiment_id,
                "ready": app.state.mlflow_experiment_id is not None,
            },
        }

    for router in (
        sessions.router,
        workspace.router,
        sessions.memory_router,
        sessions.skills_router,
        turns.router,
    ):
        app.include_router(router)
    return app


def _configure_tracing(settings: FleetSettings) -> str:
    target = settings.mlflow_target()
    settings.prepare_mlflow_auth()
    if target.mode == "local" and not mlflow_is_healthy(target.tracking_uri):
        raise RuntimeError(
            f"Local MLflow is not reachable at {target.tracking_uri}; start it with `fleet cli` "
            "or start an MLflow server separately before using `fleet serve`."
        )
    try:
        experiment_id = configure_mlflow_observability(
            target.experiment_name,
            target.tracking_uri,
            trace_catalog=settings.mlflow_trace_catalog,
            trace_schema=settings.mlflow_trace_schema,
            trace_table_prefix=settings.mlflow_trace_table_prefix,
            tracing_sql_warehouse_id=settings.mlflow_tracing_sql_warehouse_id,
        )
    except Exception as exc:
        raise RuntimeError(
            f"Could not initialize {target.mode} MLflow experiment {target.experiment_name!r} "
            f"({type(exc).__name__}); check tracking access and experiment configuration."
        ) from None
    if not experiment_id:
        raise RuntimeError("MLflow initialization did not return an experiment ID")
    return experiment_id
