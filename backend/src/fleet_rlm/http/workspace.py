"""Sessions, transcripts, files, memories, and skills."""

import asyncio
import logging
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from typing import Annotated, Any
from urllib.parse import quote, urlencode
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, Response, UploadFile
from fastapi import File as Upload
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import case, func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.types import Receive, Scope, Send

from .. import agent, chatgpt_plan
from .. import db as store
from ..config import Settings
from ..interpreter import (
    AsyncSandbox,
    DaytonaError,
    SandboxStartupError,
    WorkspaceBrowseLimit,
    allocate_session_sandbox,
    delete_pending_sandbox,
    delete_sandbox_by_id,
    get_running_sandbox,
    list_workspace_files,
    prepare_sandbox,
    read_browsable_workspace_file,
    reusable_session_sandbox,
)
from ..skills import MAX_SKILL_BYTES
from ..storage import ObjectStorage, StorageError, StorageIntegrityError, StorageObjectMissing
from .dependencies import DbDep, Runtime, RuntimeDep, _skill_registry
from .providers import _local_chatgpt
from .settings import (
    ChatModel,
    ReasoningEffort,
    _applied_effort,
    _chat_defaults,
    _effective_settings,
    _primary_chat_model,
    _workspace_sub_lm,
)

router = APIRouter()
_LOG = logging.getLogger(__name__)


_WORKSPACE_STARTUP_HELP = (
    "Fleet could not start the workspace. Try again. If the problem persists, "
    "ask your administrator to check the Daytona image and region."
)


class SessionCreate(BaseModel):
    title: str = Field(default="New session", min_length=1)


class ReasoningOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str
    effort: ReasoningEffort


class SessionEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    model_override: str | None = None
    reasoning_override: ReasoningOverride | None = None


class SessionView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    model_override: str | None = None
    reasoning_override: ReasoningOverride | None = None
    daytona_sandbox_id: str | None = None
    deleting: bool = False
    created_at: datetime
    updated_at: datetime


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


@router.post("/sessions", status_code=201)
async def create_session(db: DbDep, payload: SessionCreate | None = None) -> SessionView:
    async with db.begin():
        session = store.Session(title=(payload or SessionCreate()).title)
        db.add(session)
    return SessionView.model_validate(session)


@router.get("/sessions")
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


@router.get("/sessions/{session_id}")
async def get_session(session_id: UUID, db: DbDep) -> SessionView:
    session = await db.get(store.Session, session_id)
    if session is None:
        raise store.SessionNotFoundError(session_id)
    return SessionView.model_validate(session)


@router.get("/sessions/{session_id}/chat-model")
async def get_chat_model(
    session_id: UUID, request: Request, response: Response, runtime: RuntimeDep
) -> ChatModel:
    session = await store.get_session_or_raise(runtime.sessionmaker, session_id)
    settings = await _effective_settings(runtime)
    _, defaults = await _chat_defaults(request, runtime)
    model = await _primary_chat_model(
        runtime, settings, session.model_override, defaults.get("primary")
    )
    response.headers["Cache-Control"] = "private, no-store"
    return model.model_copy(update={"effort": _applied_effort(model, session.reasoning_override)})


async def _session_edit_changes(
    payload: SessionEdit, request: Request, runtime: Runtime
) -> dict[str, Any]:
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
        if chatgpt_plan.is_plan_model(override):
            service = _local_chatgpt(request, runtime)
            if service is None:
                raise HTTPException(403, "ChatGPT plan models are available on local Fleet only")
            await asyncio.to_thread(service.validate_model, override)
        changes["model_override"] = override or None
    return changes


@router.patch("/sessions/{session_id}")
async def edit_session(
    session_id: UUID, payload: SessionEdit, db: DbDep, request: Request, runtime: RuntimeDep
) -> SessionView:
    changes = await _session_edit_changes(payload, request, runtime)
    conditions = [store.Session.id == session_id, store.Session.deleting.is_(False)]
    if "reasoning_override" in payload.model_fields_set:
        reasoning = payload.reasoning_override
        if reasoning is not None:
            session = await store.get_session_or_raise(runtime.sessionmaker, session_id)
            settings = await _effective_settings(runtime)
            _, defaults = await _chat_defaults(request, runtime)
            model = await _primary_chat_model(
                runtime,
                settings,
                changes.get("model_override", session.model_override),
                defaults.get("primary"),
            )
            if reasoning.model != model.id:
                raise HTTPException(409, "The active model changed. Select thinking effort again.")
            if reasoning.effort not in model.efforts:
                raise HTTPException(422, "This model does not support the selected thinking effort")
            conditions.append(
                store.Session.model_override.is_not_distinct_from(session.model_override)
            )
        changes["reasoning_override"] = reasoning.model_dump() if reasoning is not None else None
    elif "model_override" in changes:
        changes["reasoning_override"] = case(
            (store.Session.model_override.is_distinct_from(changes["model_override"]), None),
            else_=store.Session.reasoning_override,
        )
    async with db.begin():
        try:
            await store.lock_model_providers(db, (changes.get("model_override"),))
        except store.OptimizationConflictError as error:
            raise HTTPException(409, str(error)) from error
        session = await db.scalar(
            update(store.Session)
            .where(*conditions)
            .values(**changes, updated_at=func.now())
            .returning(store.Session)
        )
        if session is None:
            if len(conditions) > 2 and await db.get(store.Session, session_id) is not None:
                raise HTTPException(409, "The active model changed. Select thinking effort again.")
            raise store.SessionNotFoundError(session_id)
    return SessionView.model_validate(session)


@router.delete("/sessions/{session_id}", status_code=204)
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


@router.get("/sessions/{session_id}/messages")
async def session_messages(
    session_id: UUID,
    request: Request,
    response: Response,
    runtime: RuntimeDep,
    limit: Annotated[int | None, Query(ge=1, le=50)] = None,
    before: UUID | None = None,
) -> list[dict]:
    await store.get_session_or_raise(runtime.sessionmaker, session_id)
    if limit is None and before is None:
        return await store.get_session_ui_messages(runtime.sessionmaker, session_id)
    page_size = limit if limit is not None else 25
    messages, next_before = await store.get_session_ui_message_page(
        runtime.sessionmaker, session_id, limit=page_size, before=before
    )
    if next_before is not None:
        path = request.url_for("session_messages", session_id=session_id).path
        params = urlencode({"limit": page_size, "before": str(next_before)})
        response.headers["Link"] = f'<{path}?{params}>; rel="prev"'
    return messages


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


@router.post(
    "/sessions/{session_id}/files",
    status_code=201,
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


@router.get("/sessions/{session_id}/files")
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


@router.get("/sessions/{session_id}/workspace")
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


@router.get("/sessions/{session_id}/workspace/{file_path:path}")
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
        file = await db.scalar(
            select(store.File)
            .join(store.Session)
            .where(
                store.File.session_id == session_id,
                store.File.path == file_path,
                store.Session.deleting.is_(False),
            )
        )
        if file is None:
            await _session(db, session_id)
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


@router.get("/sessions/{session_id}/files/{file_path:path}")
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


@router.get("/memories")
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


@router.patch("/memories/{memory_id}")
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


@router.delete("/memories/{memory_id}", status_code=204)
async def delete_memory(memory_id: UUID, db: DbDep) -> None:
    async with db.begin():
        memory = await db.get(store.Memory, memory_id)
        if memory is None:
            raise HTTPException(404, "Memory not found")
        await db.delete(memory)


@router.post("/sessions/{session_id}/runs/{run_id}/refine")
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
                run.prompt, skills, observations, lm=await _workspace_sub_lm(runtime, effective)
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


@router.get("/skills")
async def list_skills(runtime: RuntimeDep) -> list[SkillView]:
    registry = await _skill_registry(runtime)
    return [
        SkillView.model_validate(skill) for skill in registry.available_skills(runtime.settings)
    ]


@router.get("/skills/{name}")
async def get_skill(name: str, runtime: RuntimeDep) -> SkillDetail:
    registry = await _skill_registry(runtime)
    skill = registry.pin_names().get(name)
    if skill and not skill.unavailable_reason(runtime.settings):
        return SkillDetail.model_validate(skill)
    raise HTTPException(404, "Skill not found")


@router.post("/sessions/{session_id}/skills", status_code=201)
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
