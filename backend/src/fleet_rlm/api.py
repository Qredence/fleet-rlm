"""Stateless HTTP and native POST SSE for the Fleet backend."""

import asyncio
import hashlib
import json
import logging
import re
import secrets
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any, Literal
from urllib.parse import quote
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
from fastapi.responses import JSONResponse
from fastapi.sse import EventSourceResponse, ServerSentEvent
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
    delete_sandbox,
    delete_sandbox_by_id,
    delete_workspace_file,
    download_workspace_file,
    ensure_session_sandbox,
    list_new_artifacts,
    list_workspace_files,
    make_daytona_client,
    make_interpreter_factory,
    read_browsable_workspace_file,
    upload_workspace_file,
)
from .skills import MAX_SKILL_BYTES, SkillRegistry, select_skills

_STREAM_HEADER = "x-vercel-ai-ui-message-stream"
_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class Runtime:
    settings: Settings
    sessionmaker: store.SessionFactory
    daytona: AsyncDaytona
    skills: SkillRegistry
    lm: dspy.BaseLM
    sub_lm: dspy.BaseLM
    decision_lm: dspy.BaseLM
    turn_semaphore: asyncio.Semaphore
    optimized_program_state: dict[str, Any] | None


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
        if settings.mlflow_tracking_uri:
            mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
            mlflow.set_experiment(settings.mlflow_experiment)
            mlflow.dspy.autolog()
        skills = SkillRegistry.load(settings.skills_dir)
        lm = agent._model(settings, settings.llm_model)
        sub_lm = agent._model(settings, settings.sub_llm_model)
        decision_lm = dspy.LM(settings.decision_model)
        optimized_program_state = _load_optimized_program_state(settings, sub_lm)
        turn_semaphore = asyncio.Semaphore(settings.max_concurrent_turns_per_worker)
        async with make_daytona_client(settings) as daytona:
            app.state.runtime = Runtime(
                settings=settings,
                sessionmaker=sessionmaker,
                daytona=daytona,
                skills=skills,
                lm=lm,
                sub_lm=sub_lm,
                decision_lm=decision_lm,
                turn_semaphore=turn_semaphore,
                optimized_program_state=optimized_program_state,
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
    created_at: datetime
    updated_at: datetime


_NON_EMPTY_SETTINGS_FIELDS = (
    "llm_model",
    "sub_llm_model",
    "decision_model",
)
_NULLABLE_SETTINGS_FIELDS = ("daytona_target",)


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
    daytona_target: str | None = None
    daytona_auto_stop_minutes: int = Field(ge=1, le=1440)
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
    daytona_target: str | None = None
    daytona_auto_stop_minutes: int | None = Field(default=None, ge=1, le=1440)
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

    @field_validator(*_NULLABLE_SETTINGS_FIELDS)
    @classmethod
    def normalize_nullable(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class WorkspaceSettingsStatus(BaseModel):
    database_configured: bool
    daytona_configured: bool
    daytona_volume_id: str
    daytona_snapshot: str
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
    "ask your administrator to check the Daytona snapshot and volume."
)


@app.exception_handler(SandboxStartupError)
async def sandbox_startup_error(_request: Request, error: SandboxStartupError) -> JSONResponse:
    _LOG.warning("Workspace startup failed (%s)", type(error.__cause__ or error).__name__)
    return JSONResponse({"detail": _WORKSPACE_STARTUP_HELP}, status_code=503)


@app.exception_handler(DaytonaError)
async def daytona_error(_request: Request, error: DaytonaError) -> JSONResponse:
    _LOG.warning("Daytona request failed (%s)", type(error).__name__)
    return JSONResponse({"detail": "Workspace service unavailable"}, status_code=502)


async def _session(
    db: AsyncSession, session_id: UUID, *, for_update: bool = False
) -> store.Session:
    session = await db.get(store.Session, session_id, with_for_update=True if for_update else None)
    if session is None:
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
    return SessionView.model_validate(await _session(db, session_id))


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
            .where(store.Session.id == session_id)
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
            daytona_volume_id=base.daytona_volume_id,
            daytona_snapshot=base.daytona_snapshot,
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
            skills_count=len((await _skill_registry(runtime)).skills),
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
        session = await _editable_session(db, session_id)
        sandbox_id = session.daytona_sandbox_id
        await db.delete(session)
    if sandbox_id:
        try:
            await delete_sandbox_by_id(runtime.daytona, sandbox_id)
        except DaytonaError as error:
            if error.status_code != 404:
                _LOG.warning(
                    "Could not delete Daytona sandbox %s for deleted session %s (%s)",
                    sandbox_id,
                    session_id,
                    type(error).__name__,
                )


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
) -> tuple[AsyncSandbox, str]:
    sandbox, sandbox_id, created = await ensure_session_sandbox(
        runtime.daytona, effective, session_id, existing_sandbox_id
    )
    if not created:
        return sandbox, sandbox_id
    try:
        winning_id, won = await store.bind_session_sandbox_if_unset(
            runtime.sessionmaker, session_id, sandbox_id
        )
    except (Exception, asyncio.CancelledError):
        with suppress(DaytonaError):
            await delete_sandbox(runtime.daytona, sandbox)
        raise
    if won:
        return sandbox, winning_id
    with suppress(DaytonaError):
        await delete_sandbox(runtime.daytona, sandbox)
    winning_sandbox, _, _ = await ensure_session_sandbox(
        runtime.daytona, effective, session_id, winning_id
    )
    return winning_sandbox, winning_id


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

    async with runtime.sessionmaker() as db:
        session = await _session(db, session_id)
        if await db.scalar(
            select(store.Run.id).where(
                store.Run.session_id == session_id,
                store.Run.parent_run_id.is_(None),
                store.Run.status == "running",
            )
        ):
            raise HTTPException(409, "Session already has a running turn")
        sandbox_id = session.daytona_sandbox_id

    effective = await _effective_settings(runtime)
    path = f"files/{name}"
    timeout = effective.daytona_exec_timeout_seconds
    digest = hashlib.sha256()
    size = 0

    async def chunks() -> AsyncIterator[bytes]:
        nonlocal size
        while chunk := await file.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
            yield chunk

    sandbox, _ = await _ensure_bound_sandbox(runtime, effective, session_id, sandbox_id)
    await upload_workspace_file(sandbox, chunks(), path, timeout=timeout)

    try:
        await store.get_session_or_raise(runtime.sessionmaker, session_id)
        record = await store.upsert_file_record(
            runtime.sessionmaker,
            session_id,
            path=path,
            sha256=digest.hexdigest(),
            size_bytes=size,
            kind="input",
        )
    except store.SessionNotFoundError:
        with suppress(DaytonaError):
            await delete_workspace_file(sandbox, path, timeout=timeout)
        raise
    except IntegrityError:
        try:
            await store.get_session_or_raise(runtime.sessionmaker, session_id)
        except store.SessionNotFoundError:
            with suppress(DaytonaError):
                await delete_workspace_file(sandbox, path, timeout=timeout)
            raise
        raise
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
        sandbox_id = (await _session(db, session_id)).daytona_sandbox_id
    if sandbox_id is None:
        return None, 0
    effective = await _effective_settings(runtime)
    sandbox, _, _ = await ensure_session_sandbox(runtime.daytona, effective, session_id, sandbox_id)
    return sandbox, effective.daytona_exec_timeout_seconds


@app.get("/api/sessions/{session_id}/workspace", dependencies=[Depends(require_auth)])
async def browse_workspace(session_id: UUID, runtime: RuntimeDep) -> list[WorkspaceEntry]:
    sandbox, timeout = await _browse_sandbox(runtime, session_id)
    if sandbox is None:
        return []
    try:
        entries = await list_workspace_files(sandbox, timeout=timeout)
    except WorkspaceBrowseLimit as error:
        raise HTTPException(413, str(error)) from error
    return [
        WorkspaceEntry.model_validate(entry)
        for entry in entries
        if _browsable_workspace_path(entry["path"])
    ]


@app.get(
    "/api/sessions/{session_id}/workspace/{file_path:path}", dependencies=[Depends(require_auth)]
)
async def download_workspace_entry(
    session_id: UUID, file_path: str, runtime: RuntimeDep
) -> Response:
    if not _browsable_workspace_path(file_path):
        raise HTTPException(400, "Invalid workspace path")
    sandbox, timeout = await _browse_sandbox(runtime, session_id)
    if sandbox is None:
        raise HTTPException(404, "File not found")
    try:
        content = await read_browsable_workspace_file(sandbox, file_path, timeout=timeout)
    except WorkspaceBrowseLimit as error:
        raise HTTPException(413, str(error)) from error
    if content is None:
        raise HTTPException(404, "File not found")
    filename = quote(file_path.rsplit("/", 1)[-1], safe="")
    return Response(
        content,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


async def _workspace_file(
    runtime: Runtime, session_id: UUID, file_path: str
) -> tuple[str, store.File]:
    if not _valid_file_path(file_path):
        raise HTTPException(400, "Invalid workspace path")
    async with runtime.sessionmaker() as db:
        session = await _session(db, session_id)
        sandbox_id = session.daytona_sandbox_id
        file = await db.scalar(
            select(store.File).where(
                store.File.session_id == session_id, store.File.path == file_path
            )
        )
    if file is None or sandbox_id is None:
        raise HTTPException(404, "File not found")
    return sandbox_id, file


async def _read_workspace_file(
    runtime: Runtime, session_id: UUID, sandbox_id: str, file_path: str
) -> bytes:
    effective = await _effective_settings(runtime)
    try:
        sandbox, _, _ = await ensure_session_sandbox(
            runtime.daytona, effective, session_id, sandbox_id
        )
        return await download_workspace_file(
            sandbox, file_path, timeout=effective.daytona_exec_timeout_seconds
        )
    except (DaytonaError, SandboxStartupError) as error:
        provider_error = error.__cause__ if isinstance(error, SandboxStartupError) else error
        if isinstance(provider_error, DaytonaError) and provider_error.status_code == 404:
            raise HTTPException(404, "File not found") from error
        raise


@app.get("/api/sessions/{session_id}/files/{file_path:path}", dependencies=[Depends(require_auth)])
async def download_file(session_id: UUID, file_path: str, runtime: RuntimeDep) -> Response:
    sandbox_id, _ = await _workspace_file(runtime, session_id, file_path)
    content = await _read_workspace_file(runtime, session_id, sandbox_id, file_path)
    filename = quote(file_path.rsplit("/", 1)[-1], safe="")
    return Response(
        content,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


@app.get("/api/memories", dependencies=[Depends(require_auth)])
async def list_memories(
    db: DbDep,
    session_id: UUID | None = None,
    approved: bool | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[MemoryView]:
    query = select(store.Memory).order_by(store.Memory.id.desc())
    if session_id is not None:
        query = query.where(store.Memory.session_id == session_id)
    if approved is not None:
        query = query.where(store.Memory.approved.is_(approved))
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


@app.get("/api/skills", dependencies=[Depends(require_auth)])
async def list_skills(runtime: RuntimeDep) -> list[SkillView]:
    registry = await _skill_registry(runtime)
    return [SkillView.model_validate(skill) for skill in registry.skills]


@app.get("/api/skills/{name}", dependencies=[Depends(require_auth)])
async def get_skill(name: str, runtime: RuntimeDep) -> SkillDetail:
    registry = await _skill_registry(runtime)
    for skill in registry.skills:
        if skill.name == name:
            return SkillDetail.model_validate(skill)
    raise HTTPException(404, "Skill not found")


@app.post(
    "/api/sessions/{session_id}/skills", status_code=201, dependencies=[Depends(require_auth)]
)
async def install_skill(session_id: UUID, payload: SkillInstall, runtime: RuntimeDep) -> SkillView:
    if not payload.path.startswith("artifacts/") or not payload.path.endswith("/SKILL.md"):
        raise HTTPException(400, "Only generated SKILL.md artifacts can be installed")
    sandbox_id, file = await _workspace_file(runtime, session_id, payload.path)
    if file.kind != "artifact":
        raise HTTPException(400, "Only generated SKILL.md artifacts can be installed")
    if file.size_bytes > MAX_SKILL_BYTES:
        raise HTTPException(413, "SKILL.md must be at most 64 KiB")
    if file.sha256 != payload.sha256:
        raise HTTPException(409, "The artifact changed. Reopen its preview and try again.")
    content = await _read_workspace_file(runtime, session_id, sandbox_id, payload.path)
    if hashlib.sha256(content).hexdigest() != payload.sha256:
        raise HTTPException(409, "The artifact changed. Reopen its preview and try again.")
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
    worker: asyncio.Task[list[dict]] | None = None
    finalizing: bool = False
    finalized: bool = False


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
    committed = await store.finish_run(sessionmaker, turn.run_id, status=status, **result)
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
    if set(pinned) - {skill.name for skill in registry.skills}:
        raise HTTPException(422, "Unknown pinned Skill")

    sessionmaker = runtime.sessionmaker
    await store.get_session_or_raise(sessionmaker, session_id)
    try:
        try:
            run = await store.acquire_root_run(sessionmaker, session_id, prompt)
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
            except store.TurnConflictError as retry_error:
                raise HTTPException(409, "Session already has a running turn") from retry_error
    except IntegrityError:
        # Resolve a deletion that raced the existence check.
        await store.get_session_or_raise(sessionmaker, session_id)
        raise

    turn = Turn(run.id, session_id, prompt, pinned, registry)
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
    runtime: Runtime, session: store.Session, settings: Settings | None = None
) -> AsyncSandbox:
    effective = settings if settings is not None else await _effective_settings(runtime)
    sandbox, _ = await _ensure_bound_sandbox(
        runtime, effective, session.id, session.daytona_sandbox_id
    )
    return sandbox


def _file_manifest(file: store.File) -> dict:
    return {
        "path": file.path,
        "kind": file.kind,
        "sha256": file.sha256,
        "size_bytes": file.size_bytes,
    }


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
    sessionmaker = runtime.sessionmaker
    root_id = str(turn.run_id)
    status: store.TerminalStatus = "failed"
    error_text = None
    result = None
    terminal_frames: list[dict] = []
    artifacts: list[dict] = []

    async def send_frame(frame: dict) -> None:
        if artifacts or frame["type"] == "finish":
            # Publish newly discovered paths and completion after their Neon writes.
            terminal_frames.append(frame)
        else:
            await queue.put(frame)

    stream = ui_stream.UiStream(root_id, send_frame)

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
        sandbox = await _chat_sandbox(runtime, session, effective)
        files = await store.list_session_files(sessionmaker, turn.session_id)
        decision_lm = _request_decision_lm(runtime, effective)
        selection = await asyncio.to_thread(
            select_skills,
            effective,
            turn.skills,
            turn.prompt,
            turn.pinned_skills,
            decision_lm=decision_lm,
        )
        records = await store.search_memories(sessionmaker, turn.session_id, turn.prompt)
        task_memories = records
        if any(skill.name == "skill-creator" for skill in selection.active_skills):
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

        async def refresh_files() -> list[dict]:
            by_path = {entry["path"]: entry for entry in manifest}
            for artifact in await list_new_artifacts(sandbox, known_files):
                path = artifact["path"].removeprefix(f"{WORKSPACE}/")
                if _valid_file_path(path) and path.startswith("artifacts/"):
                    entry = {**artifact, "path": path, "kind": "artifact"}
                    artifacts.append(entry)
                    by_path[path] = entry
            return sorted(by_path.values(), key=lambda file: file["path"])

        async def emit(run_id: str, event: agent.RunEvent) -> None:
            if run_id == root_id and isinstance(event, agent.RunResultEvent):
                await stream.files(run_id, await refresh_files())
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
                fact for record in task_memories if (fact := agent.memory_fact(record.content))
            ],
            interpreter_factory=make_interpreter_factory(
                sandbox, effective, asyncio.get_running_loop()
            ),
            run_id=root_id,
            emit=emit,
            history=dspy.History.model_validate(session.history_json),
            prior_turns=prior_turns,
            selection=selection,
            model_override=session.model_override or _request_root_lm(runtime, effective),
            sub_lm=_request_sub_lm(runtime, effective),
            optimized_state=runtime.optimized_program_state,
            callbacks=agent.ChildCallbacks(start_child, finish_child),
        )
        if await request.is_disconnected():
            raise asyncio.CancelledError
        status = "completed"
    except asyncio.CancelledError:
        status = "cancelled"
        raise
    except Exception as error:
        current_task = asyncio.current_task()
        if current_task is not None and current_task.cancelling():
            status = "cancelled"
            raise asyncio.CancelledError from error
        error_text = (
            _WORKSPACE_STARTUP_HELP if isinstance(error, SandboxStartupError) else "Turn failed"
        )
        _LOG.warning("Turn %s failed (%s)", turn.run_id, type(error.__cause__ or error).__name__)
    finally:
        turn.finalizing = True
        try:
            persisted = {}
            if status == "completed" and result is not None:
                for artifact in artifacts:
                    await store.upsert_file_record(sessionmaker, turn.session_id, **artifact)
                persisted = {
                    "answer": result.answer,
                    "history_json": result.history.model_dump(mode="json"),
                }
            committed = await _finish_root(
                sessionmaker,
                turn,
                status,
                child_snapshots=stream.pending_child_snapshots() if status != "completed" else None,
                ui_parts=stream.snapshot(root_id),
                error=error_text,
                **persisted,
            )
        except Exception as error:
            _LOG.warning("Could not finalize turn %s (%s)", turn.run_id, type(error).__name__)
            if not turn.finalized:
                await _finish_root(
                    sessionmaker,
                    turn,
                    "cancelled" if status == "cancelled" else "failed",
                    error="Could not persist turn",
                )
            status = "failed"
            committed = False

    if status == "completed" and committed and result is not None:
        background.add_task(_consolidate, runtime, turn, result.answer, memories)
        return terminal_frames
    return [ui_stream.error_frame(error_text or "Could not persist turn")]


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
