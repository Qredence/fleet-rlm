"""Stateless HTTP and native POST SSE for the Fleet backend."""

import asyncio
import json
import logging
import re
import secrets
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager, closing, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from typing import Annotated, Any, Literal
from urllib.parse import quote, urlsplit, urlunsplit
from uuid import UUID

import dspy
import httpx
import mlflow
from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from fastapi import File as Upload
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.sse import EventSourceResponse, ServerSentEvent
from mlflow.entities import LiveSpan
from mlflow.entities.trace_location import MlflowExperimentLocation
from mlflow.environment_variables import MLFLOW_USE_DEFAULT_TRACER_PROVIDER
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.types import ASGIApp, Receive, Scope, Send

from . import agent, ui_stream
from . import db as store
from .config import Settings
from .interpreter import (
    WORKSPACE,
    AsyncDaytona,
    AsyncSandbox,
    DaytonaError,
    SandboxStartupError,
    WorkspaceBrowseLimit,
    allocate_session_sandbox,
    delete_pending_sandbox,
    delete_sandbox_by_id,
    download_workspace_file,
    get_running_sandbox,
    list_new_artifacts,
    list_workspace_files,
    make_daytona_client,
    make_interpreter_factory,
    prepare_sandbox,
    read_browsable_workspace_file,
    reconcile_managed_workspace,
    reset_managed_workspace,
    reusable_session_sandbox,
    sandbox_cleanup_failed,
    upload_workspace_file,
    workspace_span,
)
from .skills import MAX_SKILL_BYTES, SkillRegistry, load_host_tools, select_skills
from .storage import ObjectStorage, StorageError, StorageIntegrityError, StorageObjectMissing

_STREAM_HEADER = "x-vercel-ai-ui-message-stream"
_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class Runtime:
    settings: Settings
    sessionmaker: store.SessionFactory
    daytona: AsyncDaytona
    storage: ObjectStorage
    skills: SkillRegistry
    lm: dspy.BaseLM
    sub_lm: dspy.BaseLM
    decision_lm: dspy.BaseLM
    turn_semaphore: asyncio.Semaphore
    optimized_program_state: dict[str, Any] | None
    mlflow_experiment_id: str | None = None


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
    engine, sessionmaker = store.create_db(settings)
    try:
        await store.reap_stale_runs(sessionmaker, settings.stale_run_timeout_seconds)
        experiment_id = None
        if settings.mlflow_tracking_uri:
            mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
            experiment = mlflow.set_experiment(settings.mlflow_experiment)
            experiment_id = experiment.experiment_id
            # Concurrent creation can return the experiment without activating it.
            mlflow.set_experiment(experiment_id=experiment.experiment_id)
            if settings.daytona_otel_enabled and not MLFLOW_USE_DEFAULT_TRACER_PROVIDER.get():
                mlflow.tracing.set_destination(MlflowExperimentLocation(experiment.experiment_id))
            mlflow.dspy.autolog()
        skills = SkillRegistry.load(settings.skills_dir, fallback_to_bundled=True)
        lm = agent._model(settings, settings.llm_model)
        sub_lm = agent._model(settings, settings.sub_llm_model)
        decision_lm = dspy.LM(settings.decision_model)
        optimized_program_state = _load_optimized_program_state(settings, sub_lm)
        turn_semaphore = asyncio.Semaphore(settings.max_concurrent_turns_per_worker)
        with closing(ObjectStorage(settings)) as storage:
            async with make_daytona_client(settings) as daytona:
                app.state.runtime = Runtime(
                    settings=settings,
                    sessionmaker=sessionmaker,
                    daytona=daytona,
                    storage=storage,
                    skills=skills,
                    lm=lm,
                    sub_lm=sub_lm,
                    decision_lm=decision_lm,
                    turn_semaphore=turn_semaphore,
                    optimized_program_state=optimized_program_state,
                    mlflow_experiment_id=experiment_id,
                )
                yield
    finally:
        app.state.runtime = None
        await engine.dispose()


class _SettingsCORS:
    """Configure native CORS on the first HTTP request after lifespan startup."""

    def __init__(self, app: ASGIApp):
        self.app = app
        self.cors: CORSMiddleware | None = None
        self.origins: tuple[str, ...] | None = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        runtime = getattr(scope["app"].state, "runtime", None)
        if runtime is None:
            await self.app(scope, receive, send)
            return
        origins = tuple(runtime.settings.cors_origins)
        if self.cors is None or self.origins != origins:
            self.cors = CORSMiddleware(
                self.app,
                allow_origins=origins,
                allow_methods=["GET", "POST", "PATCH", "DELETE"],
                allow_headers=["Authorization", "Content-Type"],
                expose_headers=[_STREAM_HEADER],
            )
            self.origins = origins
        await self.cors(scope, receive, send)


app = FastAPI(title="Fleet RLM", lifespan=lifespan, max_body_size=1 * 1024 * 1024)
app.add_middleware(_SettingsCORS)


def get_runtime(request: Request) -> Runtime:
    runtime = getattr(request.app.state, "runtime", None)
    if runtime is None:
        raise HTTPException(503, "Fleet is not ready")
    return runtime


RuntimeDep = Annotated[Runtime, Depends(get_runtime)]


def require_auth(
    runtime: RuntimeDep,
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    expected = runtime.settings.api_bearer_token
    if expected is None:
        return
    scheme, _, token = (authorization or "").partition(" ")
    if (
        scheme.lower() != "bearer"
        or not token
        or not secrets.compare_digest(token.encode(), expected.get_secret_value().encode())
    ):
        raise HTTPException(401, "Invalid bearer token", headers={"WWW-Authenticate": "Bearer"})


async def get_db(runtime: RuntimeDep) -> AsyncIterator[AsyncSession]:
    async with runtime.sessionmaker() as db:
        yield db


DbDep = Annotated[AsyncSession, Depends(get_db, scope="function")]


async def turn_slot(runtime: RuntimeDep) -> AsyncIterator[None]:
    async with runtime.turn_semaphore:
        yield


TurnConcurrencySlot = Annotated[None, Depends(turn_slot)]


class SessionCreate(BaseModel):
    title: str = Field(default="New session", min_length=1)


class SessionEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    model_override: str | None = None


class SessionView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    model_override: str | None = None
    daytona_sandbox_id: str | None = None
    deleting: bool = False
    created_at: datetime
    updated_at: datetime


_NON_EMPTY_SETTINGS_FIELDS = (
    "llm_model",
    "sub_llm_model",
    "decision_model",
)


class WorkspaceSettingsFields(BaseModel):
    llm_model: str = Field(min_length=1)
    sub_llm_model: str = Field(min_length=1)
    decision_model: str = Field(min_length=1)
    rlm_max_iters: int = Field(ge=1, le=100)
    rlm_max_llm_calls: int = Field(ge=1, le=500)
    rlm_max_depth: int = Field(ge=1, le=7)
    rlm_max_output_chars: int = Field(ge=500, le=200_000)
    history_max_turns: int = Field(ge=0, le=100)
    history_max_chars: int = Field(ge=1_000, le=200_000)
    daytona_exec_timeout_seconds: int = Field(ge=5, le=600)


_EDITABLE_SETTINGS_FIELDS = frozenset(WorkspaceSettingsFields.model_fields)


class WorkspaceSettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    llm_model: str | None = None
    sub_llm_model: str | None = None
    decision_model: str | None = None
    rlm_max_iters: int | None = Field(default=None, ge=1, le=100)
    rlm_max_llm_calls: int | None = Field(default=None, ge=1, le=500)
    rlm_max_depth: int | None = Field(default=None, ge=1, le=7)
    rlm_max_output_chars: int | None = Field(default=None, ge=500, le=200_000)
    history_max_turns: int | None = Field(default=None, ge=0, le=100)
    history_max_chars: int | None = Field(default=None, ge=1_000, le=200_000)
    daytona_exec_timeout_seconds: int | None = Field(default=None, ge=5, le=600)

    @field_validator(*_NON_EMPTY_SETTINGS_FIELDS)
    @classmethod
    def validate_non_empty(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Must not be empty")
        return cleaned


class WorkspaceSettingsStatus(BaseModel):
    database_configured: bool
    daytona_configured: bool
    daytona_image: str
    daytona_region: str | None
    daytona_idle_timeout_minutes: int
    daytona_max_lifetime_minutes: int
    storage_bucket: str
    llm_api_key_configured: bool
    llm_base_url: str | None
    bearer_auth_enabled: bool
    max_concurrent_turns_per_worker: int
    mlflow_tracking_uri: str | None
    mlflow_experiment: str
    optimized_program_path: str | None
    skill_selector_path: str | None
    memory_gate_path: str | None
    skills_count: int


class WorkspaceSettingsView(BaseModel):
    effective: WorkspaceSettingsFields
    defaults: WorkspaceSettingsFields
    overrides: dict[str, Any]
    status: WorkspaceSettingsStatus


class CatalogModel(BaseModel):
    id: str
    label: str


class ModelCatalog(BaseModel):
    workspace_default: str
    catalog_status: Literal["ready", "unavailable", "unconfigured"]
    models: list[CatalogModel] = Field(default_factory=list)


class FileView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    session_id: UUID
    path: str
    sha256: str
    size_bytes: int
    kind: str
    created_at: datetime


class SkillView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    description: str


class SkillDetail(SkillView):
    body: str


class SkillInstall(BaseModel):
    path: str = Field(min_length=1, max_length=4096)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class MemoryView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    session_id: UUID | None
    source_run_id: UUID | None
    skill_name: str | None
    content: str
    approved: bool
    created_at: datetime

    @field_validator("content")
    @classmethod
    def display_fact(cls, content: str) -> str:
        return agent.memory_fact(content)


class MemoryEdit(BaseModel):
    content: str | None = Field(default=None, min_length=1)
    approved: bool | None = None


class RefineTurnView(BaseModel):
    memory: MemoryView | None


class MessagePart(BaseModel):
    type: str
    text: str | None = None


class Message(BaseModel):
    role: str
    parts: list[MessagePart]


class ChatPayload(BaseModel):
    messages: list[Message]
    pinned_skills: list[str] = Field(default_factory=list)

    def latest_prompt(self) -> str:
        for message in reversed(self.messages):
            if message.role == "user":
                prompt = "\n".join(
                    part.text for part in message.parts if part.type == "text" and part.text
                ).strip()
                if prompt:
                    return prompt
                break
        raise HTTPException(422, "The latest user message must contain text")


@app.exception_handler(store.SessionNotFoundError)
async def session_not_found(_request: Request, _error: store.SessionNotFoundError) -> JSONResponse:
    return JSONResponse({"detail": "Session not found"}, status_code=404)


_WORKSPACE_STARTUP_HELP = (
    "Fleet could not start the workspace. Try again. If the problem persists, "
    "ask your administrator to check the Daytona image and region."
)


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


async def _session(
    db: AsyncSession, session_id: UUID, *, for_update: bool = False
) -> store.Session:
    session = await db.get(
        store.Session, session_id, with_for_update=store.SESSION_WRITE_LOCK if for_update else None
    )
    if session is None or session.deleting:
        raise store.SessionNotFoundError(session_id)
    return session


async def _editable_session(db: AsyncSession, session_id: UUID) -> store.Session:
    # FOR UPDATE serializes mutations with the FK check on a concurrent root INSERT.
    session = await _session(db, session_id, for_update=True)
    if await db.scalar(
        select(store.Run.id).where(
            store.Run.session_id == session_id,
            store.Run.parent_run_id.is_(None),
            store.Run.status == "running",
        )
    ):
        raise HTTPException(409, "Session already has a running turn")
    return session


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


@app.post("/api/sessions", status_code=201, dependencies=[Depends(require_auth)])
async def create_session(db: DbDep, payload: SessionCreate | None = None) -> SessionView:
    async with db.begin():
        session = store.Session(title=(payload or SessionCreate()).title)
        db.add(session)
    return SessionView.model_validate(session)


@app.get("/api/sessions", dependencies=[Depends(require_auth)])
async def list_sessions(
    db: DbDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[SessionView]:
    sessions = await db.scalars(
        select(store.Session)
        .order_by(store.Session.updated_at.desc(), store.Session.id.desc())
        .limit(limit)
    )
    return [SessionView.model_validate(session) for session in sessions]


@app.get("/api/sessions/{session_id}", dependencies=[Depends(require_auth)])
async def get_session(session_id: UUID, db: DbDep) -> SessionView:
    session = await db.get(store.Session, session_id)
    if session is None:
        raise store.SessionNotFoundError(session_id)
    return SessionView.model_validate(session)


@app.patch("/api/sessions/{session_id}", dependencies=[Depends(require_auth)])
async def edit_session(session_id: UUID, payload: SessionEdit, db: DbDep) -> SessionView:
    if not payload.model_fields_set:
        raise HTTPException(422, "At least one field to update is required")
    changes: dict[str, Any] = {}
    if "title" in payload.model_fields_set:
        if payload.title is None:
            raise HTTPException(422, "Session title cannot be null")
        title = payload.title.strip()
        if not 1 <= len(title) <= 200:
            raise HTTPException(422, "Session title must be between 1 and 200 characters")
        changes["title"] = title
    if "model_override" in payload.model_fields_set:
        override = payload.model_override.strip() if payload.model_override is not None else ""
        changes["model_override"] = override or None
    async with db.begin():
        session = await db.scalar(
            update(store.Session)
            .where(store.Session.id == session_id, store.Session.deleting.is_(False))
            .values(**changes, updated_at=func.now())
            .returning(store.Session)
        )
        if session is None:
            raise store.SessionNotFoundError(session_id)
    return SessionView.model_validate(session)


def _sanitize_overrides(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in raw.items()
        if key in _EDITABLE_SETTINGS_FIELDS and value is not None
    }


def _settings_fields(settings: Settings) -> WorkspaceSettingsFields:
    return WorkspaceSettingsFields(
        **{field: getattr(settings, field) for field in _EDITABLE_SETTINGS_FIELDS}
    )


def _merge_settings(base: Settings, overrides: dict[str, Any]) -> Settings:
    clean = _sanitize_overrides(overrides)
    return base.model_copy(update=clean) if clean else base


async def _effective_settings(runtime: Runtime) -> Settings:
    overrides = await store.get_workspace_settings_overrides(runtime.sessionmaker)
    return _merge_settings(runtime.settings, overrides)


async def _skill_registry(runtime: Runtime) -> SkillRegistry:
    return await asyncio.to_thread(runtime.skills.with_installed, runtime.settings.skills_dir)


async def _workspace_settings_view(
    runtime: Runtime, raw_overrides: dict[str, Any]
) -> WorkspaceSettingsView:
    overrides = _sanitize_overrides(raw_overrides)
    base = runtime.settings
    effective = _merge_settings(base, overrides)
    return WorkspaceSettingsView(
        effective=_settings_fields(effective),
        defaults=_settings_fields(base),
        overrides=overrides,
        status=WorkspaceSettingsStatus(
            database_configured=bool(base.database_url),
            daytona_configured=bool(base.daytona_api_key.get_secret_value()),
            daytona_image=base.daytona_image,
            daytona_region=base.daytona_region,
            daytona_idle_timeout_minutes=base.daytona_idle_timeout_minutes,
            daytona_max_lifetime_minutes=base.daytona_max_lifetime_minutes,
            storage_bucket=base.s3_bucket,
            llm_api_key_configured=bool(base.llm_api_key.get_secret_value()),
            llm_base_url=base.llm_base_url,
            bearer_auth_enabled=base.api_bearer_token is not None
            and bool(base.api_bearer_token.get_secret_value()),
            max_concurrent_turns_per_worker=base.max_concurrent_turns_per_worker,
            mlflow_tracking_uri=base.mlflow_tracking_uri,
            mlflow_experiment=base.mlflow_experiment,
            optimized_program_path=str(base.optimized_program_path)
            if base.optimized_program_path is not None
            else None,
            skill_selector_path=str(base.skill_selector_path)
            if base.skill_selector_path is not None
            else None,
            memory_gate_path=str(base.memory_gate_path)
            if base.memory_gate_path is not None
            else None,
            skills_count=len((await _skill_registry(runtime)).available_skills(base)),
        ),
    )


def _request_root_lm(runtime: Runtime, effective: Settings) -> dspy.BaseLM:
    if effective.llm_model != runtime.settings.llm_model:
        return agent._model(effective, effective.llm_model)
    return runtime.lm


def _request_sub_lm(runtime: Runtime, effective: Settings) -> dspy.BaseLM:
    if effective.sub_llm_model != runtime.settings.sub_llm_model:
        return agent._model(effective, effective.sub_llm_model)
    return runtime.sub_lm


def _request_decision_lm(runtime: Runtime, effective: Settings) -> dspy.BaseLM:
    if effective.decision_model != runtime.settings.decision_model:
        return dspy.LM(effective.decision_model)
    return runtime.decision_lm


async def _gateway_model_labels(base_url: str, api_key: str) -> list[str]:
    base = httpx.URL(base_url)
    if (
        base.scheme not in {"http", "https"}
        or not base.host
        or base.userinfo
        or base.query
        or base.fragment
    ):
        raise ValueError("Invalid model gateway base URL")
    url = base.copy_with(raw_path=base.raw_path.rstrip(b"/") + b"/models")
    async with asyncio.timeout(5), httpx.AsyncClient(timeout=5, follow_redirects=False) as client:
        response = await client.get(url, headers={"Authorization": f"Bearer {api_key}"})
        response.raise_for_status()
        payload = response.json()

    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ValueError("Invalid model catalog response")
    labels: set[str] = set()
    for entry in payload["data"]:
        if not isinstance(entry, dict) or not isinstance(label := entry.get("id"), str):
            continue
        if not label or any(char.isspace() or not char.isprintable() for char in label):
            continue
        model_type = entry.get("type")
        if (
            isinstance(model_type, str)
            and model_type
            and model_type not in {"language", "chat", "text"}
        ):
            continue
        architecture = entry.get("architecture")
        output = architecture.get("output_modalities") if isinstance(architecture, dict) else None
        if isinstance(output, list) and output and "text" not in output:
            continue
        labels.add(label)
    return sorted(labels, key=lambda label: (label.casefold(), label))


@app.get("/api/models", dependencies=[Depends(require_auth)])
async def list_models(runtime: RuntimeDep, response: Response) -> ModelCatalog:
    effective = await _effective_settings(runtime)
    response.headers["Cache-Control"] = "private, no-store"
    if not effective.llm_base_url:
        return ModelCatalog(workspace_default=effective.llm_model, catalog_status="unconfigured")
    try:
        labels = await _gateway_model_labels(
            effective.llm_base_url, effective.llm_api_key.get_secret_value()
        )
    except (httpx.HTTPError, httpx.InvalidURL, TimeoutError, ValueError):
        return ModelCatalog(workspace_default=effective.llm_model, catalog_status="unavailable")
    return ModelCatalog(
        workspace_default=effective.llm_model,
        catalog_status="ready",
        models=[CatalogModel(id=f"openai/{label}", label=label) for label in labels],
    )


@app.get("/api/settings", dependencies=[Depends(require_auth)])
async def get_settings(runtime: RuntimeDep) -> WorkspaceSettingsView:
    overrides = await store.get_workspace_settings_overrides(runtime.sessionmaker)
    return await _workspace_settings_view(runtime, overrides)


@app.patch("/api/settings", dependencies=[Depends(require_auth)])
async def patch_settings(
    payload: WorkspaceSettingsPatch, runtime: RuntimeDep
) -> WorkspaceSettingsView:
    changes = {field: getattr(payload, field) for field in payload.model_fields_set}
    if changes:
        overrides = await store.update_workspace_settings_overrides(runtime.sessionmaker, changes)
    else:
        overrides = await store.get_workspace_settings_overrides(runtime.sessionmaker)
    return await _workspace_settings_view(runtime, overrides)


@app.delete("/api/settings", dependencies=[Depends(require_auth)])
async def reset_settings(runtime: RuntimeDep) -> WorkspaceSettingsView:
    overrides = await store.reset_workspace_settings_overrides(runtime.sessionmaker)
    return await _workspace_settings_view(runtime, overrides)


@app.delete("/api/sessions/{session_id}", status_code=204, dependencies=[Depends(require_auth)])
async def delete_session(session_id: UUID, runtime: RuntimeDep) -> None:
    async with runtime.sessionmaker() as db, db.begin():
        session = await db.get(store.Session, session_id, with_for_update=store.SESSION_WRITE_LOCK)
        if session is None:
            raise store.SessionNotFoundError(session_id)
        if not session.deleting:
            session = await _editable_session(db, session_id)
            if store.upload_is_active(session):
                raise HTTPException(409, "Session already has an upload in progress")
            session.deleting = True
            session.upload_key = session.upload_started_at = None
        sandbox_id = session.daytona_sandbox_id
        pending_name = session.pending_sandbox_name
    try:
        if sandbox_id:
            await delete_sandbox_by_id(runtime.daytona, sandbox_id)
        if pending_name:
            await delete_pending_sandbox(runtime.daytona, runtime.settings, pending_name)
        await _reclaim_discarded_objects(runtime, session_id)
        await runtime.storage.delete_session(session_id)
    except (DaytonaError, SandboxStartupError, StorageError) as error:
        raise HTTPException(503, "Workspace deletion can be retried") from error
    async with runtime.sessionmaker() as db, db.begin():
        session = await db.get(store.Session, session_id, with_for_update=True)
        if session is not None:
            await db.delete(session)


@app.get("/api/sessions/{session_id}/messages", dependencies=[Depends(require_auth)])
async def session_messages(session_id: UUID, runtime: RuntimeDep) -> list[dict]:
    await store.get_session_or_raise(runtime.sessionmaker, session_id)
    return await store.get_session_ui_messages(runtime.sessionmaker, session_id)


def _safe_component(name: str) -> bool:
    return (
        bool(name)
        and name == name.strip()
        and name not in {".", ".."}
        and not any(char in name for char in "/\\:%")
        and name.isprintable()
        and len(name.encode()) <= 255
    )


def _valid_file_path(path: str) -> bool:
    root, *parts = path.split("/")
    return root in {"files", "artifacts"} and bool(parts) and all(map(_safe_component, parts))


async def _ensure_bound_sandbox(
    runtime: Runtime,
    effective: Settings,
    session_id: UUID,
    existing_sandbox_id: str | None,
    *,
    owner_run_id: UUID,
    needs_cleanup: bool = False,
) -> AsyncSandbox:
    if existing_sandbox_id:
        if needs_cleanup:
            try:
                await delete_sandbox_by_id(runtime.daytona, existing_sandbox_id)
            except DaytonaError as error:
                raise SandboxStartupError(
                    "Could not retire the previous Daytona workspace"
                ) from error
        elif sandbox := await reusable_session_sandbox(runtime.daytona, existing_sandbox_id):
            if sandbox.state != "started":
                await prepare_sandbox(sandbox)
            return sandbox
    name, recovering = await store.reserve_session_sandbox(
        runtime.sessionmaker, session_id, owner_run_id, expected=existing_sandbox_id
    )
    sandbox = await allocate_session_sandbox(
        runtime.daytona, effective, name, recovering=recovering
    )
    # A stale worker leaves the reservation for the next owner to reconcile.
    _, won = await store.rebind_session_sandbox(
        runtime.sessionmaker,
        session_id,
        sandbox.id,
        expected=existing_sandbox_id,
        owner_run_id=owner_run_id,
        allocation_name=name,
    )
    if not won:
        raise SandboxStartupError("The Daytona workspace binding changed during startup")
    await prepare_sandbox(sandbox)
    return sandbox


async def _reclaim_discarded_objects(runtime: Runtime, session_id: UUID) -> None:
    reclaimed = []
    try:
        for entry in await store.list_discarded_objects(runtime.sessionmaker, session_id):
            await runtime.storage.reclaim(entry.object_key, entry.upload_id)
            reclaimed.append(entry.object_key)
    finally:
        if reclaimed:
            await store.forget_discarded_objects(runtime.sessionmaker, reclaimed)


async def _try_object_cleanup(runtime: Runtime, session_id: UUID) -> None:
    try:
        await _reclaim_discarded_objects(runtime, session_id)
    except (StorageError, SQLAlchemyError) as error:
        _LOG.warning("Workspace object cleanup will retry (%s)", type(error).__name__)


@app.post(
    "/api/sessions/{session_id}/files",
    status_code=201,
    dependencies=[Depends(require_auth)],
    max_body_size=100 * 1024 * 1024,
    form_limits={"max_files": 1, "max_fields": 4, "max_part_size": 64 * 1024},
)
async def upload_file(
    session_id: UUID, file: Annotated[UploadFile, Upload()], runtime: RuntimeDep
) -> FileView:
    name = file.filename or ""
    if not _safe_component(name):
        raise HTTPException(400, "Invalid filename")

    key = ObjectStorage.new_key(session_id)
    async with runtime.sessionmaker() as db, db.begin():
        session = await _editable_session(db, session_id)
        if store.upload_is_active(session):
            raise HTTPException(409, "Session already has an upload in progress")
        session.upload_key = key
        session.upload_started_at = datetime.now(UTC)
    path = f"files/{name}"

    async def register(upload_id: str) -> None:
        await store.register_object_write(runtime.sessionmaker, session_id, key, upload_id)

    try:
        with TemporaryDirectory(prefix="fleet-upload-") as temporary:
            local = Path(temporary) / "upload"
            with local.open("wb") as destination:
                while chunk := await file.read(1024 * 1024):
                    destination.write(chunk)
            digest, size = await runtime.storage.put(key, local, register=register)
        record = await store.upsert_file_record(
            runtime.sessionmaker,
            session_id,
            path=path,
            sha256=digest,
            size_bytes=size,
            kind="input",
            object_key=key,
            expected_upload=key,
        )
    except (Exception, asyncio.CancelledError):
        with suppress(SQLAlchemyError):
            await store.release_upload(runtime.sessionmaker, session_id, key)
        raise
    finally:
        await _try_object_cleanup(runtime, session_id)
    return FileView.model_validate(record)


@app.get("/api/sessions/{session_id}/files", dependencies=[Depends(require_auth)])
async def session_files(session_id: UUID, db: DbDep) -> list[FileView]:
    await _session(db, session_id)
    files = await db.scalars(
        select(store.File).where(store.File.session_id == session_id).order_by(store.File.path)
    )
    return [FileView.model_validate(file) for file in files]


class WorkspaceEntry(BaseModel):
    path: str
    size_bytes: int
    revision: str


def _browsable_workspace_path(path: str) -> bool:
    parts = path.split("/")
    return parts[0] != "artifacts" and ".git" not in parts and all(map(_safe_component, parts))


async def _browse_sandbox(runtime: Runtime, session_id: UUID) -> tuple[AsyncSandbox | None, int]:
    async with runtime.sessionmaker() as db:
        session = await _session(db, session_id)
        sandbox_id = session.daytona_sandbox_id
    if sandbox_id is None or session.sandbox_needs_cleanup:
        return None, 0
    effective = await _effective_settings(runtime)
    try:
        sandbox = await get_running_sandbox(runtime.daytona, sandbox_id)
    except DaytonaError:
        sandbox = None
    return sandbox, effective.daytona_exec_timeout_seconds


@app.get("/api/sessions/{session_id}/workspace", dependencies=[Depends(require_auth)])
async def browse_workspace(session_id: UUID, runtime: RuntimeDep) -> list[WorkspaceEntry]:
    sandbox, timeout = await _browse_sandbox(runtime, session_id)
    records = await store.list_session_files(runtime.sessionmaker, session_id)
    entries = {
        record.path: WorkspaceEntry(
            path=record.path, size_bytes=record.size_bytes, revision=record.sha256
        )
        for record in records
        if record.kind == "input" and _browsable_workspace_path(record.path)
    }
    if sandbox is None:
        return sorted(entries.values(), key=lambda value: value.path)
    try:
        workspace = await list_workspace_files(sandbox, timeout=timeout)
    except WorkspaceBrowseLimit as error:
        raise HTTPException(413, str(error)) from error
    except (DaytonaError, RuntimeError, ValueError):
        workspace = []
    for entry in workspace:
        if entry["path"] not in entries and _browsable_workspace_path(entry["path"]):
            entries[entry["path"]] = WorkspaceEntry.model_validate(entry)
    return sorted(entries.values(), key=lambda value: value.path)


@app.get(
    "/api/sessions/{session_id}/workspace/{file_path:path}", dependencies=[Depends(require_auth)]
)
async def download_workspace_entry(
    session_id: UUID, file_path: str, runtime: RuntimeDep
) -> Response:
    if not _browsable_workspace_path(file_path):
        raise HTTPException(400, "Invalid workspace path")
    if _valid_file_path(file_path):
        try:
            stored = await _workspace_file(runtime, session_id, file_path)
        except HTTPException as error:
            if error.status_code != 404:
                raise
        else:
            if stored.size_bytes > 1024 * 1024:
                raise HTTPException(413, "Workspace file preview supports files up to 1 MiB")
            content = await _read_workspace_file(runtime, stored, max_bytes=1024 * 1024)
            return _workspace_response(file_path, content)
    sandbox, timeout = await _browse_sandbox(runtime, session_id)
    if sandbox is None:
        raise HTTPException(404, "File not found")
    try:
        content = await read_browsable_workspace_file(sandbox, file_path, timeout=timeout)
    except WorkspaceBrowseLimit as error:
        raise HTTPException(413, str(error)) from error
    if content is None:
        raise HTTPException(404, "File not found")
    return _workspace_response(file_path, content)


async def _workspace_file(runtime: Runtime, session_id: UUID, file_path: str) -> store.File:
    if not _valid_file_path(file_path):
        raise HTTPException(400, "Invalid workspace path")
    async with runtime.sessionmaker() as db:
        await _session(db, session_id)
        file = await db.scalar(
            select(store.File).where(
                store.File.session_id == session_id, store.File.path == file_path
            )
        )
    if file is None:
        raise HTTPException(404, "File not found")
    return file


async def _copy_committed_file(
    runtime: Runtime,
    record: store.File,
    path: Path,
    *,
    max_bytes: int | None = None,
    expected_sha: str | None = None,
) -> None:
    for attempt in range(2):
        if expected_sha is not None and record.sha256 != expected_sha:
            raise StorageIntegrityError("The artifact changed")
        if max_bytes is not None and record.size_bytes > max_bytes:
            raise HTTPException(413, "Workspace file exceeds the preview size limit")
        try:
            await runtime.storage.download(
                record.object_key, path, sha256=record.sha256, size=record.size_bytes
            )
            return
        except StorageObjectMissing:
            if attempt:
                raise
            # A detached manifest snapshot can lose its object to a concurrent replacement.
            current = await _workspace_file(runtime, record.session_id, record.path)
            if current.object_key == record.object_key:
                raise
            record = current


async def _read_workspace_file(
    runtime: Runtime, record: store.File, *, max_bytes: int, expected_sha: str | None = None
) -> bytes:
    with TemporaryDirectory(prefix="fleet-read-") as temporary:
        path = Path(temporary) / "file"
        await _copy_committed_file(
            runtime, record, path, max_bytes=max_bytes, expected_sha=expected_sha
        )
        return path.read_bytes()


def _attachment_headers(file_path: str) -> dict[str, str]:
    filename = quote(file_path.rsplit("/", 1)[-1], safe="")
    return {"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"}


def _workspace_response(file_path: str, content: bytes) -> Response:
    return Response(
        content,
        media_type="application/octet-stream",
        headers=_attachment_headers(file_path),
    )


@app.get("/api/sessions/{session_id}/files/{file_path:path}", dependencies=[Depends(require_auth)])
async def download_file(session_id: UUID, file_path: str, runtime: RuntimeDep) -> Response:
    record = await _workspace_file(runtime, session_id, file_path)
    with NamedTemporaryFile(prefix="fleet-download-", delete=False) as temporary:
        path = Path(temporary.name)
    try:
        await _copy_committed_file(runtime, record, path)
    except (Exception, asyncio.CancelledError):
        path.unlink(missing_ok=True)
        raise
    return _TemporaryFileResponse(
        path,
        headers=_attachment_headers(file_path),
        media_type="application/octet-stream",
    )


class _TemporaryFileResponse(FileResponse):
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            Path(self.path).unlink(missing_ok=True)


@app.get("/api/memories", dependencies=[Depends(require_auth)])
async def list_memories(
    db: DbDep,
    session_id: UUID | None = None,
    approved: bool | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    memory_id: UUID | None = None,
) -> list[MemoryView]:
    query = select(store.Memory).order_by(store.Memory.id.desc())
    if session_id is not None:
        query = query.where(store.Memory.session_id == session_id)
    if approved is not None:
        query = query.where(store.Memory.approved.is_(approved))
    if memory_id is not None:
        query = query.where(store.Memory.id == memory_id)
    return [MemoryView.model_validate(memory) for memory in await db.scalars(query.limit(limit))]


@app.patch("/api/memories/{memory_id}", dependencies=[Depends(require_auth)])
async def edit_memory(memory_id: UUID, payload: MemoryEdit, db: DbDep) -> MemoryView:
    changes = payload.model_dump(exclude_none=True)
    if "content" in changes:
        changes["content"] = agent.memory_fact(changes["content"])
        if not changes["content"]:
            raise HTTPException(422, "Memory content is required")
    if not changes:
        raise HTTPException(422, "A content or approval change is required")
    async with db.begin():
        memory = await db.scalar(
            update(store.Memory)
            .where(store.Memory.id == memory_id)
            .values(**changes)
            .returning(store.Memory)
        )
        if memory is None:
            raise HTTPException(404, "Memory not found")
    return MemoryView.model_validate(memory)


@app.delete("/api/memories/{memory_id}", status_code=204, dependencies=[Depends(require_auth)])
async def delete_memory(memory_id: UUID, db: DbDep) -> None:
    async with db.begin():
        memory = await db.get(store.Memory, memory_id)
        if memory is None:
            raise HTTPException(404, "Memory not found")
        await db.delete(memory)


@app.post("/api/sessions/{session_id}/runs/{run_id}/refine", dependencies=[Depends(require_auth)])
async def refine_turn(session_id: UUID, run_id: UUID, runtime: RuntimeDep) -> RefineTurnView:
    source = await store.get_turn_for_refinement(runtime.sessionmaker, session_id, run_id)
    if source is None:
        raise HTTPException(404, "Turn not found")
    run, existing = source
    if run.status != "completed":
        raise HTTPException(409, "Only completed turns can be refined")
    if existing is not None:
        return RefineTurnView(memory=MemoryView.model_validate(existing))
    skills, observations = agent.refinement_evidence(run.ui_parts)
    if not observations:
        raise HTTPException(409, "This turn has no saved RLM telemetry")
    effective = await _effective_settings(runtime)
    try:
        async with asyncio.timeout(30):
            lesson = await agent.refine_turn(
                run.prompt, skills, observations, lm=_request_sub_lm(runtime, effective)
            )
    except TimeoutError as error:
        raise HTTPException(504, "Refinement timed out. Try again.") from error
    except Exception as error:
        _LOG.warning("Refinement failed for %s (%s)", run.id, type(error).__name__)
        raise HTTPException(502, "Fleet could not refine this turn. Try again.") from error
    if lesson is None:
        return RefineTurnView(memory=None)
    memory = await store.save_turn_lesson(
        runtime.sessionmaker,
        session_id,
        run_id,
        content=lesson.content,
        skill_name=lesson.skill_name,
    )
    return RefineTurnView(memory=MemoryView.model_validate(memory))


@app.get("/api/skills", dependencies=[Depends(require_auth)])
async def list_skills(runtime: RuntimeDep) -> list[SkillView]:
    registry = await _skill_registry(runtime)
    return [
        SkillView.model_validate(skill) for skill in registry.available_skills(runtime.settings)
    ]


@app.get("/api/skills/{name}", dependencies=[Depends(require_auth)])
async def get_skill(name: str, runtime: RuntimeDep) -> SkillDetail:
    registry = await _skill_registry(runtime)
    for skill in registry.available_skills(runtime.settings):
        if skill.name == name:
            return SkillDetail.model_validate(skill)
    raise HTTPException(404, "Skill not found")


@app.post(
    "/api/sessions/{session_id}/skills", status_code=201, dependencies=[Depends(require_auth)]
)
async def install_skill(session_id: UUID, payload: SkillInstall, runtime: RuntimeDep) -> SkillView:
    if not payload.path.startswith("artifacts/") or not payload.path.endswith("/SKILL.md"):
        raise HTTPException(400, "Only generated SKILL.md artifacts can be installed")
    file = await _workspace_file(runtime, session_id, payload.path)
    if file.kind != "artifact":
        raise HTTPException(400, "Only generated SKILL.md artifacts can be installed")
    if file.size_bytes > MAX_SKILL_BYTES:
        raise HTTPException(413, "SKILL.md must be at most 64 KiB")
    if file.sha256 != payload.sha256:
        raise HTTPException(409, "The artifact changed. Reopen its preview and try again.")
    try:
        content = await _read_workspace_file(
            runtime, file, max_bytes=MAX_SKILL_BYTES, expected_sha=payload.sha256
        )
    except StorageIntegrityError as error:
        raise HTTPException(
            409, "The artifact changed. Reopen its preview and try again."
        ) from error
    try:
        skill = await asyncio.to_thread(
            runtime.skills.install, runtime.settings.skills_dir, content
        )
    except FileExistsError as error:
        raise HTTPException(409, str(error)) from error
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    except OSError as error:
        _LOG.exception("Could not store installed Skill")
        raise HTTPException(503, "Could not store the skill") from error
    return SkillView.model_validate(skill)


@dataclass
class Turn:
    run_id: UUID
    session_id: UUID
    prompt: str
    pinned_skills: list[str]
    skills: SkillRegistry
    deadline: float
    worker: asyncio.Task[list[dict]] | None = None
    finalizing: bool = False
    finalized: bool = False
    sandbox_for_cleanup: str | None = None


async def _finish_root(
    sessionmaker: store.SessionFactory,
    turn: Turn,
    status: store.TerminalStatus,
    *,
    child_snapshots: dict[str, list[dict]] | None = None,
    **result,
) -> bool:
    if status != "completed":
        running_children = (
            update(store.Run)
            .where(
                store.Run.root_run_id == turn.run_id,
                store.Run.parent_run_id.is_not(None),
                store.Run.status == "running",
            )
            .values(status=status, finished_at=func.now())
        )
        async with sessionmaker() as db, db.begin():
            for child_id, parts in (child_snapshots or {}).items():
                await db.execute(
                    running_children.where(store.Run.id == UUID(child_id)).values(ui_parts=parts)
                )
            # A child-start transaction can commit before its interrupted callback returns.
            await db.execute(running_children)
    committed = await store.finish_run(
        sessionmaker,
        turn.run_id,
        status=status,
        retire_sandbox_id=turn.sandbox_for_cleanup,
        **result,
    )
    turn.finalized = True
    return committed


async def _prepare_turn(
    session_id: UUID,
    payload: ChatPayload,
    response: Response,
    runtime: RuntimeDep,
    _slot: TurnConcurrencySlot,
) -> AsyncIterator[Turn]:
    prompt = payload.latest_prompt()
    registry = await _skill_registry(runtime)
    mentioned = [
        skill.name
        for skill in registry.skills
        if re.search(rf"(?<![\w@])@{re.escape(skill.name)}(?![\w-])", prompt)
    ]
    pinned = list(
        dict.fromkeys([*(name.removeprefix("@") for name in payload.pinned_skills), *mentioned])
    )
    skills_by_name = {skill.name: skill for skill in registry.skills}
    if set(pinned) - skills_by_name.keys():
        raise HTTPException(422, "Unknown pinned Skill")
    for name in pinned:
        if reason := skills_by_name[name].unavailable_reason(runtime.settings):
            raise HTTPException(422, reason)

    sessionmaker = runtime.sessionmaker
    await store.get_session_or_raise(sessionmaker, session_id)
    deadline = asyncio.get_running_loop().time() + runtime.settings.stale_run_timeout_seconds - 5
    try:
        try:
            run = await store.acquire_root_run(sessionmaker, session_id, prompt)
        except store.UploadConflictError:
            raise
        except store.TurnConflictError as error:
            reaped = await store.reap_stale_runs(
                sessionmaker,
                runtime.settings.stale_run_timeout_seconds,
                session_id=session_id,
            )
            if reaped <= 0:
                raise HTTPException(409, "Session already has a running turn") from error
            try:
                run = await store.acquire_root_run(sessionmaker, session_id, prompt)
            except store.UploadConflictError:
                raise
            except store.TurnConflictError as retry_error:
                raise HTTPException(409, "Session already has a running turn") from retry_error
    except store.UploadConflictError as error:
        raise HTTPException(409, "Session already has an upload in progress") from error
    except IntegrityError:
        # Resolve a deletion that raced the existence check.
        await store.get_session_or_raise(sessionmaker, session_id)
        raise

    turn = Turn(run.id, session_id, prompt, pinned, registry, deadline)
    response.headers[_STREAM_HEADER] = "v1"
    # FastAPI's native SSE response supplies X-Accel-Buffering: no.
    try:
        yield turn
    finally:
        if turn.worker is not None:
            if not turn.finalizing and not turn.worker.done():
                turn.worker.cancel()
            with suppress(asyncio.CancelledError):
                await turn.worker
        if not turn.finalized:
            await _finish_root(sessionmaker, turn, "cancelled")


async def _chat_sandbox(
    runtime: Runtime,
    session: store.Session,
    settings: Settings | None = None,
    *,
    owner_run_id: UUID,
) -> AsyncSandbox:
    effective = settings if settings is not None else await _effective_settings(runtime)
    return await _ensure_bound_sandbox(
        runtime,
        effective,
        session.id,
        session.daytona_sandbox_id,
        owner_run_id=owner_run_id,
        needs_cleanup=session.sandbox_needs_cleanup,
    )


def _file_manifest(file: store.File) -> dict:
    return {
        "path": file.path,
        "kind": file.kind,
        "sha256": file.sha256,
        "size_bytes": file.size_bytes,
    }


_HYDRATE_CONCURRENCY = 4


async def _hydrate_workspace(
    runtime: Runtime,
    sandbox: AsyncSandbox,
    files: list[store.File],
    timeout: int,
    *,
    reused: bool,
) -> list[store.File]:
    """Make the managed workspace match the file records and return the transferred files."""
    if not all(_valid_file_path(file.path) for file in files):
        raise StorageError("Invalid durable workspace path")
    kept: set[str] = set()
    if reused:
        expected = {file.path: (file.sha256, file.size_bytes) for file in files}
        try:
            kept = await reconcile_managed_workspace(sandbox, expected, timeout=timeout)
        except (DaytonaError, RuntimeError, TimeoutError, ValueError) as error:
            _LOG.warning("Workspace audit failed; resetting it (%s)", type(error).__name__)
            reused = False
    if not reused:
        await reset_managed_workspace(sandbox, timeout=timeout)
    missing = [file for file in files if file.path not in kept]
    limit = asyncio.Semaphore(_HYDRATE_CONCURRENCY)
    with TemporaryDirectory(prefix="fleet-hydrate-") as temporary:

        async def transfer(index: int, file: store.File) -> None:
            path = Path(temporary) / str(index)
            async with limit:
                await runtime.storage.download(
                    file.object_key, path, sha256=file.sha256, size=file.size_bytes
                )
                await upload_workspace_file(
                    sandbox,
                    path,
                    file.path,
                    sha256=file.sha256,
                    size=file.size_bytes,
                    timeout=timeout,
                )
                path.unlink()

        try:
            async with asyncio.TaskGroup() as group:
                for index, file in enumerate(missing):
                    group.create_task(transfer(index, file))
        except ExceptionGroup as failures:
            raise failures.exceptions[0] from None
    return missing


async def _retire_workspace(runtime: Runtime, sandbox_id: str) -> None:
    try:
        await delete_sandbox_by_id(runtime.daytona, sandbox_id)
    except DaytonaError as error:
        _LOG.warning("Could not retire Daytona VM %s (%s)", sandbox_id, type(error).__name__)


async def _consolidate(runtime: Runtime, turn: Turn, answer: str, memories: list[dict]) -> None:
    async def save(candidate: agent.MemoryCandidate) -> None:
        if candidate.content is None:
            return
        await store.save_or_update_memory(
            runtime.sessionmaker,
            session_id=turn.session_id if candidate.scope == "session" else None,
            content=candidate.content,
            approved=candidate.approved,
            memory_id=UUID(candidate.supersedes_id) if candidate.supersedes_id else None,
        )

    try:
        effective = await _effective_settings(runtime)
        decision_lm = _request_decision_lm(runtime, effective)
        candidates = await store.search_memories(
            runtime.sessionmaker,
            turn.session_id,
            f"{turn.prompt}\n{answer}",
            limit=3,
            include_unapproved=True,
        )
        merged = [
            {
                "id": str(record.id),
                "content": record.content,
                "scope": "global" if record.session_id is None else "session",
                "approved": bool(record.approved),
            }
            for record in candidates
        ]
        seen_ids = {item["id"] for item in merged}
        for memory in memories:
            if memory["id"] not in seen_ids:
                merged.append(memory)
                seen_ids.add(memory["id"])
        await agent.consolidate_memory(
            effective,
            turn.prompt,
            answer,
            merged[:3],
            on_save=save,
            decision_lm=decision_lm,
        )
    except Exception as error:
        _LOG.warning("Memory consolidation failed for %s (%s)", turn.run_id, type(error).__name__)


async def _run_turn(
    request: Request,
    runtime: Runtime,
    turn: Turn,
    queue: asyncio.Queue[dict],
    background: BackgroundTasks,
) -> list[dict]:
    if not runtime.settings.mlflow_tracking_uri:
        return await _execute_turn(request, runtime, turn, queue, background, trace=None)
    with workspace_span(
        "fleet.turn",
        root=True,
        attributes={"fleet.run_id": str(turn.run_id), "fleet.turn.status": "failed"},
    ) as trace:
        mlflow.update_current_trace(
            tags={"fleet.run_id": str(turn.run_id)}, session_id=str(turn.session_id)
        )
        try:
            return await _execute_turn(request, runtime, turn, queue, background, trace=trace)
        finally:
            if span := mlflow.get_current_active_span():
                _LOG.info("Turn %s MLflow trace %s", turn.run_id, span.trace_id)


def _trace_link(runtime: Runtime, trace: LiveSpan | None) -> str | None:
    if (
        trace is None
        or not runtime.mlflow_experiment_id
        or not runtime.settings.mlflow_tracking_uri
    ):
        return None
    try:
        uri = urlsplit(runtime.settings.mlflow_tracking_uri)
        if uri.scheme not in {"http", "https"} or not uri.hostname or uri.username is not None:
            return None
    except ValueError:
        return None
    experiment = quote(runtime.mlflow_experiment_id, safe="")
    trace_id = quote(trace.trace_id, safe="")
    return urlunsplit(
        (
            uri.scheme,
            uri.netloc,
            uri.path.rstrip("/") + "/",
            "",
            f"/experiments/{experiment}/traces?traceId={trace_id}",
        )
    )


async def _execute_turn(
    request: Request,
    runtime: Runtime,
    turn: Turn,
    queue: asyncio.Queue[dict],
    background: BackgroundTasks,
    *,
    trace: LiveSpan | None,
) -> list[dict]:
    sessionmaker = runtime.sessionmaker
    root_id = str(turn.run_id)
    status: store.TerminalStatus = "failed"
    error_text = None
    result = None
    terminal_frames: list[dict] = []
    artifacts: list[store.StagedFile] = []
    sandbox: AsyncSandbox | None = None
    hydrated = False
    buffering_terminal = False
    failure_parts = None
    committed = False
    timed_out = False
    telemetry = agent.TurnTelemetry()

    async def send_frame(frame: dict) -> None:
        if buffering_terminal:
            # Publish newly discovered paths and completion after their Neon writes.
            terminal_frames.append(frame)
        else:
            await queue.put(frame)

    async def stage_artifact(key: str, local: Path) -> tuple[str, int]:
        async def register(upload_id: str) -> None:
            await store.register_object_write(
                sessionmaker, turn.session_id, key, upload_id, run_id=turn.run_id
            )

        return await runtime.storage.put(key, local, register=register)

    stream = ui_stream.UiStream(root_id, send_frame)

    def details(outcome: str) -> dict:
        completion = telemetry.completion if outcome == "completed" else outcome
        return {
            "runtime": "dspy.RLM" if telemetry.started_at is not None else None,
            "root_actions": telemetry.root_actions,
            "child_rlms": telemetry.child_rlms,
            "sub_lm_queries": telemetry.sub_lm_queries,
            "elapsed_ms": telemetry.elapsed_ms,
            "completion": completion,
            "trace_url": _trace_link(runtime, trace),
        }

    async def start_child(child: agent.ChildRun) -> UUID:
        call_id = stream.reserve_call(child.parent_id)
        row = await store.create_child_run(
            sessionmaker, UUID(child.parent_id), kind=child.kind, prompt=child.prompt
        )
        stream.add_child(str(row.id), child.parent_id, call_id)
        return row.id

    async def finish_child(
        child: agent.ChildRun,
        status: agent.ChildStatus,
        answer: str | None,
        error: str | None,
    ) -> None:
        committed = await store.finish_run(
            sessionmaker,
            UUID(child.id),
            status=status,
            answer=answer,
            ui_parts=stream.snapshot(child.id),
            error=error,
        )
        if not committed:
            raise store.TurnConflictError("Child run is no longer active")
        if status == "cancelled":
            stream.discard_child(child.id)

    try:
        async with asyncio.timeout_at(turn.deadline):
            effective = await _effective_settings(runtime)
            # Read history after acquiring the root so it includes the previous completed turn.
            async with sessionmaker() as db:
                session = await _session(db, turn.session_id)
            prior_turns = await store.get_session_prior_turns(
                sessionmaker,
                turn.session_id,
                max_turns=effective.history_max_turns,
                max_chars=effective.history_max_chars,
            )
            with workspace_span("daytona.sandbox.acquire") as acquisition:
                existing_sandbox_id = session.daytona_sandbox_id
                sandbox = await _chat_sandbox(runtime, session, effective, owner_run_id=turn.run_id)
                if acquisition is not None:
                    acquisition.set_attributes(
                        {
                            "daytona.sandbox_id": sandbox.id,
                            "daytona.sandbox.reused": sandbox.id == existing_sandbox_id,
                        }
                    )
            files = await store.list_session_files(sessionmaker, turn.session_id)
            reused = sandbox.id == existing_sandbox_id
            with workspace_span(
                "fleet.workspace.hydrate",
                attributes={"fleet.file_count": len(files), "daytona.sandbox.reused": reused},
            ) as hydration:
                transferred = await _hydrate_workspace(
                    runtime, sandbox, files, effective.daytona_exec_timeout_seconds, reused=reused
                )
                if hydration is not None:
                    hydration.set_attributes(
                        {
                            "fleet.workspace.transferred_count": len(transferred),
                            "fleet.workspace.transferred_bytes": sum(
                                file.size_bytes for file in transferred
                            ),
                        }
                    )
            hydrated = True
            decision_lm = _request_decision_lm(runtime, effective)
            selection = await asyncio.to_thread(
                select_skills,
                effective,
                turn.skills,
                turn.prompt,
                turn.pinned_skills,
                prior_turns=prior_turns,
                decision_lm=decision_lm,
            )
            records = await store.search_memories(sessionmaker, turn.session_id, turn.prompt)
            selected_skill_names = [skill.name for skill in selection.active_skills]
            procedures = await store.recall_procedures(
                sessionmaker, turn.session_id, turn.prompt, selected_skill_names
            )
            task_memories = records
            if "skill-creator" in selected_skill_names:
                task_memories = await store.list_approved_session_memories(
                    sessionmaker, turn.session_id
                )
            memories = [
                {
                    "id": str(record.id),
                    "content": record.content,
                    "scope": "global" if record.session_id is None else "session",
                    "approved": bool(record.approved),
                }
                for record in records
            ]
            manifest = [_file_manifest(file) for file in files]
            known_files = {file.path: (file.sha256, file.size_bytes) for file in files}

            async def emit(run_id: str, event: agent.RunEvent) -> None:
                if run_id == root_id and isinstance(event, agent.RunResultEvent):
                    return  # The root result becomes visible only after artifacts are committed.
                await stream.publish(run_id, event)
                if isinstance(event, agent.RunStartedEvent) and event.kind == "workspace":
                    await stream.skills(
                        run_id,
                        [
                            (skill.name, selection.scores[skill.name])
                            for skill in selection.active_skills
                        ],
                    )
                    await stream.files(run_id, manifest)

            result = await agent.run_workspace_task(
                effective,
                task=turn.prompt,
                files_manifest=manifest,
                memories=[
                    *(f"Approved procedure\n{record.content}" for record in procedures),
                    *(
                        fact
                        for record in task_memories
                        if (fact := agent.memory_fact(record.content))
                    ),
                ],
                interpreter_factory=make_interpreter_factory(
                    sandbox, effective, asyncio.get_running_loop(), turn_deadline=turn.deadline
                ),
                run_id=root_id,
                emit=emit,
                history=dspy.History.model_validate(session.history_json),
                prior_turns=prior_turns,
                selection=selection,
                host_tools=load_host_tools(runtime.skills, selection, effective),
                model_override=session.model_override or _request_root_lm(runtime, effective),
                sub_lm=_request_sub_lm(runtime, effective),
                optimized_state=runtime.optimized_program_state,
                callbacks=agent.ChildCallbacks(start_child, finish_child),
                telemetry=telemetry,
            )
            with (
                workspace_span("fleet.workspace.publish") as publication,
                TemporaryDirectory(prefix="fleet-publish-") as temporary,
            ):
                local = Path(temporary) / "file"
                for artifact in await list_new_artifacts(sandbox, known_files):
                    path = artifact["path"].removeprefix(f"{WORKSPACE}/")
                    if not (_valid_file_path(path) and path.startswith("artifacts/")):
                        continue
                    await download_workspace_file(
                        sandbox, path, local, timeout=effective.daytona_exec_timeout_seconds
                    )
                    key = ObjectStorage.new_key(turn.session_id)
                    digest, size = await stage_artifact(key, local)
                    if (digest, size) != (artifact["sha256"], artifact["size_bytes"]):
                        raise StorageError("Artifact changed during publication")
                    artifacts.append(
                        {
                            "path": path,
                            "kind": "artifact",
                            "object_key": key,
                            "sha256": digest,
                            "size_bytes": size,
                        }
                    )
                if publication is not None:
                    publication.set_attribute("fleet.file_count", len(artifacts))
            if await request.is_disconnected():
                raise asyncio.CancelledError
            failure_parts = stream.snapshot(root_id)
            buffering_terminal = True
            if artifacts:
                updated_files = {entry["path"]: entry for entry in manifest}
                for old_path in store.replaced_file_paths(
                    updated_files, (artifact["path"] for artifact in artifacts)
                ):
                    del updated_files[old_path]
                for entry in artifacts:
                    updated_files[entry["path"]] = {
                        field: value for field, value in entry.items() if field != "object_key"
                    }
                await stream.files(
                    root_id, sorted(updated_files.values(), key=lambda file: file["path"])
                )
            await send_frame(stream.turn_details(details("completed")))
            await stream.publish(root_id, agent.RunResultEvent(result.answer))
            status = "completed"
    except asyncio.CancelledError:
        status = "cancelled"
        turn.finalizing = True
        if sandbox is not None and not hydrated:
            turn.sandbox_for_cleanup = sandbox.id
        raise
    except Exception as error:
        turn.finalizing = True
        timed_out = isinstance(error, TimeoutError)
        if sandbox is not None and (not hydrated or timed_out or sandbox_cleanup_failed(error)):
            turn.sandbox_for_cleanup = sandbox.id
        current_task = asyncio.current_task()
        if current_task is not None and current_task.cancelling():
            status = "cancelled"
            raise asyncio.CancelledError from error
        if timed_out:
            error_text = "Turn timed out"
        else:
            error_text = (
                _WORKSPACE_STARTUP_HELP if isinstance(error, SandboxStartupError) else "Turn failed"
            )
        _LOG.warning("Turn %s failed (%s)", turn.run_id, type(error.__cause__ or error).__name__)
    finally:
        turn.finalizing = True
        interruption_frames = []
        if status != "completed":
            interruption_frames = stream.interrupt_web_calls()
            terminal_frames[:] = [
                *interruption_frames,
                stream.turn_details(details("timeout" if timed_out else status)),
            ]
        with workspace_span("fleet.turn.finalize") as finalization:
            fence_committed = False
            try:
                persisted: dict[str, Any] = {}
                if status == "completed" and result is not None:
                    persisted = {
                        "answer": result.answer,
                        "history_json": result.history.model_dump(mode="json"),
                        "files": artifacts,
                    }
                committed = await _finish_root(
                    sessionmaker,
                    turn,
                    status,
                    child_snapshots=stream.pending_child_snapshots()
                    if status != "completed"
                    else None,
                    ui_parts=stream.snapshot(root_id),
                    error=error_text,
                    **persisted,
                )
                fence_committed = committed
            except Exception as error:
                _LOG.warning("Could not finalize turn %s (%s)", turn.run_id, type(error).__name__)
                if not turn.finalized:
                    failed_details = stream.turn_details(
                        details("cancelled" if status == "cancelled" else "failed")
                    )
                    # Keep the failed transcript free of an answer that did not commit.
                    if failure_parts is not None:
                        failure_parts.append(failed_details)
                    terminal_frames[:] = [*interruption_frames, failed_details]
                    fence_committed = await _finish_root(
                        sessionmaker,
                        turn,
                        "cancelled" if status == "cancelled" else "failed",
                        error="Could not persist turn",
                        ui_parts=failure_parts or stream.snapshot(root_id),
                    )
                status = "failed"
                committed = False
            if finalization is not None and not committed:
                finalization.set_status("ERROR")
        with workspace_span("fleet.workspace.cleanup"):
            if fence_committed and turn.sandbox_for_cleanup is not None:
                await _retire_workspace(runtime, turn.sandbox_for_cleanup)
            await _try_object_cleanup(runtime, turn.session_id)
        if trace is not None:
            outcome = "failed" if status == "completed" and not committed else status
            trace.set_attributes(
                {
                    "fleet.turn.status": outcome,
                    "fleet.rlm.root_actions": telemetry.root_actions,
                    "fleet.rlm.child_rlms": telemetry.child_rlms,
                    "fleet.rlm.sub_lm_queries": telemetry.sub_lm_queries,
                    "fleet.rlm.completion": details("timeout" if timed_out else outcome)[
                        "completion"
                    ],
                }
            )
            trace.set_status("OK" if outcome == "completed" else "ERROR")

    if status == "completed" and committed and result is not None:
        background.add_task(_consolidate, runtime, turn, result.answer, memories)
        return terminal_frames
    error_frame = ui_stream.error_frame(error_text or "Could not persist turn")
    if status != "completed" and fence_committed:
        return [*terminal_frames, error_frame]
    return [error_frame]


@app.post(
    "/api/sessions/{session_id}/chat",
    response_class=EventSourceResponse,
    dependencies=[Depends(require_auth)],
    responses={409: {"description": "Session already has a running turn"}},
)
async def chat(
    request: Request,
    runtime: RuntimeDep,
    turn: Annotated[Turn, Depends(_prepare_turn)],
    background: BackgroundTasks,
) -> AsyncIterator[ServerSentEvent]:
    queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=1)
    turn.worker = asyncio.create_task(_run_turn(request, runtime, turn, queue, background))
    seq = 0
    while not await request.is_disconnected():
        if turn.worker.done() and queue.empty():
            if not turn.worker.cancelled():
                for frame in turn.worker.result():
                    if await request.is_disconnected():
                        return
                    yield ServerSentEvent(id=f"{turn.run_id}:{seq}", data=frame)
                    seq += 1
                yield ServerSentEvent(raw_data="[DONE]")
            return
        try:
            frame = await asyncio.wait_for(queue.get(), timeout=0.1)
        except TimeoutError:
            continue
        if await request.is_disconnected():
            return
        yield ServerSentEvent(id=f"{turn.run_id}:{seq}", data=frame)
        seq += 1
