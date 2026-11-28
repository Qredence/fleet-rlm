"""FastAPI application assembly, startup, and health probes."""

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager, closing, suppress
from pathlib import Path
from tempfile import gettempdir
from typing import Any

import dspy
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from . import agent, chatgpt_plan, opt_runner
from . import db as store
from .config import Settings
from .http import access, chat, optimizations, providers, settings, workspace
from .http.access import require_auth
from .http.dependencies import DbDep, Runtime, RuntimeDep
from .http.workspace import _WORKSPACE_STARTUP_HELP
from .interpreter import (
    DatasetVolumeError,
    DatasetVolumeObjectMissing,
    DaytonaError,
    SandboxStartupError,
    close_dataset_volume_helpers,
    make_daytona_client,
)
from .skills import SkillRegistry
from .storage import ObjectStorage, StorageError, StorageObjectMissing
from .telemetry import tracking_client

_LOG = logging.getLogger(__name__)


def _load_optimized_program_state(settings: Settings, sub_lm: dspy.BaseLM) -> dict[str, Any] | None:
    path = settings.optimized_program_path
    if path is None:
        return None
    if path.suffix != ".json":
        raise ValueError(f"Optimized program path must end with .json, received: {path}")
    state = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(state, dict):
        raise ValueError(f"Optimized program state in {path} must be a JSON object")
    # Validate artifact state once at startup before serving requests.
    agent.build_program(settings, [], sub_lm=sub_lm, optimized_state=state)
    return state


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    settings = Settings()
    access_log = logging.getLogger("uvicorn.access")
    callback_filter = chatgpt_plan.CallbackLogFilter()
    engine, sessionmaker = store.create_db(settings)
    runtime: Runtime | None = None
    access_log.addFilter(callback_filter)
    try:
        await store.reap_stale_runs(sessionmaker, settings.stale_run_timeout_seconds)
        await store.reap_stale_optimization_jobs(
            sessionmaker, settings.optimization_heartbeat_stale_seconds
        )
        skills = SkillRegistry.load(settings.skills_dir, fallback_to_bundled=True)
        lm = agent._model(settings, settings.llm_model)
        sub_lm = agent._model(settings, settings.sub_llm_model)
        decision_lm = dspy.LM(settings.decision_model)
        optimized_program_state = _load_optimized_program_state(settings, sub_lm)
        turn_semaphore = asyncio.Semaphore(settings.max_concurrent_turns_per_worker)
        cache_dir = opt_runner.ensure_private_cache_dir(
            settings.artifact_cache_dir or (Path(gettempdir()) / "fleet-rlm-artifacts")
        )
        chatgpt = None
        if settings.chatgpt_plan_enabled:
            local_config = Path.home() / ".config/fleet-rlm"
            chatgpt = chatgpt_plan.ChatGPTPlan(
                local_config / "chatgpt/credentials.json",
                local_config / "chatgpt-models/defaults.json",
            )
        with closing(ObjectStorage(settings)) as storage:
            async with AsyncExitStack() as clients:
                daytona = await clients.enter_async_context(make_daytona_client(settings))
                volume_daytona = daytona
                if settings.daytona_service_key and settings.daytona_service_key.get_secret_value():
                    volume_settings = settings.model_copy(
                        update={"daytona_api_key": settings.daytona_service_key}
                    )
                    volume_daytona = await clients.enter_async_context(
                        make_daytona_client(volume_settings)
                    )
                runtime = Runtime(
                    settings=settings,
                    sessionmaker=sessionmaker,
                    daytona=daytona,
                    volume_daytona=volume_daytona,
                    storage=storage,
                    skills=skills,
                    lm=lm,
                    sub_lm=sub_lm,
                    decision_lm=decision_lm,
                    turn_semaphore=turn_semaphore,
                    optimized_program_state=optimized_program_state,
                    tracking_pending=bool(settings.mlflow_tracking_uri),
                    artifact_cache_dir=cache_dir,
                    chatgpt=chatgpt,
                )
                if runtime.tracking_pending:
                    await clients.enter_async_context(
                        tracking_client(runtime, opt_runner.optimization_experiment(settings))
                    )
                clients.push_async_callback(opt_runner.shutdown_optimization_runtime, runtime)
                app.state.runtime = runtime
                yield
    finally:
        access_log.removeFilter(callback_filter)
        if runtime is None:
            with suppress(Exception):
                await close_dataset_volume_helpers()
        app.state.runtime = None
        await engine.dispose()


app = FastAPI(title="Fleet RLM", lifespan=lifespan, max_body_size=1 * 1024 * 1024)


@app.exception_handler(RequestValidationError)
async def _request_validation_error(
    _request: Request, error: RequestValidationError
) -> JSONResponse:
    details = []
    for issue in error.errors():
        kind, location, message = issue["type"], issue["loc"], issue["msg"]
        if kind == "extra_forbidden":
            location = location[:-1]
        if kind in {"value_error", "assertion_error"}:
            message = "Invalid value"
        details.append({"type": kind, "loc": list(location), "msg": message})
    return JSONResponse({"detail": details}, status_code=422)


@app.exception_handler(chatgpt_plan.PlanError)
async def _chatgpt_plan_error(request: Request, error: chatgpt_plan.PlanError) -> JSONResponse:
    return JSONResponse(
        {"detail": str(error)}, status_code=409, headers={"Cache-Control": "no-store"}
    )


@app.exception_handler(store.SessionNotFoundError)
async def session_not_found(_request: Request, _error: store.SessionNotFoundError) -> JSONResponse:
    return JSONResponse({"detail": "Session not found"}, status_code=404)


@app.exception_handler(SandboxStartupError)
async def sandbox_startup_error(_request: Request, error: SandboxStartupError) -> JSONResponse:
    _LOG.warning("Workspace startup failed (%s)", type(error.__cause__ or error).__name__)
    return JSONResponse({"detail": _WORKSPACE_STARTUP_HELP}, status_code=503)


@app.exception_handler(DaytonaError)
async def daytona_error(_request: Request, error: DaytonaError) -> JSONResponse:
    _LOG.warning("Daytona request failed (%s)", type(error).__name__)
    return JSONResponse({"detail": "Workspace service unavailable"}, status_code=502)


@app.exception_handler(StorageObjectMissing)
async def missing_storage_object(_request: Request, _error: StorageObjectMissing) -> JSONResponse:
    return JSONResponse({"detail": "File not found"}, status_code=404)


@app.exception_handler(StorageError)
async def storage_error(_request: Request, _error: StorageError) -> JSONResponse:
    return JSONResponse({"detail": "Workspace storage unavailable"}, status_code=503)


@app.exception_handler(DatasetVolumeObjectMissing)
async def missing_volume_dataset(
    _request: Request, _error: DatasetVolumeObjectMissing
) -> JSONResponse:
    return JSONResponse({"detail": "Dataset not found in Daytona volume"}, status_code=404)


@app.exception_handler(DatasetVolumeError)
async def volume_dataset_error(_request: Request, error: DatasetVolumeError) -> JSONResponse:
    _LOG.warning(
        "Daytona dataset volume request failed (%s)",
        type(error.__cause__ or error).__name__,
    )
    return JSONResponse({"detail": "Daytona dataset volume unavailable"}, status_code=503)


@app.get("/healthz")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
async def readiness(runtime: RuntimeDep, db: DbDep) -> dict[str, str]:
    if runtime.daytona is None:
        raise HTTPException(503, "Workspace client is not ready")
    try:
        await db.execute(text("SELECT 1"))
    except SQLAlchemyError as error:
        raise HTTPException(503, "Database is not ready") from error
    return {"status": "ok"}


app.add_middleware(access._WorkspaceAccess)
app.add_middleware(access._SettingsCORS)

api_router = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])
for router in (
    workspace.router,
    settings.router,
    providers.router,
    optimizations.router,
    chat.router,
):
    api_router.include_router(router)
app.include_router(api_router)
app.include_router(providers.callback_router)
