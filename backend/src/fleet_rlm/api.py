"""Stateless HTTP and native POST SSE for the Fleet backend."""

import asyncio
import hashlib
import logging
import re
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated
from urllib.parse import quote
from uuid import UUID, uuid4

import dspy
import mlflow
from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from fastapi import File as Upload
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel, ConfigDict, Field
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
    ensure_session_sandbox,
    list_new_artifacts,
    make_daytona_client,
    make_interpreter_factory,
)
from .skills import SkillRegistry, select_skills

_STREAM_HEADER = "x-vercel-ai-ui-message-stream"
_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class Runtime:
    settings: Settings
    sessionmaker: store.SessionFactory
    daytona: AsyncDaytona
    skills: SkillRegistry
    lm: dspy.BaseLM
    decision_lm: dspy.BaseLM
    program: dspy.RLM


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = Settings()
    engine, sessionmaker = store.create_db(settings)
    try:
        await store.reap_stale_runs(sessionmaker, settings.stale_run_timeout_seconds)
        if settings.mlflow_tracking_uri:
            mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
            mlflow.set_experiment(settings.mlflow_experiment)
            mlflow.dspy.autolog()
        skills = SkillRegistry.load(settings.skills_dir)
        async with make_daytona_client(settings) as daytona:
            app.state.runtime = Runtime(
                settings,
                sessionmaker,
                daytona,
                skills,
                agent._model(settings, settings.llm_model),
                dspy.LM(settings.decision_model),
                agent.build_program(settings, []),
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


async def turn_slot() -> AsyncIterator[None]:
    yield


TurnConcurrencySlot = Annotated[None, Depends(turn_slot, max_concurrency=16)]


class SessionCreate(BaseModel):
    title: str = Field(default="New session", min_length=1)


class SessionView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    created_at: datetime


class FileView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    session_id: UUID
    path: str
    sha256: str
    size_bytes: int
    kind: str
    created_at: datetime


class MemoryView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    session_id: UUID | None
    content: str
    approved: bool
    created_at: datetime


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
async def list_sessions(db: DbDep) -> list[SessionView]:
    sessions = await db.scalars(
        select(store.Session).order_by(store.Session.updated_at.desc(), store.Session.id.desc())
    )
    return [SessionView.model_validate(session) for session in sessions]


@app.delete("/api/sessions/{session_id}", status_code=204, dependencies=[Depends(require_auth)])
async def delete_session(session_id: UUID, runtime: RuntimeDep, db: DbDep) -> None:
    async with db.begin():
        session = await _editable_session(db, session_id)
        if session.daytona_sandbox_id:
            try:
                sandbox = await runtime.daytona.get(session.daytona_sandbox_id)
                await runtime.daytona.delete(sandbox, wait=True)
            except DaytonaError as error:
                if error.status_code != 404:
                    raise
        await db.delete(session)


@app.get("/api/sessions/{session_id}/messages", dependencies=[Depends(require_auth)])
async def session_messages(session_id: UUID, db: DbDep, runtime: RuntimeDep) -> list[dict]:
    await _session(db, session_id)
    await db.rollback()
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


@app.post(
    "/api/sessions/{session_id}/files",
    status_code=201,
    dependencies=[Depends(require_auth)],
    max_body_size=100 * 1024 * 1024,
    form_limits={"max_files": 1, "max_fields": 4, "max_part_size": 64 * 1024},
)
async def upload_file(
    session_id: UUID, file: Annotated[UploadFile, Upload()], db: DbDep, runtime: RuntimeDep
) -> FileView:
    name = file.filename or ""
    if not _safe_component(name):
        raise HTTPException(400, "Invalid filename")
    path = f"files/{name}"
    temporary = f"{WORKSPACE}/files/.{uuid4().hex}.upload"
    timeout = runtime.settings.daytona_exec_timeout_seconds
    digest = hashlib.sha256()
    size = 0

    async def chunks() -> AsyncIterator[bytes]:
        nonlocal size
        while chunk := await file.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
            yield chunk

    sandbox = None
    created = False
    try:
        async with db.begin():
            session = await _editable_session(db, session_id)
            sandbox, sandbox_id, created = await ensure_session_sandbox(
                runtime.daytona, runtime.settings, session_id, session.daytona_sandbox_id
            )
            session.daytona_sandbox_id = sandbox_id
            try:
                await sandbox.fs.upload_file_stream(chunks(), temporary, timeout=timeout)
                await sandbox.fs.move_files(
                    temporary, f"{WORKSPACE}/{path}", request_timeout=timeout
                )
            except (Exception, asyncio.CancelledError):
                with suppress(DaytonaError):
                    await sandbox.fs.delete_file(temporary, request_timeout=timeout)
                raise
            record = await db.scalar(
                select(store.File).where(
                    store.File.session_id == session_id, store.File.path == path
                )
            )
            if record is None:
                record = store.File(session_id=session_id, path=path, kind="input")
                db.add(record)
            record.size_bytes = size
            record.sha256 = digest.hexdigest()
    except (Exception, asyncio.CancelledError):
        if created:
            with suppress(DaytonaError):
                await runtime.daytona.delete(sandbox, wait=True)
        raise
    return FileView.model_validate(record)


@app.get("/api/sessions/{session_id}/files", dependencies=[Depends(require_auth)])
async def session_files(session_id: UUID, db: DbDep) -> list[FileView]:
    await _session(db, session_id)
    files = await db.scalars(
        select(store.File).where(store.File.session_id == session_id).order_by(store.File.path)
    )
    return [FileView.model_validate(file) for file in files]


@app.get("/api/sessions/{session_id}/files/{file_path:path}", dependencies=[Depends(require_auth)])
async def download_file(
    session_id: UUID, file_path: str, db: DbDep, runtime: RuntimeDep
) -> Response:
    if not _valid_file_path(file_path):
        raise HTTPException(400, "Invalid workspace path")
    session = await _session(db, session_id)
    sandbox_id = session.daytona_sandbox_id
    file_id = await db.scalar(
        select(store.File.id).where(
            store.File.session_id == session_id, store.File.path == file_path
        )
    )
    if file_id is None or sandbox_id is None:
        raise HTTPException(404, "File not found")
    await db.rollback()
    try:
        sandbox, _, _ = await ensure_session_sandbox(
            runtime.daytona, runtime.settings, session_id, sandbox_id
        )
        content = await sandbox.fs.download_file(
            f"{WORKSPACE}/{file_path}", runtime.settings.daytona_exec_timeout_seconds
        )
    except DaytonaError as error:
        if error.status_code == 404:
            raise HTTPException(404, "File not found") from error
        raise
    filename = quote(file_path.rsplit("/", 1)[-1], safe="")
    return Response(
        content,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


@app.get("/api/memories", dependencies=[Depends(require_auth)])
async def list_memories(
    db: DbDep, session_id: UUID | None = None, approved: bool | None = None
) -> list[MemoryView]:
    query = select(store.Memory).order_by(store.Memory.id.desc())
    if session_id is not None:
        query = query.where(store.Memory.session_id == session_id)
    if approved is not None:
        query = query.where(store.Memory.approved.is_(approved))
    return [MemoryView.model_validate(memory) for memory in await db.scalars(query)]


@app.patch("/api/memories/{memory_id}", dependencies=[Depends(require_auth)])
async def edit_memory(memory_id: UUID, payload: MemoryEdit, db: DbDep) -> MemoryView:
    changes = payload.model_dump(exclude_none=True)
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
async def list_skills(runtime: RuntimeDep) -> list[dict[str, str]]:
    return [
        {"name": skill.name, "description": skill.description} for skill in runtime.skills.skills
    ]


@dataclass
class Turn:
    run_id: UUID
    session_id: UUID
    prompt: str
    pinned_skills: list[str]
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
    pinned = payload.pinned_skills or [
        skill.name
        for skill in runtime.skills.skills
        if re.search(rf"(?<![\w@])@{re.escape(skill.name)}(?![\w-])", prompt)
    ]
    if set(name.removeprefix("@") for name in pinned) - {
        skill.name for skill in runtime.skills.skills
    }:
        raise HTTPException(422, "Unknown pinned Skill")

    sessionmaker = runtime.sessionmaker
    await store.get_session_or_raise(sessionmaker, session_id)
    try:
        run = await store.acquire_root_run(sessionmaker, session_id, prompt)
    except store.TurnConflictError as error:
        raise HTTPException(409, "Session already has a running turn") from error
    except IntegrityError:
        # Resolve a deletion that raced the existence check.
        await store.get_session_or_raise(sessionmaker, session_id)
        raise

    turn = Turn(run.id, session_id, prompt, pinned)
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


async def _chat_sandbox(runtime: Runtime, session: store.Session) -> AsyncSandbox:
    sandbox, sandbox_id, created = await ensure_session_sandbox(
        runtime.daytona, runtime.settings, session.id, session.daytona_sandbox_id
    )
    if created:
        try:
            async with runtime.sessionmaker() as db, db.begin():
                await db.execute(
                    update(store.Session)
                    .where(store.Session.id == session.id)
                    .values(daytona_sandbox_id=sandbox_id)
                )
        except (Exception, asyncio.CancelledError):
            with suppress(DaytonaError):
                await runtime.daytona.delete(sandbox, wait=True)
            raise
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
        await store.save_or_update_memory(
            runtime.sessionmaker,
            session_id=turn.session_id if candidate.scope == "session" else None,
            content=candidate.content,
            approved=candidate.approved,
            memory_id=UUID(candidate.supersedes_id) if candidate.supersedes_id else None,
        )

    try:
        with dspy.context(lm=runtime.decision_lm):
            await agent.consolidate_memory(
                runtime.settings, turn.prompt, answer, memories, on_save=save
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
        # Read history after acquiring the root so it includes the previous completed turn.
        async with sessionmaker() as db:
            session = await _session(db, turn.session_id)
        sandbox = await _chat_sandbox(runtime, session)
        files = await store.list_session_files(sessionmaker, turn.session_id)
        records = await store.search_memories(sessionmaker, turn.session_id, turn.prompt)
        memories = [
            {
                "id": str(record.id),
                "content": record.content,
                "scope": "global" if record.session_id is None else "session",
            }
            for record in records
        ]
        manifest = [_file_manifest(file) for file in files]

        async def refresh_files() -> list[dict]:
            for artifact in await list_new_artifacts(sandbox, {file.path for file in files}):
                path = artifact["path"].removeprefix(f"{WORKSPACE}/")
                if _valid_file_path(path) and path.startswith("artifacts/"):
                    artifacts.append({**artifact, "path": path, "kind": "artifact"})
            return sorted([*manifest, *artifacts], key=lambda file: file["path"])

        with dspy.context(lm=runtime.decision_lm):
            selection = await asyncio.to_thread(
                select_skills, runtime.settings, runtime.skills, turn.prompt, turn.pinned_skills
            )

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
            runtime.settings,
            task=turn.prompt,
            files_manifest=manifest,
            memories=[memory["content"] for memory in memories],
            interpreter_factory=make_interpreter_factory(
                sandbox, runtime.settings, asyncio.get_running_loop()
            ),
            run_id=root_id,
            emit=emit,
            history=dspy.History.model_validate(session.history_json),
            selection=selection,
            model_override=session.model_override or runtime.lm,
            sub_lm=runtime.program.sub_lm,
            callbacks=agent.ChildCallbacks(start_child, finish_child),
        )
        if await request.is_disconnected():
            raise asyncio.CancelledError
        status = "completed"
    except asyncio.CancelledError:
        status = "cancelled"
        raise
    except Exception as error:
        if asyncio.current_task().cancelling():
            status = "cancelled"
            raise asyncio.CancelledError from error
        error_text = "Turn failed"
        _LOG.warning("Turn %s failed (%s)", turn.run_id, type(error).__name__)
    finally:
        turn.finalizing = True
        try:
            persisted = {}
            if status == "completed":
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

    if status == "completed" and committed:
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
