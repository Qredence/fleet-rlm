"""Offline Phase 3 checks against FastAPI's real ASGI routes and SSE transport."""

import asyncio
import base64
import hashlib
import json
import subprocess
import sys
import threading
from contextlib import asynccontextmanager, contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import dspy
import httpx
import mlflow
import pytest
import pytest_asyncio
from botocore.response import StreamingBody
from dspy.utils.dummies import DummyLM
from pydantic import SecretStr, ValidationError
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError

from fleet_rlm import agent, api, db, interpreter, skills, ui_stream
from fleet_rlm.config import Settings
from fleet_rlm.http import chat as chat_routes
from fleet_rlm.http import settings as settings_routes
from fleet_rlm.http import workspace as workspace_routes
from fleet_rlm.interpreter import DaytonaError, NotFoundError
from fleet_rlm.skills import SkillDefinition, SkillRegistry
from fleet_rlm.storage import (
    ObjectStorage,
    StorageError,
    StorageIntegrityError,
    StorageObjectMissing,
)

_AUTH = {"Authorization": "Bearer offline-token"}
_PAYLOAD = {
    "messages": [
        {"role": "user", "parts": [{"type": "text", "text": "Previous question"}]},
        {"role": "assistant", "parts": [{"type": "text", "text": "Previous answer"}]},
        {
            "role": "user",
            "parts": [{"type": "file"}, {"type": "text", "text": "Analyze @table"}],
        },
    ],
}
_MIB = 1024 * 1024
_read_all_messages = db.get_session_ui_messages


class OfflineStore:
    def __init__(self):
        now = datetime.now(UTC)
        self.session_id = uuid4()
        self.session = db.Session(
            id=self.session_id,
            title="Test session",
            history_json={"messages": []},
            daytona_sandbox_id="test-sandbox",
            deleting=False,
            model_override=None,
            created_at=now,
            updated_at=now,
        )
        self.sessions: dict[UUID, db.Session] = {self.session_id: self.session}
        self.active_by_session: dict[UUID, UUID] = {}
        self.checkouts = 0
        self.files: dict[tuple[UUID, str], db.File] = {}
        self.cleanup: dict[str, db.ObjectCleanup] = {}
        self.memories: dict[UUID, db.Memory] = {}
        self.ui_messages_by_session: dict[UUID, list[dict]] = {}
        self.statements = []
        self.workspace_overrides = {}

    @property
    def active(self) -> UUID | None:
        return self.active_by_session.get(self.session_id)

    @active.setter
    def active(self, value: UUID | None) -> None:
        if value is None:
            self.active_by_session.pop(self.session_id, None)
        else:
            self.active_by_session[self.session_id] = value

    @asynccontextmanager
    async def __call__(self):
        self.checkouts += 1
        try:
            yield self
        finally:
            self.checkouts -= 1

    @asynccontextmanager
    async def begin(self):
        yield

    async def get(self, model, identity, **_options):
        if model is db.Session:
            return self.sessions.get(identity)
        if model is db.Memory:
            return self.memories.get(identity)
        return None

    async def delete(self, record):
        if isinstance(record, db.Session):
            self.sessions.pop(record.id, None)
            self.active_by_session.pop(record.id, None)
            for key in [key for key in self.files if key[0] == record.id]:
                self.files.pop(key, None)
            for mid in [mid for mid, mem in self.memories.items() if mem.session_id == record.id]:
                self.memories.pop(mid, None)
            for key in [key for key, item in self.cleanup.items() if item.session_id == record.id]:
                self.cleanup.pop(key)
        elif isinstance(record, db.Memory):
            self.memories.pop(record.id, None)

    async def execute(self, query):
        assert self.checkouts > 0
        self.statements.append(query)

    async def rollback(self):
        pass

    async def scalars(self, query):
        compiled = query.compile()
        sql = str(compiled)
        params = compiled.params
        limit = query._limit_clause.value if query._limit_clause is not None else None
        entity = query.column_descriptions[0]["entity"]
        if entity is db.Session:
            rows = sorted(
                self.sessions.values(),
                key=lambda item: (item.updated_at, item.id),
                reverse=True,
            )
            return rows[:limit] if limit is not None else rows
        if entity is db.File:
            session_id = params.get("session_id_1", self.session_id)
            rows = sorted(
                (item for (sid, _), item in self.files.items() if sid == session_id),
                key=lambda item: item.path,
            )
            return rows[:limit] if limit is not None else rows
        if entity is db.Memory:
            rows = list(self.memories.values())
            if "id_1" in params:
                rows = [item for item in rows if item.id == params["id_1"]]
            if "session_id_1" in params:
                rows = [item for item in rows if item.session_id == params["session_id_1"]]
            if "memories.approved IS true" in sql:
                rows = [item for item in rows if item.approved is True]
            elif "memories.approved IS false" in sql:
                rows = [item for item in rows if item.approved is False]
            rows.sort(key=lambda item: item.id, reverse=True)
            return rows[:limit] if limit is not None else rows
        return []

    async def scalar(self, query):
        if getattr(query, "is_update", False):
            params = query.compile().params
            if query.table.name == "sessions":
                session = self.sessions.get(params.get("id_1"))
                if session is None or session.deleting:
                    return None
                if "title" in params:
                    session.title = params["title"]
                if "model_override" in params:
                    if session.model_override != params["model_override"]:
                        session.reasoning_override = None
                    session.model_override = params["model_override"]
                if "reasoning_override" in params:
                    session.reasoning_override = params["reasoning_override"]
                session.updated_at = datetime.now(UTC)
                return session
            if query.table.name == "memories":
                memory = self.memories.get(params.get("id_1"))
                if memory is None:
                    return None
                if "content" in params:
                    memory.content = params["content"]
                if "approved" in params:
                    memory.approved = params["approved"]
                return memory
            return None
        entity = query.column_descriptions[0]["entity"]
        params = query.compile().params
        if entity is db.Run:
            return self.active_by_session.get(params.get("session_id_1", self.session_id))
        session_id = params.get("session_id_1", self.session_id)
        file = self.files.get((session_id, params["path_1"]))
        if file and query.column_descriptions[0]["name"] == "id":
            return file.id
        return file

    def add(self, record):
        now = datetime.now(UTC)
        if record.id is None:
            record.id = uuid4()
        if record.created_at is None:
            record.created_at = now
        if isinstance(record, db.Session):
            if record.deleting is None:
                record.deleting = False
            if record.history_json is None:
                record.history_json = {"messages": []}
            if record.updated_at is None:
                record.updated_at = now
            self.sessions[record.id] = record
        elif isinstance(record, db.File):
            if record.session_id is None:
                record.session_id = self.session_id
            if record.sha256 is None:
                record.sha256 = "0" * 64
            if record.size_bytes is None:
                record.size_bytes = 0
            if record.kind is None:
                record.kind = "artifact"
            self.files[(record.session_id, record.path)] = record
        elif isinstance(record, db.Memory):
            if record.approved is None:
                record.approved = False
            self.memories[record.id] = record

    async def get_session(self, _sessionmaker, session_id):
        async with self() as connection:
            session = await connection.get(db.Session, session_id)
            if session is None or session.deleting:
                raise db.SessionNotFoundError(session_id)
            return session

    async def bind_sandbox(
        self,
        _sessionmaker,
        session_id,
        sandbox_id,
        *,
        expected=None,
        owner_run_id=None,
        allocation_name=None,
    ):
        async with self():
            session = self.sessions.get(session_id)
            if session is None:
                raise db.SessionNotFoundError(session_id)
            if owner_run_id is not None and self.active_by_session.get(session_id) != owner_run_id:
                raise db.TurnConflictError
            if session.daytona_sandbox_id == expected:
                if allocation_name is not None:
                    if session.pending_sandbox_name != allocation_name:
                        return session.daytona_sandbox_id, False
                    session.pending_sandbox_name = None
                session.daytona_sandbox_id = sandbox_id
                session.sandbox_needs_cleanup = False
                session.updated_at = datetime.now(UTC)
                return sandbox_id, True
            return session.daytona_sandbox_id, False

    async def reserve_sandbox(self, _sessionmaker, session_id, run_id, *, expected):
        async with self():
            session = self.sessions[session_id]
            assert session.daytona_sandbox_id == expected
            assert self.active_by_session[session_id] == run_id
            if session.pending_sandbox_name is not None:
                return session.pending_sandbox_name, True
            session.pending_sandbox_name = f"fleet-{run_id.hex}"
            return session.pending_sandbox_name, False

    async def acquire_root(self, _sessionmaker, session_id, _prompt):
        async with self():
            if session_id not in self.sessions:
                raise db.SessionNotFoundError(session_id)
            if session_id in self.active_by_session or db.upload_is_active(
                self.sessions[session_id]
            ):
                raise db.TurnConflictError
            run_id = uuid4()
            self.active_by_session[session_id] = run_id
            return SimpleNamespace(id=run_id)

    async def finish(self, _sessionmaker, run_id, *, status, **details):
        async with self():
            for session_id, active_id in list(self.active_by_session.items()):
                if active_id == run_id:
                    self.active_by_session.pop(session_id, None)
                    session = self.sessions[session_id]
                    if (
                        details.get("retire_sandbox_id") == session.daytona_sandbox_id
                        and session.daytona_sandbox_id is not None
                    ):
                        session.sandbox_needs_cleanup = True
                    if status == "completed":
                        self.sessions[session_id].history_json = details["history_json"]
                        paths = [path for owner, path in self.files if owner == session_id]
                        for path in db.replaced_file_paths(
                            paths, (file["path"] for file in details["files"])
                        ):
                            self.discard_file(self.files.pop((session_id, path)))
                        for artifact in details["files"]:
                            self.upsert_file_values(session_id, **artifact)
                    break
            return True

    async def upsert_file(self, _sessionmaker, session_id, *, expected_upload=None, **values):
        async with self():
            session = self.sessions.get(session_id)
            if session is None or session.deleting or session.upload_key != expected_upload:
                raise db.SessionNotFoundError(session_id)
            session.upload_key = session.upload_started_at = None
            return self.upsert_file_values(session_id, **values)

    def upsert_file_values(self, session_id, *, path, **values):
        existing = self.files.get((session_id, path))
        if existing is None:
            existing = db.File(
                id=uuid4(),
                session_id=session_id,
                path=path,
                created_at=datetime.now(UTC),
            )
            self.files[(session_id, path)] = existing
        elif existing.object_key != values.get("object_key"):
            self.discard_file(existing)
        for name, value in values.items():
            setattr(existing, name, value)
        self.cleanup.pop(existing.object_key, None)
        return existing

    def discard_file(self, file):
        self.cleanup[file.object_key] = db.ObjectCleanup(
            session_id=file.session_id, object_key=file.object_key
        )

    async def register_object(self, _sessionmaker, session_id, key, upload_id, *, run_id=None):
        session = self.sessions.get(session_id)
        if session is None or session.deleting or (run_id is None and session.upload_key != key):
            raise db.SessionNotFoundError(session_id)
        if run_id is not None and self.active_by_session.get(session_id) != run_id:
            raise db.TurnConflictError
        self.cleanup[key] = db.ObjectCleanup(
            session_id=session_id, object_key=key, upload_id=upload_id, run_id=run_id
        )

    async def discarded_objects(self, _sessionmaker, session_id):
        session = self.sessions.get(session_id)
        active = self.active_by_session.get(session_id)
        return [
            item
            for item in self.cleanup.values()
            if item.session_id == session_id
            and session
            and (
                item.run_id != active
                if item.run_id is not None
                else item.object_key != session.upload_key
            )
        ]

    async def forget_objects(self, _sessionmaker, keys):
        for key in keys:
            self.cleanup.pop(key, None)

    async def release_upload(self, _sessionmaker, session_id, key):
        if (session := self.sessions.get(session_id)) and session.upload_key == key:
            session.upload_key = session.upload_started_at = None

    async def list_files(self, _sessionmaker, session_id):
        async with self():
            return sorted(
                (item for (sid, _), item in self.files.items() if sid == session_id),
                key=lambda item: item.path,
            )

    async def ui_messages(self, _sessionmaker, session_id):
        async with self():
            return list(self.ui_messages_by_session.get(session_id, []))

    async def get_workspace_overrides(self, _sessionmaker):
        async with self():
            return dict(self.workspace_overrides)

    async def update_workspace_overrides(self, _sessionmaker, changes):
        async with self():
            for key, value in changes.items():
                if value is None:
                    self.workspace_overrides.pop(key, None)
                else:
                    self.workspace_overrides[key] = value
            return dict(self.workspace_overrides)

    async def reset_workspace_overrides(self, _sessionmaker):
        async with self():
            self.workspace_overrides.clear()
            return {}


class OfflineStorage:
    prefix = staticmethod(ObjectStorage.prefix)
    new_key = ObjectStorage.new_key

    def __init__(self, _settings):
        self.objects = {}
        self.checkouts = lambda: 0

    def close(self):
        pass

    def seed(self, store, path, content, *, kind="artifact"):
        key = self.new_key(store.session_id)
        self.objects[key] = content
        return store.upsert_file_values(
            store.session_id,
            path=path,
            kind=kind,
            object_key=key,
            sha256=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
        )

    async def put(self, key, path, *, register):
        assert self.checkouts() == 0
        await register("fixture-upload")
        body = path.read_bytes()
        self.objects[key] = body
        return hashlib.sha256(body).hexdigest(), len(body)

    async def download(self, key, path, *, sha256, size):
        assert self.checkouts() == 0
        if key not in self.objects:
            raise StorageObjectMissing("Fixture object missing")
        body = self.objects[key]
        if len(body) != size or hashlib.sha256(body).hexdigest() != sha256:
            raise StorageIntegrityError("Fixture object changed")
        path.write_bytes(body)

    async def reclaim(self, key, _upload_id):
        assert self.checkouts() == 0
        self.objects.pop(key, None)

    async def delete_session(self, session_id):
        assert self.checkouts() == 0
        prefix = self.prefix(session_id)
        for key in list(self.objects):
            if key.startswith(prefix):
                del self.objects[key]


@pytest_asyncio.fixture
async def tracking_endpoint(request):
    mode = getattr(request, "param", None)
    if mode is None:
        yield None
        return
    accepted = asyncio.Event()
    release = asyncio.Event()

    async def stall(reader, writer):
        try:
            await reader.readuntil(b"\r\n\r\n")
            accepted.set()
            await release.wait()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(stall, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    if mode == "refused":
        server.close()
        await server.wait_closed()
    try:
        yield SimpleNamespace(uri=f"http://127.0.0.1:{port}", mode=mode, accepted=accepted)
    finally:
        release.set()
        server.close()
        await server.wait_closed()


@pytest_asyncio.fixture
async def offline(monkeypatch, tmp_path, tracking_endpoint):
    skill = tmp_path / "table"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: table\ndescription: Analyze tabular data\n---\nInspect the data first.\n"
    )
    settings = Settings(
        _env_file=None,
        TAVILY_API_KEY=None,
        database_url="postgresql://localhost/unused",
        database_direct_url="postgresql://localhost/unused",
        daytona_api_key="offline",
        daytona_image="offline",
        llm_model="openai/gpt-4o-mini",
        sub_llm_model="openai/gpt-4o-mini",
        llm_api_key="offline",
        mlflow_tracking_uri=tracking_endpoint.uri if tracking_endpoint else None,
        api_bearer_token="offline-token",
        cors_origins=["https://client.example"],
        skills_dir=tmp_path,
    )
    monkeypatch.setattr(db, "list_llm_providers", AsyncMock(return_value=[]))
    store = OfflineStore()
    engine = SimpleNamespace(dispose=AsyncMock())
    reap = AsyncMock(return_value=0)
    finish = AsyncMock(side_effect=store.finish)
    create_child = AsyncMock(return_value=SimpleNamespace(id=uuid4()))
    remote_files = {"artifacts/report.txt": b"report"}
    artifact = {
        "path": "/workspace/artifacts/report.txt",
        "size_bytes": 6,
        "sha256": hashlib.sha256(b"report").hexdigest(),
    }
    upsert_artifact = AsyncMock(side_effect=store.upsert_file)
    list_files = AsyncMock(side_effect=store.list_files)
    bind_sandbox = AsyncMock(side_effect=store.bind_sandbox)
    ui_messages = AsyncMock(side_effect=store.ui_messages)

    async def pull_artifact(_sandbox, path, destination, *, timeout):
        assert store.checkouts == 0
        destination.write_bytes(remote_files[path])

    async def delete_sandbox(_sandbox, **_options):
        assert store.checkouts == 0

    sandbox = SimpleNamespace(id="test-sandbox", state="started")
    daytona = SimpleNamespace(
        v2=SimpleNamespace(
            get=AsyncMock(return_value=sandbox),
            delete=AsyncMock(side_effect=delete_sandbox),
        )
    )

    @asynccontextmanager
    async def daytona_client(_settings):
        yield daytona

    model = DummyLM([{"plan": "Analyze the data", "code": "SUBMIT('Verified', [])"}])
    monkeypatch.setattr(api, "Settings", lambda: settings)
    monkeypatch.setattr(api, "make_daytona_client", daytona_client)
    monkeypatch.setattr(api, "ObjectStorage", OfflineStorage)
    monkeypatch.setattr(chat_routes, "reset_managed_workspace", AsyncMock())
    monkeypatch.setattr(chat_routes, "reconcile_managed_workspace", AsyncMock(return_value=set()))
    monkeypatch.setattr(chat_routes, "upload_workspace_file", AsyncMock())
    monkeypatch.setattr(agent, "_model", lambda *_args: model)
    monkeypatch.setattr(db, "create_db", lambda _settings: (engine, store))
    monkeypatch.setattr(db, "reap_stale_runs", reap)
    monkeypatch.setattr(db, "get_session_or_raise", store.get_session)
    monkeypatch.setattr(db, "rebind_session_sandbox", bind_sandbox)
    monkeypatch.setattr(db, "reserve_session_sandbox", store.reserve_sandbox)
    monkeypatch.setattr(db, "register_object_write", store.register_object)
    monkeypatch.setattr(db, "list_discarded_objects", store.discarded_objects)
    monkeypatch.setattr(db, "forget_discarded_objects", store.forget_objects)
    monkeypatch.setattr(db, "acquire_root_run", store.acquire_root)
    monkeypatch.setattr(db, "create_child_run", create_child)
    monkeypatch.setattr(db, "finish_run", finish)
    monkeypatch.setattr(db, "list_session_files", list_files)
    monkeypatch.setattr(db, "get_session_ui_messages", ui_messages)
    monkeypatch.setattr(db, "get_session_prior_turns", AsyncMock(return_value=[]))
    monkeypatch.setattr(db, "search_memories", AsyncMock(return_value=[]))
    monkeypatch.setattr(db, "recall_procedures", AsyncMock(return_value=[]))
    monkeypatch.setattr(db, "upsert_file_record", upsert_artifact)
    monkeypatch.setattr(db, "release_upload", store.release_upload)
    pull = AsyncMock(side_effect=pull_artifact)
    monkeypatch.setattr(chat_routes, "download_workspace_file", pull)
    monkeypatch.setattr(db, "get_workspace_settings_overrides", store.get_workspace_overrides)
    monkeypatch.setattr(db, "update_workspace_settings_overrides", store.update_workspace_overrides)
    monkeypatch.setattr(db, "reset_workspace_settings_overrides", store.reset_workspace_overrides)
    monkeypatch.setattr(chat_routes, "list_new_artifacts", AsyncMock(return_value=[artifact]))
    memory = AsyncMock(side_effect=RuntimeError("Optional memory gate failed"))
    monkeypatch.setattr(agent, "consolidate_memory", memory)

    async with api.lifespan(api.app):
        storage = api.app.state.runtime.storage
        storage.checkouts = lambda: store.checkouts
        storage.put = AsyncMock(wraps=storage.put)
        storage.download = AsyncMock(wraps=storage.download)
        storage.reclaim = AsyncMock(wraps=storage.reclaim)
        reap.assert_awaited_once_with(store, settings.stale_run_timeout_seconds)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api.app),
            base_url="https://test",
            headers=_AUTH,
        ) as client:
            yield SimpleNamespace(
                client=client,
                store=store,
                sandbox=sandbox,
                daytona=daytona,
                settings=settings,
                base=f"/api/sessions/{store.session_id}",
                reap=reap,
                finish=finish,
                create_child=create_child,
                storage=storage,
                remote_files=remote_files,
                pull=pull,
                list_files=list_files,
                bind_sandbox=bind_sandbox,
                ui_messages=ui_messages,
                memory=memory,
            )
    engine.dispose.assert_awaited_once()


@pytest.mark.asyncio
async def test_auth_and_cors(offline):
    for authorization in ("", "Bearer", "Basic offline-token", "Bearer wrong"):
        response = await offline.client.get("/api/skills", headers={"Authorization": authorization})
        assert response.status_code == 401
        assert response.headers["WWW-Authenticate"] == "Bearer"
    response = await offline.client.get("/api/skills", headers={"Origin": "https://client.example"})
    assert response.status_code == 200
    assert response.json() == [{"name": "table", "description": "Analyze tabular data"}]
    assert response.headers["access-control-allow-origin"] == "https://client.example"
    assert set(response.headers["access-control-expose-headers"].lower().split(", ")) == {
        "x-vercel-ai-ui-message-stream",
        "link",
        "retry-after",
    }

    for path in (offline.base, "/api/skills/table"):
        assert (
            await offline.client.get(path, headers={"Authorization": "Bearer wrong"})
        ).status_code == 401
    preview = await offline.client.get("/api/skills/table")
    assert preview.json() == {
        "name": "table",
        "description": "Analyze tabular data",
        "body": "Inspect the data first.",
    }
    assert (await offline.client.get("/api/skills/missing")).status_code == 404

    offline.settings.api_bearer_token = None
    assert (await offline.client.get("/api/skills")).status_code == 401
    local = "http://127.0.0.1:8000/api/skills"
    assert (
        await offline.client.get(local, headers={"Origin": "http://localhost:5173"})
    ).status_code == 200
    assert (
        await offline.client.get(local, headers={"Origin": "https://client.example"})
    ).status_code == 401
    assert (
        await offline.client.get(local, headers={"Forwarded": "proto=https"})
    ).status_code == 401

    schema = api.app.openapi()
    assert schema["components"]["securitySchemes"]["FleetBearer"]["scheme"] == "bearer"
    assert schema["paths"]["/api/skills"]["get"]["security"] == [{"FleetBearer": []}]
    assert "security" not in schema["paths"]["/healthz"]["get"]


@pytest.mark.asyncio
@pytest.mark.parametrize("admin", [None, "   ", "offline-token"])
async def test_shared_startup_checks_credentials_before_database(offline, monkeypatch, admin):
    values = offline.settings.model_dump()
    values.update(shared_deployment=True, provider_admin_token=admin)
    monkeypatch.setattr(api, "Settings", lambda: Settings(_env_file=None, **values))
    create_db = Mock()
    monkeypatch.setattr(db, "create_db", create_db)
    with pytest.raises(ValidationError, match="Shared deployments require distinct"):
        async with api.lifespan(api.app):
            pytest.fail("The app started with invalid shared credentials")
    create_db.assert_not_called()


@pytest.mark.asyncio
async def test_unauthorized_upload_is_rejected_before_reading_body(offline):
    async def upload_body():
        pytest.fail("The upload body was read before authorization")
        yield b""

    response = await offline.client.post(
        f"{offline.base}/files",
        headers={
            "Authorization": "",
            "Content-Type": "multipart/form-data; boundary=upload",
            "Origin": "https://client.example",
        },
        content=upload_body(),
    )
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.headers["access-control-allow-origin"] == "https://client.example"
    offline.storage.put.assert_not_awaited()


@pytest.mark.asyncio
async def test_models_reads_the_configured_gateway(offline, monkeypatch):
    offline.settings.llm_base_url = "https://gateway.example/openai/v1/"
    offline.store.workspace_overrides = {"llm_model": "openai/default"}
    requests = []

    def gateway(request):
        requests.append(request)
        assert offline.store.checkouts == 0
        assert request.url == httpx.URL("https://gateway.example/openai/v1/models")
        assert request.headers["authorization"] == "Bearer offline"
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "org/z-model"},
                    {"id": "openai/a-model", "architecture": {"output_modalities": ["text"]}},
                    {"id": "org/z-model"},
                    {"id": "org/chat-model", "type": "language"},
                    {"id": "bfl/flux-2-pro", "type": "image"},
                    {"id": "openai/text-embedding-3-small", "type": "embedding"},
                    {"id": "image-only", "architecture": {"output_modalities": ["image"]}},
                    {"id": "invalid id"},
                    {},
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(gateway), follow_redirects=False)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client)
    assert (
        await offline.client.get("/api/models", headers={"Authorization": ""})
    ).status_code == 401
    assert not requests
    response = await offline.client.get("/api/models")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.json() == {
        "workspace_default": "openai/default",
        "catalog_status": "ready",
        "models": [
            {"id": "openai/openai/a-model", "label": "openai/a-model"},
            {"id": "openai/org/chat-model", "label": "org/chat-model"},
            {"id": "openai/org/z-model", "label": "org/z-model"},
        ],
    }
    assert len(requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["unconfigured", "redirect", "bad-shape", "timeout"])
async def test_models_falls_back_to_default(offline, monkeypatch, failure):
    offline.settings.llm_base_url = (
        None if failure == "unconfigured" else "https://gateway.example/v1"
    )
    requests = []

    def gateway(request):
        requests.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("private upstream error", request=request)
        if failure == "redirect":
            return httpx.Response(302, headers={"Location": "https://other.example/models"})
        return httpx.Response(200, json={"private upstream response": True})

    client = httpx.AsyncClient(transport=httpx.MockTransport(gateway), follow_redirects=False)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client)
    response = await offline.client.get("/api/models")
    unconfigured = failure == "unconfigured"
    assert response.status_code == 200
    assert response.json() == {
        "workspace_default": offline.settings.llm_model,
        "catalog_status": "unconfigured" if unconfigured else "unavailable",
        "models": [],
    }
    assert len(requests) == (0 if unconfigured else 1)


@pytest.mark.asyncio
async def test_health_and_readiness(offline):
    for path in ("/healthz", "/readyz"):
        response = await offline.client.get(path, headers={"Authorization": ""})
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
    assert offline.store.checkouts == 0


@pytest.mark.asyncio
async def test_active_root_is_http_409(offline):
    offline.store.active = uuid4()
    response = await offline.client.post(f"{offline.base}/chat", json=_PAYLOAD)
    assert response.status_code == 409
    assert response.headers["content-type"] == "application/json"
    offline.daytona.v2.get.assert_not_awaited()
    offline.finish.assert_not_awaited()


@pytest.mark.asyncio
async def test_body_size_limits(offline):
    prefix = b'{"messages":[],"padding":"'
    suffix = b'"}'
    body = prefix + b"x" * (_MIB - len(prefix) - len(suffix)) + suffix
    json_headers = {"Content-Type": "application/json"}
    response = await offline.client.post(f"{offline.base}/chat", content=body, headers=json_headers)
    assert response.status_code == 422
    response = await offline.client.post(
        f"{offline.base}/chat", content=body + b" ", headers=json_headers
    )
    assert response.status_code == 413

    prefix = b'--limit\r\nContent-Disposition: form-data; name="file"; filename="data.csv"\r\n\r\n'
    suffix = b"\r\n--limit--\r\n"
    payload_size = 100 * _MIB - len(prefix) - len(suffix)
    chunk = b"x" * _MIB

    async def multipart(payload_size):
        yield prefix
        full_chunks, remainder = divmod(payload_size, len(chunk))
        for _ in range(full_chunks):
            yield chunk
        yield chunk[:remainder]
        yield suffix

    headers = {"Content-Type": "multipart/form-data; boundary=limit"}
    response = await offline.client.post(
        f"{offline.base}/files", content=multipart(payload_size), headers=headers
    )
    assert response.status_code == 201
    assert response.json()["size_bytes"] == payload_size
    digest = hashlib.sha256()
    full_chunks, remainder = divmod(payload_size, len(chunk))
    for _ in range(full_chunks):
        digest.update(chunk)
    digest.update(chunk[:remainder])
    assert response.json()["sha256"] == digest.hexdigest()
    assert response.json()["path"] == "files/data.csv"

    response = await offline.client.post(
        f"{offline.base}/files", content=multipart(payload_size + 1), headers=headers
    )
    assert response.status_code == 413
    offline.storage.put.assert_awaited_once()


@pytest.mark.asyncio
async def test_form_limits(offline):
    response = await offline.client.post(
        f"{offline.base}/files", files=[("file", ("a", b"x")), ("file", ("b", b"y"))]
    )
    assert response.status_code == 400
    response = await offline.client.post(
        f"{offline.base}/files",
        files={"file": ("a", b"x"), "note": (None, "y" * (64 * 1024 + 1))},
    )
    assert response.status_code == 400
    offline.daytona.v2.get.assert_not_awaited()
    offline.storage.put.assert_not_awaited()


@pytest.mark.parametrize("name", ["../outside.csv", r"..\outside.csv", "/absolute.csv", "C:one"])
@pytest.mark.asyncio
async def test_upload_rejects_unsafe_names(offline, name):
    response = await offline.client.post(f"{offline.base}/files", files={"file": (name, b"x")})
    assert response.status_code == 400
    offline.daytona.v2.get.assert_not_awaited()
    offline.storage.put.assert_not_awaited()


@pytest.mark.asyncio
async def test_download_requires_a_safe_registered_path(offline):
    path = f"{offline.base}/files"
    response = await offline.client.get(f"{path}/artifacts/%2e%2e/secret")
    assert response.status_code == 400
    response = await offline.client.get(f"{path}/artifacts/report.txt")
    assert response.status_code == 404
    offline.daytona.v2.get.assert_not_awaited()

    file_path = "artifacts/report 1.txt"
    record = offline.storage.seed(offline.store, file_path, b"report")
    response = await offline.client.get(f"{path}/artifacts/report%201.txt")
    assert response.content == b"report"
    assert response.headers["content-disposition"] == "attachment; filename*=UTF-8''report%201.txt"
    assert offline.storage.download.await_args.args[0] == record.object_key
    response = await offline.client.get(
        f"{path}/artifacts/report%201.txt", headers={"Range": "bytes=100-199"}
    )
    assert response.status_code == 416
    assert not offline.storage.download.await_args.args[1].exists()
    offline.daytona.v2.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_session_cleanup_filters_each_provider_page(offline, monkeypatch):
    session = uuid4()
    owned = ObjectStorage.new_key(session)
    foreign = ObjectStorage.new_key(uuid4())
    uploads = Mock()
    uploads.paginate.return_value = [
        {"Uploads": [{"Key": foreign, "UploadId": "sibling"}]},
        {"Uploads": [{"Key": owned, "UploadId": token} for token in ("first", "second")]},
    ]
    objects = Mock()
    objects.paginate.return_value = [
        {"Contents": [{"Key": foreign}]},
        {"Contents": [{"Key": owned}, {"Key": foreign}]},
    ]
    sdk = Mock()
    sdk.get_paginator.side_effect = {
        "list_multipart_uploads": uploads,
        "list_objects_v2": objects,
    }.__getitem__
    sdk.delete_objects.return_value = {}
    monkeypatch.setattr("fleet_rlm.storage.boto3.client", Mock(return_value=sdk))
    settings = offline.settings.model_copy(
        update={
            "s3_endpoint_url": "https://storage.example.test",
            "s3_access_key_id": SecretStr("offline"),
            "s3_secret_access_key": SecretStr("offline"),
        }
    )
    storage = ObjectStorage(settings)
    await storage.delete_session(session)
    for paginator in (uploads, objects):
        paginator.paginate.assert_called_once_with(
            Bucket=storage.bucket, Prefix=storage.prefix(session)
        )
    assert [call.kwargs for call in sdk.abort_multipart_upload.call_args_list] == [
        {"Bucket": storage.bucket, "Key": owned, "UploadId": token} for token in ("first", "second")
    ]
    sdk.delete_objects.assert_called_once_with(
        Bucket=storage.bucket, Delete={"Objects": [{"Key": owned}], "Quiet": True}
    )


@pytest.mark.asyncio
async def test_storage_checks_persisted_bytes_with_botocore_streaming_body(monkeypatch, tmp_path):
    content = b"verified workspace file"
    digest = hashlib.sha256(content).hexdigest()
    source = tmp_path / "source.txt"
    source.write_bytes(content)
    target = tmp_path / "target.txt"
    raw = BytesIO(content)
    body = StreamingBody(raw, len(content))
    sdk = Mock(
        create_multipart_upload=Mock(return_value={"UploadId": "fixture-upload"}),
        upload_part=Mock(return_value={"ETag": "fixture-etag"}),
        get_object=Mock(return_value={"Body": body, "ContentLength": len(content)}),
    )
    monkeypatch.setattr("fleet_rlm.storage.boto3.client", Mock(return_value=sdk))
    storage = ObjectStorage(
        Settings(
            _env_file=None,
            database_url="postgresql://localhost/unused",
            database_direct_url="postgresql://localhost/unused",
            daytona_api_key="offline",
            llm_model="openai/gpt-4o-mini",
            sub_llm_model="openai/gpt-4o-mini",
            llm_api_key="offline",
            s3_endpoint_url="https://storage.example.test",
            s3_access_key_id="fixture-key",
            s3_secret_access_key="fixture-secret",
        )
    )

    async def register(upload_id):
        assert upload_id == "fixture-upload"
        sdk.upload_part.assert_not_called()

    saved = await storage.put("object", source, register=register)
    assert saved == (digest, len(content))
    sdk.complete_multipart_upload.assert_called_once()
    assert raw.closed
    sdk.get_object.assert_called_once_with(Bucket=storage.bucket, Key="object")
    sdk.get_object.return_value["Body"] = StreamingBody(BytesIO(content), len(content))
    await storage.download("object", target, sha256=digest, size=len(content))
    assert target.read_bytes() == content

    changed = BytesIO(b"x" * len(content))
    sdk.get_object.return_value["Body"] = StreamingBody(changed, len(content))
    sdk.upload_part.reset_mock()
    with pytest.raises(StorageIntegrityError, match="hash"):
        await storage.put("object", source, register=register)
    assert changed.closed

    entered = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()

    def slow_initiation(**_kwargs):
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(timeout=5)
        return {"UploadId": "cancelled-upload"}

    sdk.create_multipart_upload.side_effect = slow_initiation
    sdk.upload_part.reset_mock()
    register_cancelled = AsyncMock()
    upload = asyncio.create_task(storage.put("object", source, register=register_cancelled))
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        upload.cancel()
        await asyncio.sleep(0)
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await upload
    register_cancelled.assert_not_awaited()
    sdk.upload_part.assert_not_called()
    sdk.abort_multipart_upload.assert_called_once_with(
        Bucket=storage.bucket, Key="object", UploadId="cancelled-upload"
    )


@pytest.mark.parametrize(
    ("error", "expected", "detail"),
    [
        (StorageObjectMissing("missing"), 404, "File not found"),
        (StorageError("unavailable"), 503, "Workspace storage unavailable"),
    ],
)
@pytest.mark.asyncio
async def test_download_handles_object_storage_errors(offline, error, expected, detail):
    offline.storage.seed(offline.store, "artifacts/report.txt", b"report")
    offline.storage.download.side_effect = error
    response = await offline.client.get(f"{offline.base}/files/artifacts/report.txt")
    assert response.status_code == expected and response.json()["detail"] == detail
    offline.daytona.v2.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_download_reloads_metadata_if_replacement_reclaims_selected_object(offline):
    path = "artifacts/report.txt"
    old = offline.storage.seed(offline.store, path, b"old")

    async def race(key, local, **metadata):
        if key == old.object_key:
            offline.store.files.pop((offline.store.session_id, path))
            offline.storage.seed(offline.store, path, b"new")
            offline.storage.objects.pop(key)
        await OfflineStorage.download(offline.storage, key, local, **metadata)

    offline.storage.download.side_effect = race
    result = await offline.client.get(f"{offline.base}/files/{path}")
    assert result.status_code == 200 and result.content == b"new"
    assert offline.storage.download.await_count == 2


@pytest.mark.asyncio
async def test_workspace_browses_checkout_and_skips_symlinks(offline, monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    checkout = workspace / "repo"
    checkout.mkdir(parents=True)
    (checkout / "README.md").write_text("# Example checkout")
    (workspace / "artifacts").mkdir()
    (workspace / "artifacts" / "report.md").write_text("# Report")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.txt").write_text("outside the workspace")
    (checkout / "link").symlink_to(outside, target_is_directory=True)
    (checkout / "alias.txt").symlink_to(outside / "private.txt")
    monkeypatch.setattr(interpreter, "WORKSPACE", str(workspace))

    async def run(*, argv, timeout, execution_timeout):
        result = subprocess.run(
            [sys.executable, *argv[1:]], capture_output=True, text=True, timeout=timeout
        )
        return SimpleNamespace(exit_code=result.returncode, stdout=result.stdout)

    offline.sandbox.process = SimpleNamespace(run=run)
    route = f"{offline.base}/workspace"
    response = await offline.client.get(route)
    assert response.status_code == 200
    assert [entry["path"] for entry in response.json()] == ["repo/README.md"]
    assert (await offline.client.get(f"{route}/repo/README.md")).content == b"# Example checkout"
    assert (await offline.client.get(f"{route}/repo/link/private.txt")).status_code == 404
    assert (await offline.client.get(f"{route}/repo/alias.txt")).status_code == 404
    assert (await offline.client.get(f"{route}/repo/%2e%2e/outside")).status_code == 400


@pytest.mark.asyncio
async def test_install_skill_artifact_is_immediately_available_to_new_turns(offline, monkeypatch):
    artifact_path = "artifacts/receipt-check/SKILL.md"
    content = b"---\nname: receipt-check\ndescription: Check receipts\n---\nUse code RC-29.\n"
    digest = hashlib.sha256(content).hexdigest()
    artifact = offline.storage.seed(offline.store, artifact_path, content)

    created = await offline.client.post("/api/sessions", json={"title": "Fresh"})
    assert created.status_code == 201
    session_id = created.json()["id"]
    offline.store.sessions[UUID(session_id)].daytona_sandbox_id = "test-sandbox"
    payload = {
        "messages": [{"role": "user", "parts": [{"type": "text", "text": "Verify a receipt"}]}],
        "pinned_skills": ["receipt-check"],
    }
    path = f"/api/sessions/{session_id}/chat"
    assert (await offline.client.post(path, json=payload)).status_code == 422

    endpoint = f"{offline.base}/skills"
    install = {"path": artifact_path, "sha256": digest}
    for _ in range(2):
        installed = await offline.client.post(endpoint, json=install)
        assert installed.status_code == 201
        assert installed.json() == {"name": "receipt-check", "description": "Check receipts"}
    catalog = await offline.client.get("/api/skills")
    assert [item["name"] for item in catalog.json()] == ["receipt-check", "table"]
    preview = await offline.client.get("/api/skills/receipt-check")
    assert preview.json() == {
        "name": "receipt-check",
        "description": "Check receipts",
        "body": "Use code RC-29.",
    }

    task = AsyncMock(
        return_value=dspy.Prediction(answer="RC-29", history=dspy.History(messages=[]))
    )
    monkeypatch.setattr(agent, "run_workspace_task", task)
    response = await offline.client.post(path, json=payload)
    assert response.status_code == 200
    assert response.text.endswith("data: [DONE]\n\n")
    (skill,) = task.await_args.kwargs["selection"].active_skills
    assert skill.body == "Use code RC-29."

    different = content.replace(b"RC-29", b"RC-30")
    digest = hashlib.sha256(different).hexdigest()
    artifact.sha256 = digest
    offline.storage.objects[artifact.object_key] = different
    assert (
        await offline.client.post(endpoint, json={"path": artifact_path, "sha256": digest})
    ).status_code == 409
    assert skill.path.read_bytes() == content


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "kind", "name", "changed", "status"),
    [
        ("files/SKILL.md", "input", "receipt-check", False, 400),
        ("artifacts/test/SKILL.md", "input", "receipt-check", False, 400),
        ("artifacts/test/SKILL.md", "artifact", "../escape", False, 422),
        ("artifacts/test/SKILL.md", "artifact", "table", False, 409),
        ("artifacts/test/SKILL.md", "artifact", "receipt-check", True, 409),
    ],
)
async def test_install_skill_rejects_invalid_or_changed_artifacts(
    offline, path, kind, name, changed, status
):
    content = f"---\nname: {name}\ndescription: Test\n---\nDo the thing.\n".encode()
    digest = hashlib.sha256(content).hexdigest()
    record = offline.storage.seed(offline.store, path, content, kind=kind)
    if changed:
        offline.storage.objects[record.object_key] += b"Changed"
    response = await offline.client.post(
        f"{offline.base}/skills", json={"path": path, "sha256": digest}
    )
    assert response.status_code == status
    assert [item["name"] for item in (await offline.client.get("/api/skills")).json()] == ["table"]


@pytest.mark.asyncio
async def test_ui_stream_marks_only_terminal_python_output_as_submission():
    frames = []
    snapshots_at_marker = []

    async def emit(frame):
        frames.append(frame)
        if frame["type"] == "data-python-submission":
            snapshots_at_marker.append(stream.snapshot("root"))

    stream = ui_stream.UiStream("root", emit)
    payload = {"answer": "5", "artifact_paths": []}
    for execution_id, submission in enumerate((False, True)):
        await stream.publish("root", agent.PythonExecStartedEvent(execution_id, "run()"))
        if submission:
            await stream.publish("root", agent.PythonExecProgressEvent(execution_id, "working\n"))
        await stream.publish(
            "root", agent.PythonExecCompletedEvent(execution_id, payload, submission=submission)
        )

    ordinary, preview, marker, submitted = [
        frame
        for frame in frames
        if frame["type"] in {"tool-output-available", "data-python-submission"}
    ]
    assert ordinary["output"] == submitted["output"] == payload
    assert preview["preliminary"] is True and preview["output"] == "working\n"
    assert preview["toolCallId"] == submitted["toolCallId"]
    assert marker["data"] == {"tool_call_id": submitted["toolCallId"]}
    assert ordinary["toolCallId"] != submitted["toolCallId"]
    assert snapshots_at_marker == [stream.snapshot("root")]
    _, final_part, persisted_marker = snapshots_at_marker[0]
    assert persisted_marker == marker
    assert final_part["output"] == payload and "preliminary" not in final_part


def test_ui_frames_and_lossless_nested_snapshots():
    root = ui_stream.UiPartAccumulator("root")
    assert root.start() == {"type": "start", "messageId": "root"}
    assert root.data("skills", {"skills": [{"name": "csv", "score": 0.8}]})["type"] == "data-skills"
    reason = list(root.reasoning("Check the input"))
    assert [frame["type"] for frame in reason] == [
        "reasoning-start",
        "reasoning-delta",
        "reasoning-end",
    ]
    assert len({frame["id"] for frame in reason}) == 1
    code = "print('x')"
    assert list(root.python("py", code)) == [
        {
            "type": "tool-input-start",
            "toolCallId": "py",
            "toolName": "python_exec",
            "inputEncoding": "text",
        },
        {"type": "tool-input-delta", "toolCallId": "py", "inputTextDelta": code},
    ]
    assert root.tool_error("py", "invalid")["type"] == "tool-output-error"
    assert (
        root.subagent("a", {"task": "Count", "kind": "subagent"})["type"] == "tool-input-available"
    )
    child = ui_stream.UiPartAccumulator("child")
    child.subagent("b", {"task": "Verify", "kind": "llm_query"})
    grandchild = ui_stream.UiPartAccumulator("grandchild")
    early = root.tool_output("a", child.message(), preliminary=True)
    list(grandchild.finish("Verified"))
    child.tool_output("b", grandchild.message(), preliminary=False)
    partial = root.tool_output("a", child.message(), preliminary=True)
    list(child.finish("Two"))
    final = root.tool_output("a", child.message(), preliminary=False)
    root.data("file-tree", {"files": []})
    root.data("file-tree", {"files": [{"path": "counts.csv"}]})
    finish = list(root.finish("Two rows"))
    assert [frame["type"] for frame in finish] == ["text-start", "text-delta", "text-end", "finish"]
    assert early["output"]["parts"][0]["state"] == "input-available"
    assert partial["preliminary"] and len(partial["output"]["parts"]) == 1
    assert final["preliminary"] is False
    expected = [
        {
            "type": "data-skills",
            "id": "root:skills",
            "data": {"skills": [{"name": "csv", "score": 0.8}]},
        },
        {"type": "reasoning", "id": reason[0]["id"], "text": "Check the input", "state": "done"},
        {
            "type": "tool-python_exec",
            "toolCallId": "py",
            "state": "output-error",
            "input": code,
            "rawInput": code,
            "errorText": "invalid",
        },
        {
            "type": "tool-subagent",
            "toolCallId": "a",
            "state": "output-available",
            "input": {"task": "Count", "kind": "subagent"},
            "output": child.message(),
            "preliminary": False,
        },
        {
            "type": "data-file-tree",
            "id": "root:file-tree",
            "data": {"files": [{"path": "counts.csv"}]},
        },
        {"type": "text", "text": "Two rows", "state": "done"},
    ]
    snapshot = root.snapshot()
    assert json.loads(json.dumps(snapshot)) == expected
    snapshot[3]["output"]["parts"][0]["output"]["parts"].clear()
    assert root.snapshot() == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("skill_name", ["table", "skill-creator"])
async def test_chat_supplies_session_only_memories_to_skill_creator(
    offline, monkeypatch, skill_name
):
    runtime = api.app.state.runtime
    creator = SkillDefinition("skill-creator", "Draft a skill", "Use memories.", Path("SKILL.md"))
    api.app.state.runtime = replace(
        runtime, skills=SkillRegistry((*runtime.skills.skills, creator))
    )
    global_memory = db.Memory(
        id=uuid4(),
        session_id=None,
        content="Memory candidate (pending approval): Use USD for financial totals",
        approved=True,
    )
    session_memory = db.Memory(
        id=uuid4(),
        session_id=offline.store.session_id,
        content="Prefix summaries with COBALT-QA",
        approved=True,
    )
    procedure = db.Memory(content="Verify column types before aggregating.")
    procedure_lookup = AsyncMock(return_value=[procedure] if skill_name == "table" else [])
    monkeypatch.setattr(db, "recall_procedures", procedure_lookup)
    monkeypatch.setattr(db, "search_memories", AsyncMock(return_value=[global_memory]))
    session_lookup = AsyncMock(return_value=[session_memory])
    monkeypatch.setattr(db, "list_approved_session_memories", session_lookup)
    prior = [{"user": "The earlier marker was ALDER-58.", "assistant": "Acknowledged."}]
    prior_lookup = AsyncMock(return_value=prior)
    monkeypatch.setattr(db, "get_session_prior_turns", prior_lookup)
    turn = AsyncMock(return_value=dspy.Prediction(answer="Done", history=dspy.History(messages=[])))
    monkeypatch.setattr(agent, "run_workspace_task", turn)

    response = await offline.client.post(
        f"{offline.base}/chat",
        json={
            "messages": [{"role": "user", "parts": [{"type": "text", "text": "Use a skill"}]}],
            "pinned_skills": [skill_name],
        },
    )

    assert response.status_code == 200
    prior_lookup.assert_awaited_once_with(
        offline.store,
        offline.store.session_id,
        max_turns=offline.settings.history_max_turns,
        max_chars=offline.settings.history_max_chars,
    )
    assert turn.await_args.kwargs["prior_turns"] == prior
    procedure_lookup.assert_awaited_once_with(
        offline.store, offline.store.session_id, "Use a skill", [skill_name]
    )
    if skill_name == "skill-creator":
        session_lookup.assert_awaited_once_with(offline.store, offline.store.session_id)
        assert turn.await_args.kwargs["memories"] == [session_memory.content]
    else:
        session_lookup.assert_not_awaited()
        assert turn.await_args.kwargs["memories"] == [
            f"Approved procedure\n{procedure.content}",
            "Use USD for financial totals",
        ]


@pytest.mark.asyncio
async def test_refine_saved_turn_creates_one_reviewable_lesson(offline, monkeypatch):
    run_id = uuid4()
    session_id = offline.store.session_id
    run = db.Run(id=run_id, status="failed", prompt="x" * 2001, ui_parts=[])
    lookup = AsyncMock(return_value=None)
    monkeypatch.setattr(db, "get_turn_for_refinement", lookup)
    content = "When an extract is truncated, search for a narrower source before opening it."
    lesson = {"content": content, "evidence_ref": "web_open.truncated", "skill_name": "table"}
    model = DummyLM(
        [
            {"lesson": None},
            {"lesson": {**lesson, "evidence_ref": "nonexistent"}},
            {"lesson": {**lesson, "skill_name": "unselected"}},
            {"lesson": lesson},
        ]
    )
    monkeypatch.setattr(settings_routes, "_request_sub_lm", lambda *_: model)
    refine = agent.refine_turn

    async def predict(*args, **kwargs):
        assert offline.store.checkouts == 0
        return await refine(*args, **kwargs)

    monkeypatch.setattr(agent, "refine_turn", predict)

    async def save(_db, sid, source_id, **fields):
        assert (sid, source_id) == (session_id, run_id)
        record = db.Memory(session_id=sid, source_run_id=source_id, approved=False, **fields)
        offline.store.add(record)
        lookup.return_value = run, record
        return record

    save_lesson = AsyncMock(side_effect=save)
    monkeypatch.setattr(db, "save_turn_lesson", save_lesson)
    path = f"{offline.base}/runs/{run_id}/refine"

    assert (await offline.client.post(path, headers={"Authorization": ""})).status_code == 401
    assert (await offline.client.post(path)).status_code == 404
    lookup.assert_awaited_once_with(offline.store, session_id, run_id)
    lookup.return_value = run, None
    assert (await offline.client.post(path)).status_code == 409
    run.status = "completed"
    assert (await offline.client.post(path)).status_code == 409
    assert model.history == []

    opened = {"tool": "web_open", "outcome": "truncated", "target": "PRIVATE_TARGET"}
    run.ui_parts = [
        {"type": "data-skills", "data": {"skills": [{"name": "table", "score": 1}]}},
        {"type": "data-web-call", "data": {"tool": "web_search", "outcome": "results"}},
        {"type": "data-web-call", "data": {**opened, "id": "first"}},
        {"type": "data-web-call", "data": {**opened, "id": "second"}},
        {"type": "reasoning", "text": "PRIVATE_NOTES"},
        {"type": "tool-python_exec", "state": "output-error", "input": "PRIVATE_CODE"},
        {
            "type": "data-turn-details",
            "data": {
                "runtime": "dspy.RLM",
                "completion": "submit",
                "root_actions": 5,
            },
        },
    ]
    assert (await offline.client.post(path)).json() == {"memory": None}
    assert (await offline.client.post(path)).status_code == 502
    assert (await offline.client.post(path)).status_code == 502
    save_lesson.assert_not_awaited()
    result = await offline.client.post(path)
    assert result.status_code == 200
    memory = result.json()["memory"]
    assert memory["content"] == content
    assert memory["approved"] is False
    assert memory["skill_name"] == "table"
    assert memory["source_run_id"] == str(run_id)
    assert (await offline.client.post(path)).json() == result.json()
    save_lesson.assert_awaited_once()

    user_input = model.get_convo(-1)[0][-1]["content"]
    assert '"web_open.truncated": 2' in user_input
    assert '"web_open.repeated_displayed_targets": 1' in user_input
    assert '"python.root_errors": 1' in user_input
    assert '"rlm.completion": "submit"' in user_input
    assert "x" * 2001 not in user_input and "x" * 2000 in user_input
    assert "PRIVATE_" not in user_input
    scoped = await offline.client.get(
        "/api/memories", params={"session_id": str(session_id), "memory_id": memory["id"]}
    )
    assert scoped.json() == [memory]


@pytest.mark.asyncio
async def test_approving_legacy_memory_displays_only_the_fact(offline):
    record = db.Memory(
        id=uuid4(),
        session_id=None,
        content="Memory candidate (pending approval): Use code FIR-42 for the handoff.",
        approved=False,
    )
    offline.store.add(record)
    response = await offline.client.patch(f"/api/memories/{record.id}", json={"approved": True})
    assert response.status_code == 200
    assert response.json()["approved"] is True
    assert response.json()["content"] == "Use code FIR-42 for the handoff."


@pytest.mark.asyncio
async def test_consolidate_searches_prompt_and_answer_including_unapproved_memories(
    offline, monkeypatch
):
    decision = SimpleNamespace(needs_skill=SimpleNamespace(value=False))
    monkeypatch.setattr(
        skills, "build_skill_selector", Mock(return_value=Mock(return_value=decision))
    )
    pending_id = uuid4()
    approved_id = uuid4()
    pending_memory = db.Memory(
        id=pending_id,
        session_id=None,
        content="The user's name is Zachary.",
        approved=False,
    )
    approved_memory = db.Memory(
        id=approved_id,
        session_id=offline.store.session_id,
        content="Prefix summaries with COBALT-QA.",
        approved=True,
    )
    search_calls: list[dict] = []

    async def fake_search(_sessionmaker, session_id, query, **kwargs):
        search_calls.append({"session_id": session_id, "query": query, "kwargs": kwargs})
        if kwargs.get("include_unapproved"):
            return [pending_memory, approved_memory]
        return [approved_memory]

    monkeypatch.setattr(db, "search_memories", fake_search)
    turn = AsyncMock(
        return_value=dspy.Prediction(
            answer="Remembered Zachary.", history=dspy.History(messages=[])
        )
    )
    monkeypatch.setattr(agent, "run_workspace_task", turn)

    response = await offline.client.post(
        f"{offline.base}/chat",
        json={"messages": [{"role": "user", "parts": [{"type": "text", "text": "remember it"}]}]},
    )

    assert response.status_code == 200
    # Turn execution still receives approved memories only.
    assert turn.await_args.kwargs["memories"] == [approved_memory.content]
    assert len(search_calls) == 2
    assert search_calls[0]["query"] == "remember it"
    assert not search_calls[0]["kwargs"].get("include_unapproved", False)
    # Consolidation searches prompt + answer including pending unapproved candidates.
    assert search_calls[1]["query"] == "remember it\nRemembered Zachary."
    assert search_calls[1]["kwargs"] == {"limit": 3, "include_unapproved": True}
    assert offline.memory.await_args.args[3] == [
        {
            "id": str(pending_id),
            "content": "The user's name is Zachary.",
            "scope": "global",
            "approved": False,
        },
        {
            "id": str(approved_id),
            "content": "Prefix summaries with COBALT-QA.",
            "scope": "session",
            "approved": True,
        },
    ]


@pytest.fixture
def trace_uri(tmp_path):
    uri = f"sqlite:///{tmp_path / 'traces.db'}"
    previous = mlflow.get_tracking_uri()
    mlflow.set_tracking_uri(uri)
    try:
        yield uri
    finally:
        mlflow.set_tracking_uri(previous)


@pytest.mark.asyncio
async def test_workspace_startup_failure_returns_actionable_stream_error(
    offline, monkeypatch, caplog, trace_uri
):
    tracking_uri = trace_uri
    runtime = api.app.state.runtime
    api.app.state.runtime = replace(
        runtime, settings=offline.settings.model_copy(update={"mlflow_tracking_uri": tracking_uri})
    )
    caplog.set_level("INFO", logger=chat_routes.__name__)
    monkeypatch.setattr(
        chat_routes,
        "_chat_sandbox",
        AsyncMock(side_effect=interpreter.SandboxStartupError("private provider diagnostics")),
    )
    with mlflow.start_span("incoming HTTP request") as http_span:
        response = await offline.client.post(
            f"{offline.base}/chat",
            json={"messages": [{"role": "user", "parts": [{"type": "text", "text": "Hello"}]}]},
        )
    assert response.status_code == 200
    frames = [
        json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: {")
    ]
    assert [frame["type"] for frame in frames] == ["data-turn-details", "error"]
    assert frames[0]["data"]["runtime"] is None
    assert frames[0]["data"]["completion"] == "failed"
    assert frames[0]["data"]["trace_url"] is None
    assert "Fleet could not start the workspace. Try again." in response.text
    assert "private provider diagnostics" not in response.text
    assert response.text.endswith("data: [DONE]\n\n")
    trace_id = next(
        record.args[1] for record in caplog.records if record.msg == "Turn %s MLflow trace %s"
    )
    mlflow.flush_trace_async_logging()
    trace = mlflow.get_trace(trace_id)
    assert trace is not None
    assert trace_id != http_span.trace_id
    root, acquisition, finalize, cleanup = trace.data.spans
    assert root.parent_id is None
    assert (root.name, root.attributes["fleet.turn.status"]) == ("fleet.turn", "failed")
    assert root.status.status_code.name == "ERROR"
    assert trace.info.tags["fleet.run_id"] == str(offline.finish.await_args.args[1])
    assert trace.info.tags["fleet.trace.kind"] == "turn"
    assert trace.info.tags["fleet.outcome"] == "failed"
    assert trace.info.tags["fleet.error.stage"] == "sandbox.acquire"
    assert trace.info.request_preview == "Hello"
    assert trace.info.response_preview == "failed during sandbox.acquire"
    assert [span.name for span in (acquisition, finalize, cleanup)] == [
        "daytona.sandbox.acquire",
        "fleet.turn.finalize",
        "fleet.workspace.cleanup",
    ]
    assert all(span.parent_id == root.span_id for span in (acquisition, finalize, cleanup))
    assert acquisition.attributes["error.type"] == "SandboxStartupError"
    assert "private provider diagnostics" not in trace.to_json()


@pytest.mark.asyncio
async def test_hydration_failure_preserves_cause_in_mlflow(offline, monkeypatch, caplog, trace_uri):
    runtime = api.app.state.runtime
    monkeypatch.setattr(
        api.app.state,
        "runtime",
        replace(
            runtime, settings=offline.settings.model_copy(update={"mlflow_tracking_uri": trace_uri})
        ),
    )
    offline.storage.seed(offline.store, "files/input.csv", b"input")

    async def fail_download(*_args, **_kwargs):
        try:
            raise OSError("private storage diagnostics")
        except OSError as cause:
            raise StorageError("Could not download workspace file") from cause

    offline.storage.download.side_effect = fail_download
    caplog.set_level("INFO", logger=chat_routes.__name__)
    response = await offline.client.post(
        f"{offline.base}/chat",
        json={"messages": [{"role": "user", "parts": [{"type": "text", "text": "Hello"}]}]},
    )

    assert response.status_code == 200
    assert response.text.endswith("data: [DONE]\n\n")
    assert any(
        record.args[-1] == "OSError"
        for record in caplog.records
        if record.msg == "Turn %s failed (%s)"
    )
    trace_id = next(
        record.args[1] for record in caplog.records if record.msg == "Turn %s MLflow trace %s"
    )
    mlflow.flush_trace_async_logging()
    trace = mlflow.get_trace(trace_id)
    assert trace is not None
    root = next(span for span in trace.data.spans if span.name == "fleet.turn")
    assert root.attributes["fleet.error.stage"] == "workspace.hydrate"
    assert root.attributes["error.type"] == "OSError"
    assert trace.info.tags["fleet.outcome"] == "failed"
    assert "private storage diagnostics" not in trace.to_json()


@pytest.mark.asyncio
async def test_concurrent_chat_traces_correlate_memory_and_commit_previews(
    offline,
    monkeypatch,
    trace_uri,
):
    tracking_uri = trace_uri
    runtime = api.app.state.runtime
    api.app.state.runtime = replace(
        runtime,
        settings=offline.settings.model_copy(update={"mlflow_tracking_uri": tracking_uri}),
    )
    other = (await offline.client.post("/api/sessions", json={"title": "Other"})).json()["id"]
    offline.store.sessions[UUID(other)].daytona_sandbox_id = "test-sandbox"
    both_running = asyncio.Event()
    arrivals = 0

    async def run(_settings, *, task, **_):
        nonlocal arrivals
        arrivals += 1
        if arrivals == 2:
            both_running.set()
        await asyncio.wait_for(both_running.wait(), timeout=2)
        return dspy.Prediction(
            answer=f"Committed {task}: " + "界" * 400,
            history=dspy.History(messages=[]),
        )

    monkeypatch.setattr(agent, "run_workspace_task", run)
    offline.memory.side_effect = None
    seed = "sk-canary-only-not-real-1234567890"

    async def send_turn(session_id, marker):
        return await offline.client.post(
            f"/api/sessions/{session_id}/chat",
            json={
                "messages": [
                    {
                        "role": "user",
                        "parts": [
                            {"type": "text", "text": f"{marker} @table {seed}"},
                        ],
                    }
                ]
            },
        )

    await asyncio.gather(send_turn(offline.store.session_id, "First"), send_turn(other, "Second"))
    mlflow.flush_trace_async_logging()
    traces = mlflow.search_traces(locations=["0"], return_type="list")
    turns = [t for t in traces if t.info.tags.get("fleet.trace.kind") == "turn"]
    memories = [t for t in traces if t.info.tags.get("fleet.trace.kind") == "memory"]
    assert len(turns) == len(memories) == 2
    assert {t.info.trace_metadata["mlflow.trace.session"] for t in turns} == {
        str(offline.store.session_id),
        other,
    }
    for turn in turns:
        run_id = turn.info.tags["fleet.run_id"]
        memory = next(t for t in memories if t.info.tags["fleet.run_id"] == run_id)
        assert (
            memory.info.trace_metadata["mlflow.trace.session"]
            == (turn.info.trace_metadata["mlflow.trace.session"])
        )
        assert memory.info.tags["fleet.parent_trace_id"] == turn.info.trace_id
        assert memory.data.spans[0].parent_id is None
        assert turn.info.tags["fleet.outcome"] == memory.info.tags["fleet.outcome"] == "completed"
        assert turn.info.response_preview.startswith("Committed ")
        assert len(turn.info.response_preview.encode()) <= 768
        assert "[REDACTED]" in turn.info.request_preview
        assert seed not in turn.to_json()
        root = turn.data.spans[0]
        spans = {span.name: span for span in turn.data.spans}
        assert spans["fleet.skills.route"].attributes["fleet.skills.route"] == "pinned"
        assert spans["fleet.memory.retrieve"].outputs == {"memories": 0, "procedures": 0}
        assert (
            spans["fleet.skills.route"].parent_id
            == spans["fleet.memory.retrieve"].parent_id
            == root.span_id
        )


@pytest.mark.asyncio
async def test_turn_timeout_is_searchable_in_mlflow(offline, monkeypatch, trace_uri):
    uri = trace_uri
    runtime = api.app.state.runtime
    api.app.state.runtime = replace(
        runtime,
        settings=runtime.settings.model_copy(update={"mlflow_tracking_uri": uri}),
    )
    turn_worker = AsyncMock(side_effect=TimeoutError("Private provider timeout"))
    monkeypatch.setattr(agent, "run_workspace_task", turn_worker)
    response = await offline.client.post(f"{offline.base}/chat", json=_PAYLOAD)
    assert response.status_code == 200
    run_id = offline.finish.await_args.args[1]
    mlflow.flush_trace_async_logging()
    traces = mlflow.MlflowClient(tracking_uri=uri).search_traces(
        locations=["0"],
        filter_string=f"tag.`fleet.run_id` = '{run_id}'",
    )
    trace = traces[0]
    assert trace.info.tags["fleet.outcome"] == "timeout"
    assert trace.info.tags["fleet.error.stage"] == "rlm"
    assert trace.info.response_preview == "timeout during rlm"
    assert "Private provider timeout" not in trace.to_json()


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["primary", "decision"])
async def test_chat_billing_error_matches_failing_model(offline, monkeypatch, role):
    runtime = api.app.state.runtime
    failure = dspy.LMBillingError("private provider billing details")
    if role == "decision":
        decision_model = SimpleNamespace(model="typesafe:jev-test")
        monkeypatch.setattr(
            chat_routes, "_request_decision_lm", AsyncMock(return_value=decision_model)
        )
        monkeypatch.setattr(chat_routes, "select_skills", Mock(side_effect=failure))
        run = AsyncMock()
        expected = (
            "Billing failed while using the Decision Model (typesafe:jev-test). "
            "Update it in Settings → Models."
        )
    else:
        run = AsyncMock(side_effect=failure)
        expected = (
            f"Billing failed while using workspace provider · {runtime.settings.llm_model}. "
            "Choose another model and retry."
        )
    monkeypatch.setattr(agent, "run_workspace_task", run)
    response = await offline.client.post(
        f"{offline.base}/chat",
        json={
            "messages": [{"role": "user", "parts": [{"type": "text", "text": "Hello"}]}],
            "pinned_skills": ["table"] if role == "primary" else [],
        },
    )
    assert response.status_code == 200
    frames = [
        json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: {")
    ]
    assert [frame["errorText"] for frame in frames if frame["type"] == "error"] == [expected]
    if role == "decision":
        run.assert_not_awaited()
    else:
        run.assert_awaited_once()


@pytest.mark.asyncio
async def test_post_sse_frames_and_persistence(offline, monkeypatch):
    child_id, nested_id = uuid4(), uuid4()
    offline.create_child.side_effect = [SimpleNamespace(id=child_id), SimpleNamespace(id=nested_id)]
    run_turn = chat_routes._run_turn
    sse_queue = None

    async def capture_queue(request, runtime, turn, queue, background):
        nonlocal sse_queue
        sse_queue = queue
        return await run_turn(request, runtime, turn, queue, background)

    monkeypatch.setattr(chat_routes, "_run_turn", capture_queue)

    async def complete(_settings, *, task, run_id, selection, emit, callbacks, **_):
        assert offline.store.checkouts == 0
        assert task == "Analyze @table" and selection.active_skills[0].name == "table"
        await emit(run_id, agent.RunStartedEvent("workspace"))
        await emit(run_id, agent.ReasoningEvent("Inspect the data"))
        await emit(run_id, agent.PythonExecStartedEvent(1, "```python\nprint(x)\n```"))
        assert sse_queue.full()
        await emit(run_id, agent.PythonExecProgressEvent(1, "skipped preview\n"))
        async with asyncio.timeout(2):
            while sse_queue.full():
                await asyncio.sleep(0)
        await emit(run_id, agent.PythonExecProgressEvent(1, "working\n"))
        await emit(run_id, agent.PythonExecFailedEvent(1, "NameError: x"))

        child = agent.ChildRun(str(child_id), run_id, 1, "subagent", "Read the table")
        assert await callbacks.on_child_start(child) == child_id
        await emit(run_id, agent.SubagentStartedEvent(child))
        await emit(child.id, agent.RunStartedEvent("workspace"))

        nested = agent.ChildRun(str(nested_id), child.id, 2, "llm_query", "Check the totals")
        assert await callbacks.on_child_start(nested) == nested_id
        await emit(child.id, agent.SubagentStartedEvent(nested))
        await emit(nested.id, agent.RunStartedEvent("llm_query"))
        await emit(nested.id, agent.RunResultEvent("Totals checked"))
        offline.finish.assert_not_awaited()
        assert offline.store.statements == []
        await callbacks.on_child_finish(nested, "completed", "Totals checked", None)
        await emit(child.id, agent.SubagentCompletedEvent(nested.id))

        await emit(child.id, agent.RunResultEvent("Table read"))
        assert offline.finish.await_count == 1
        assert offline.store.statements == []
        await callbacks.on_child_finish(child, "completed", "Table read", None)
        await emit(run_id, agent.SubagentCompletedEvent(child.id))
        await emit(run_id, agent.RunResultEvent("Verified"))
        return dspy.Prediction(answer="Verified", history=dspy.History(messages=[{"task": task}]))

    monkeypatch.setattr(agent, "run_workspace_task", complete)
    response = await offline.client.post(f"{offline.base}/chat", json=_PAYLOAD)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["x-vercel-ai-ui-message-stream"] == "v1"
    assert response.headers["x-accel-buffering"] == "no"
    assert response.text.endswith("data: [DONE]\n\n")
    chunks = response.text.strip().split("\n\n")
    frames = [
        json.loads(next(line[6:] for line in chunk.splitlines() if line.startswith("data: ")))
        for chunk in chunks[:-1]
    ]
    assert [frame["type"] for frame in frames] == [
        "start",
        "data-skills",
        "data-file-tree",
        "reasoning-start",
        "reasoning-delta",
        "reasoning-end",
        "tool-input-start",
        "tool-input-delta",
        "tool-output-available",
        "tool-output-error",
        "tool-input-available",
        *["tool-output-available"] * 15,
        "data-file-tree",
        "data-turn-details",
        "text-start",
        "text-delta",
        "text-end",
        "finish",
    ]
    root_id = frames[0]["messageId"]
    for index, chunk in enumerate(chunks[:-1]):
        assert f"id: {root_id}:{index}" in chunk.splitlines()
    assert frames[1] == {
        "type": "data-skills",
        "id": f"{root_id}:skills",
        "data": {"skills": [{"name": "table", "score": 1.0}]},
    }
    assert frames[2]["id"] == frames[-6]["id"] == f"{root_id}:file-tree"
    assert frames[-6]["data"]["files"][0]["path"] == "artifacts/report.txt"
    assert [frame["id"] for frame in frames[3:6]] == [f"{root_id}:1"] * 3
    assert frames[6]["toolCallId"] == frames[9]["toolCallId"] == f"{root_id}:2"
    assert frames[6]["inputEncoding"] == "text"
    assert frames[7]["inputTextDelta"] == "print(x)"
    assert frames[8] == {
        "type": "tool-output-available",
        "toolCallId": f"{root_id}:2",
        "output": "working\n",
        "preliminary": True,
    }
    assert frames[9]["errorText"] == "NameError: x"
    assert frames[10]["toolCallId"] == f"{root_id}:3"
    progress = frames[11:-6]
    assert all(frame["preliminary"] is True for frame in progress[:-1])
    assert progress[0]["output"] == {"id": str(child_id), "role": "assistant", "parts": []}

    nested_finish, child_finish, root_finish = offline.finish.await_args_list
    child_parts = child_finish.kwargs["ui_parts"]
    assert progress[-1] == {
        "type": "tool-output-available",
        "toolCallId": f"{root_id}:3",
        "output": {"id": str(child_id), "role": "assistant", "parts": child_parts},
        "preliminary": False,
    }
    assert child_parts[2]["output"] == {
        "id": str(nested_id),
        "role": "assistant",
        "parts": nested_finish.kwargs["ui_parts"],
    }
    assert child_parts[2]["preliminary"] is False
    assert root_finish.kwargs["ui_parts"][-3]["output"] == progress[-1]["output"]
    assert root_finish.kwargs["ui_parts"][1] == frames[-6]
    assert "output" not in root_finish.kwargs["ui_parts"][3]
    assert [item["path"] for item in root_finish.kwargs["files"]] == ["artifacts/report.txt"]
    assert offline.store.files[(offline.store.session_id, "artifacts/report.txt")].size_bytes == 6
    assert offline.create_child.await_count == 2
    assert [call.kwargs["status"] for call in offline.finish.await_args_list] == [
        "completed",
        "completed",
        "completed",
    ]
    assert offline.store.statements == []
    assert offline.finish.await_args.kwargs["answer"] == "Verified"
    assert offline.finish.await_args.kwargs["ui_parts"][-1]["text"] == "Verified"
    assert offline.store.session.history_json == {"messages": [{"task": "Analyze @table"}]}
    assert not offline.store.active and offline.store.checkouts == 0
    offline.memory.assert_awaited_once()


@pytest.mark.asyncio
async def test_rejected_transcript_releases_run_and_retires_vm(offline, monkeypatch):
    async def reject_once(*args, **kwargs):
        offline.finish.side_effect = offline.store.finish
        raise SQLAlchemyError("rejected transcript")

    async def interrupt(*_args, **_kwargs):
        raise RuntimeError("interrupted")

    monkeypatch.setattr(agent, "run_workspace_task", interrupt)
    offline.finish.side_effect = reject_once
    response = await offline.client.post(f"{offline.base}/chat", json=_PAYLOAD)
    assert response.text.endswith("data: [DONE]\n\n")
    terminal = offline.finish.await_args.kwargs
    assert terminal["status"] == "failed"
    assert [part["type"] for part in terminal["ui_parts"]] == ["data-turn-details"]
    assert terminal["retire_sandbox_id"] == "test-sandbox"
    assert not offline.store.active
    offline.daytona.v2.delete.assert_awaited_once_with("test-sandbox", timeout=30)


@pytest.mark.parametrize(
    ("status", "retry"), [("failed", False), ("failed", True), ("cancelled", False)]
)
@pytest.mark.asyncio
async def test_incomplete_child_snapshot_survives_root_teardown(
    offline, monkeypatch, status, retry
):
    async def interrupt(_settings, *, run_id, emit, callbacks, **_):
        await emit(run_id, agent.RunStartedEvent("workspace"))
        child_id = await callbacks.on_child_start(
            agent.ChildRun("placeholder", run_id, 1, "subagent", "Read the table")
        )
        child = agent.ChildRun(str(child_id), run_id, 1, "subagent", "Read the table")
        await emit(run_id, agent.SubagentStartedEvent(child))
        await emit(child.id, agent.RunStartedEvent("workspace"))
        await emit(child.id, agent.PythonExecStartedEvent(1, "print('working')"))
        await emit(child.id, agent.WebCallEvent("request-1", "web_open", "https://example.org"))
        assert offline.store.statements == []
        offline.finish.assert_not_awaited()
        if status == "cancelled":
            raise asyncio.CancelledError
        raise RuntimeError("Turn interrupted before the child was finalized")

    monkeypatch.setattr(agent, "run_workspace_task", interrupt)
    if retry:

        async def fail_first_write(*_args, **_kwargs):
            offline.finish.side_effect = offline.store.finish
            raise RuntimeError("First root write failed")

        offline.finish.side_effect = fail_first_write
    response = await offline.client.post(f"{offline.base}/chat", json=_PAYLOAD)
    assert response.status_code == 200
    assert ("data: [DONE]" in response.text) is (status == "failed")
    assert offline.finish.await_count == (2 if retry else 1)
    assert offline.finish.await_args.kwargs["status"] == status
    root_parts = offline.finish.await_args.kwargs["ui_parts"]
    child_part, web_part, details = root_parts[-3:]
    assert web_part["data"]["outcome"] == "interrupted"
    assert details["data"]["completion"] == status
    if status == "failed":
        events = [
            json.loads(line[6:])
            for line in response.text.splitlines()
            if line.startswith("data: {")
        ]
        assert web_part in events and details in events
    assert child_part["preliminary"] is True
    parts = child_part["output"]["parts"]
    assert parts[-1]["state"] == "input-streaming"
    assert parts[-1]["input"] == "print('working')"

    child_update, *fallback_updates = offline.store.statements
    assert child_update.compile().params["status"] == status
    assert child_update.compile().params["id_1"] == offline.create_child.return_value.id
    assert child_update.compile().params["ui_parts"] == parts
    assert len(fallback_updates) == offline.finish.await_count
    assert all("ui_parts" not in update.compile().params for update in fallback_updates)


@pytest.mark.parametrize("asgi_version", ["2.3", "2.4"])
@pytest.mark.asyncio
async def test_disconnect_aborts_context_and_cancels_run(
    offline,
    asgi_version,
    trace_uri,
):
    uri = trace_uri
    runtime = api.app.state.runtime
    api.app.state.runtime = replace(
        runtime,
        settings=runtime.settings.model_copy(update={"mlflow_tracking_uri": uri}),
    )
    executing = asyncio.Event()
    disconnected = asyncio.Event()
    frames = asyncio.Queue()
    cursor = 0

    async def write_stdin(message):
        request = json.loads(message)
        if "Verified" in request.get("code", ""):
            executing.set()
        else:
            frame = f"\x1e{request['token']} " + json.dumps({"kind": "done"}) + "\n"
            frames.put_nowait(base64.b64encode(frame.encode()))

    async def logs(**_options):
        nonlocal cursor
        while True:
            data = await frames.get()
            cursor += 1
            yield SimpleNamespace(
                type="log", cursor=str(cursor), frame=SimpleNamespace(channel="stdout", data=data)
            )

    sandbox = offline.sandbox
    worker = SimpleNamespace(
        id="worker-1", write_stdin=write_stdin, stream_logs=logs, delete=AsyncMock()
    )
    sandbox.process = SimpleNamespace(start=AsyncMock(return_value=worker), kill_all=AsyncMock())
    request_sent = False

    async def receive():
        nonlocal request_sent
        if not request_sent:
            request_sent = True
            return {
                "type": "http.request",
                "body": json.dumps(_PAYLOAD).encode(),
                "more_body": False,
            }
        await disconnected.wait()
        return {"type": "http.disconnect"}

    sent = []

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": asgi_version},
        "method": "POST",
        "scheme": "https",
        "path": f"{offline.base}/chat",
        "root_path": "",
        "query_string": b"",
        "headers": [
            (b"authorization", b"Bearer offline-token"),
            (b"content-type", b"application/json"),
        ],
    }
    request = asyncio.create_task(api.app(scope, receive, send))
    try:
        await asyncio.wait_for(executing.wait(), timeout=3)
        root_id = offline.store.active
        assert root_id and offline.store.checkouts == 0
        assert sent[0]["status"] == 200
        version_response = await offline.client.post(f"{offline.base}/chat", json=_PAYLOAD)
        assert version_response.status_code == 409
    finally:
        disconnected.set()
        await asyncio.wait_for(request, timeout=2)

    worker.delete.assert_awaited_once()
    sandbox.process.kill_all.assert_awaited_once_with(
        tag=sandbox.process.start.await_args.kwargs["tag"], timeout=10
    )
    assert offline.finish.await_args.args == (offline.store, root_id)
    assert offline.finish.await_args.kwargs["status"] == "cancelled"
    part, details = offline.finish.await_args.kwargs["ui_parts"][-2:]
    assert details["data"]["runtime"] == "dspy.RLM"
    assert details["data"]["root_actions"] == 1
    assert details["data"]["completion"] == "cancelled"
    assert part["state"] == "input-streaming"
    assert part["input"] == "SUBMIT('Verified', [])"
    assert offline.store.active is None and offline.store.checkouts == 0
    assert not any(b"[DONE]" in message.get("body", b"") for message in sent)
    offline.memory.assert_not_awaited()
    mlflow.flush_trace_async_logging()
    traces = mlflow.MlflowClient(tracking_uri=uri).search_traces(
        locations=["0"],
        filter_string=f"tag.`fleet.run_id` = '{root_id}'",
    )
    assert len(traces) == 1
    assert traces[0].info.tags["fleet.outcome"] == "cancelled"
    assert traces[0].info.response_preview == "cancelled during rlm"


@pytest.mark.asyncio
@pytest.mark.parametrize("model_id", ["openai/gpt-6-luna", "gpt-6-luna"])
async def test_chat_model_reports_and_persists_model_bound_effort(offline, model_id, monkeypatch):
    monkeypatch.setattr(
        agent, "_model", lambda settings, model: dspy.LM(model, api_base=settings.llm_base_url)
    )
    offline.settings.llm_model = model_id
    original = {"model": model_id, "effort": "high"}
    url = f"{offline.base}/chat-model"
    model = (await offline.client.get(url)).json()
    assert model["id"] == original["model"]
    assert "Luna" in model["label"]
    assert model["efforts"] == ["none", "low", "medium", "high", "xhigh", "max"]
    assert model["effort"] is None
    assert (
        await offline.client.patch(offline.base, json={"reasoning_override": original})
    ).status_code == 200
    assert (await offline.client.get(url)).json()["effort"] == "high"
    assert (
        await offline.client.patch(
            offline.base, json={"reasoning_override": {**original, "effort": "minimal"}}
        )
    ).status_code == 422
    offline.settings.llm_model = "openai/gpt-4.1"
    model = (await offline.client.get(url)).json()
    assert model["label"] == "GPT-4.1" and model["efforts"] == [] and model["effort"] is None
    assert (
        await offline.client.patch(offline.base, json={"reasoning_override": original})
    ).status_code == 409
    changed = await offline.client.patch(offline.base, json={"model_override": "openai/gpt-6-luna"})
    assert changed.json()["reasoning_override"] is None
    explicit = {"model": "openai/gpt-6-luna", "effort": "high"}
    assert (
        await offline.client.patch(offline.base, json={"reasoning_override": explicit})
    ).status_code == 200
    cleared = await offline.client.patch(offline.base, json={"reasoning_override": None})
    assert cleared.json()["reasoning_override"] is None
    assert (await offline.client.get(url)).json()["effort"] is None


@pytest.mark.asyncio
async def test_patch_session_edits_title_and_model_override(offline):
    unauth = await offline.client.patch(
        offline.base, json={"title": "Blocked"}, headers={"Authorization": ""}
    )
    assert unauth.status_code == 401

    assert (await offline.client.patch(offline.base, json={})).status_code == 422
    assert (await offline.client.patch(offline.base, json={"title": None})).status_code == 422
    assert (await offline.client.patch(offline.base, json={"title": "   "})).status_code == 422
    assert (await offline.client.patch(offline.base, json={"title": "x" * 201})).status_code == 422
    assert (
        await offline.client.patch(f"/api/sessions/{uuid4()}", json={"title": "Missing"})
    ).status_code == 404

    updated = await offline.client.patch(
        offline.base,
        json={"title": "  Quarterly analysis  ", "model_override": "  openai/gpt-4.1  "},
    )
    assert updated.status_code == 200
    body = updated.json()
    assert set(body) == {
        "id",
        "title",
        "model_override",
        "reasoning_override",
        "daytona_sandbox_id",
        "deleting",
        "created_at",
        "updated_at",
    }
    assert body["id"] == str(offline.store.session_id)
    assert body["title"] == "Quarterly analysis"
    assert body["model_override"] == "openai/gpt-4.1"
    assert body["daytona_sandbox_id"] == "test-sandbox"

    cleared_null = await offline.client.patch(offline.base, json={"model_override": None})
    assert cleared_null.status_code == 200
    assert cleared_null.json()["model_override"] is None
    assert cleared_null.json()["title"] == "Quarterly analysis"

    re_set = await offline.client.patch(offline.base, json={"model_override": "anthropic/claude"})
    assert re_set.status_code == 200
    assert re_set.json()["model_override"] == "anthropic/claude"

    missing = await offline.client.patch(
        offline.base, json={"model_override": f"provider/{uuid4()}/gpt-4.1"}
    )
    assert missing.status_code == 409
    assert missing.json()["detail"] == "The selected provider no longer exists"
    assert offline.store.session.model_override == "anthropic/claude"

    cleared_empty = await offline.client.patch(offline.base, json={"model_override": "   "})
    assert cleared_empty.status_code == 200
    assert cleared_empty.json()["model_override"] is None


@pytest.mark.asyncio
async def test_workspace_settings_crud_validation_and_secret_safety(offline):
    for method in ("GET", "PATCH", "DELETE"):
        kwargs = {"json": {"rlm_max_iters": 12}} if method == "PATCH" else {}
        response = await offline.client.request(
            method, "/api/settings", headers={"Authorization": ""}, **kwargs
        )
        assert response.status_code == 401

    initial = await offline.client.get("/api/settings")
    assert initial.status_code == 200
    data = initial.json()
    raw_text = json.dumps(data)
    for forbidden_value in ("offline-token", "postgresql://"):
        assert forbidden_value not in raw_text
    for forbidden_key in (
        "database_url",
        "database_direct_url",
        "daytona_api_key",
        "llm_api_key",
        "api_bearer_token",
    ):
        assert f'"{forbidden_key}"' not in raw_text
    assert data["overrides"] == {}
    assert data["effective"] == data["defaults"]
    assert data["status"] == {
        "database_configured": True,
        "daytona_configured": True,
        "daytona_image": "offline",
        "daytona_region": None,
        "daytona_idle_timeout_minutes": 15,
        "daytona_max_lifetime_minutes": 480,
        "storage_bucket": "fleet-workspaces",
        "llm_api_key_configured": True,
        "llm_base_url": None,
        "bearer_auth_enabled": True,
        "max_concurrent_turns_per_worker": 16,
        "mlflow_tracking_uri": None,
        "mlflow_experiment": "fleet-rlm",
        "optimized_program_path": None,
        "skill_selector_path": None,
        "memory_gate_path": None,
        "skills_count": 1,
    }
    assert set(data["effective"]) == {
        "llm_model",
        "sub_llm_model",
        "decision_model",
        "rlm_max_iters",
        "rlm_max_llm_calls",
        "rlm_max_depth",
        "rlm_max_output_chars",
        "history_max_turns",
        "history_max_chars",
        "daytona_exec_timeout_seconds",
    }

    for invalid_payload in (
        {"llm_api_key": "injected"},
        {"daytona_api_key": "injected"},
        {"database_url": "postgresql://attacker/db"},
        {"api_bearer_token": "override"},
        {"llm_base_url": "https://attacker.example/v1"},
        {"daytona_image": "custom-image"},
        {"mlflow_experiment": "other-experiment"},
        {"max_concurrent_turns_per_worker": 4},
        {"rlm_max_iters": 0},
        {"rlm_max_iters": 101},
        {"rlm_max_llm_calls": 0},
        {"rlm_max_llm_calls": 501},
        {"rlm_max_depth": 0},
        {"rlm_max_depth": 8},
        {"rlm_max_output_chars": 499},
        {"rlm_max_output_chars": 200_001},
        {"history_max_turns": -1},
        {"history_max_turns": 101},
        {"history_max_chars": 999},
        {"history_max_chars": 200_001},
        {"daytona_idle_timeout_minutes": 60},
        {"s3_secret_access_key": "injected"},
        {"daytona_exec_timeout_seconds": 4},
        {"daytona_exec_timeout_seconds": 601},
        {"llm_model": "   "},
        {"sub_llm_model": ""},
        {"decision_model": "   "},
    ):
        rejected = await offline.client.patch("/api/settings", json=invalid_payload)
        assert rejected.status_code == 422, invalid_payload

    # Legacy persisted read-only overrides in Neon are ignored cleanly.
    offline.store.workspace_overrides.update(
        {
            "llm_base_url": "https://legacy.example/v1",
            "daytona_image": "injected-image",
            "mlflow_experiment": "legacy-experiment",
        }
    )
    sanitized = await offline.client.get("/api/settings")
    assert sanitized.status_code == 200
    assert sanitized.json()["overrides"] == {}
    assert sanitized.json()["status"]["llm_base_url"] is None
    assert sanitized.json()["status"]["daytona_image"] == "offline"
    assert sanitized.json()["status"]["mlflow_experiment"] == "fleet-rlm"

    patched = await offline.client.patch(
        "/api/settings",
        json={
            "llm_model": "openai/gpt-4.1",
            "rlm_max_iters": 12,
            "rlm_max_depth": 7,
            "history_max_turns": 0,
            "daytona_exec_timeout_seconds": 45,
        },
    )
    assert patched.status_code == 200
    body = patched.json()
    assert body["overrides"] == {
        "llm_model": "openai/gpt-4.1",
        "rlm_max_iters": 12,
        "rlm_max_depth": 7,
        "history_max_turns": 0,
        "daytona_exec_timeout_seconds": 45,
    }
    assert body["effective"]["llm_model"] == "openai/gpt-4.1"
    assert body["effective"]["rlm_max_iters"] == 12
    assert body["effective"]["rlm_max_depth"] == 7
    assert body["effective"]["history_max_turns"] == 0
    assert body["effective"]["daytona_exec_timeout_seconds"] == 45
    assert body["defaults"]["llm_model"] == "openai/gpt-4o-mini"
    assert body["defaults"]["rlm_max_iters"] == 20
    assert body["defaults"]["rlm_max_depth"] == 4

    cleared = await offline.client.patch(
        "/api/settings",
        json={"rlm_max_iters": None},
    )
    assert cleared.status_code == 200
    cleared_body = cleared.json()
    assert cleared_body["overrides"] == {
        "llm_model": "openai/gpt-4.1",
        "rlm_max_depth": 7,
        "history_max_turns": 0,
        "daytona_exec_timeout_seconds": 45,
    }
    assert cleared_body["effective"]["rlm_max_iters"] == 20

    reset = await offline.client.delete("/api/settings")
    assert reset.status_code == 200
    reset_body = reset.json()
    assert reset_body["overrides"] == {}
    assert reset_body["effective"] == reset_body["defaults"]


@pytest.mark.asyncio
@pytest.mark.parametrize("session_model", [None, "openai/provider/catalog-model"])
async def test_run_turn_and_files_apply_neon_settings_overrides(
    offline, monkeypatch, session_model
):
    runtime = api.app.state.runtime
    startup_lm = runtime.lm
    startup_sub_lm = runtime.sub_lm
    startup_decision_lm = runtime.decision_lm

    created_models: list[tuple[str, str | None]] = []
    created_decision_models: list[str] = []

    def fake_model(settings, name):
        created_models.append((name, settings.llm_base_url))
        return DummyLM([{"plan": "Override", "code": "SUBMIT('Done', [])"}])

    def fake_decision_lm(name):
        created_decision_models.append(name)
        return DummyLM([])

    monkeypatch.setattr(agent, "_model", fake_model)
    monkeypatch.setattr(dspy, "LM", fake_decision_lm)

    patch_resp = await offline.client.patch(
        "/api/settings",
        json={
            "llm_model": "openai/gpt-4.1",
            "sub_llm_model": "openai/gpt-4.1-mini",
            "decision_model": "typesafe:jev-2026-01",
            "rlm_max_iters": 7,
            "daytona_exec_timeout_seconds": 33,
        },
    )
    assert patch_resp.status_code == 200
    if session_model:
        saved = await offline.client.patch(offline.base, json={"model_override": session_model})
        assert saved.status_code == 200

    observed: dict[str, object] = {}

    async def capture_turn(settings, *, model_override, sub_lm, optimized_state, run_id, emit, **_):
        observed["settings"] = settings
        observed["model_override"] = model_override
        observed["sub_lm"] = sub_lm
        observed["optimized_state"] = optimized_state
        await emit(run_id, agent.RunStartedEvent("workspace"))
        await emit(run_id, agent.RunResultEvent("Overridden"))
        return dspy.Prediction(answer="Overridden", history=dspy.History(messages=[]))

    monkeypatch.setattr(agent, "run_workspace_task", capture_turn)

    response = await offline.client.post(f"{offline.base}/chat", json=_PAYLOAD)
    assert response.status_code == 200
    assert "Overridden" in response.text

    effective = observed["settings"]
    assert isinstance(effective, Settings)
    assert effective.llm_model == "openai/gpt-4.1"
    assert effective.sub_llm_model == "openai/gpt-4.1-mini"
    assert effective.decision_model == "typesafe:jev-2026-01"
    assert effective.llm_base_url is None
    assert effective.rlm_max_iters == 7
    assert effective.daytona_exec_timeout_seconds == 33
    if session_model:
        assert observed["model_override"] == session_model
        assert created_models == [("openai/gpt-4.1-mini", None)]
    else:
        assert observed["model_override"] is not startup_lm
        assert created_models == [("openai/gpt-4.1", None), ("openai/gpt-4.1-mini", None)]
    assert observed["sub_lm"] is not startup_sub_lm
    assert observed["optimized_state"] is None
    assert created_decision_models == ["typesafe:jev-2026-01", "typesafe:jev-2026-01"]
    assert offline.memory.await_args.args[0].decision_model == "typesafe:jev-2026-01"
    assert offline.memory.await_args.kwargs["decision_lm"] is not startup_decision_lm
    # Startup runtime remains immutable across requests.
    assert runtime.settings.rlm_max_iters == 20
    assert runtime.lm is startup_lm
    assert runtime.sub_lm is startup_sub_lm
    assert runtime.decision_lm is startup_decision_lm

    file_path = "artifacts/report.txt"
    download = await offline.client.get(f"{offline.base}/files/{file_path}")
    assert download.status_code == 200
    assert download.content == b"report"
    assert offline.pull.await_args.kwargs["timeout"] == 33


@pytest.mark.asyncio
async def test_transcript_pages_exclude_running_roots_and_keep_turns_together(offline, monkeypatch):
    # Exercise the projected query and exclusive cursor against an in-memory run table.
    engine = create_engine("sqlite://")
    session_id = offline.store.session_id.hex
    other_session = uuid4().hex
    assistant = json.dumps([{"type": "text", "text": "Saved reply"}])
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE runs (id CHAR(32), session_id CHAR(32), parent_run_id CHAR(32),"
            " status TEXT, prompt TEXT, ui_parts JSON)"
        )

        def insert_run(number, status, prompt, *, owner=session_id, parent=None, parts="[]"):
            connection.exec_driver_sql(
                "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?)",
                (UUID(int=number).hex, owner, parent, status, prompt, parts),
            )

        insert_run(1, "completed", "Oldest", parts=assistant)
        insert_run(2, "running", "Still running")
        insert_run(3, "cancelled", "Cancelled")
        insert_run(4, "completed", "Latest", parts=assistant)
        insert_run(5, "completed", "Other session", owner=other_session)
        insert_run(6, "completed", "Child turn", parent=UUID(int=4).hex)

        @asynccontextmanager
        async def sessionmaker():
            yield SimpleNamespace(execute=AsyncMock(side_effect=connection.execute))

        runtime = api.app.state.runtime
        api.app.state.runtime = replace(runtime, sessionmaker=sessionmaker)
        monkeypatch.setattr(db, "get_session_ui_messages", _read_all_messages)
        path = f"{offline.base}/messages"
        first = await offline.client.get(path, params={"limit": 2})
        first.raise_for_status()
        assert [message["parts"][0]["text"] for message in first.json()] == [
            "Cancelled",
            "Latest",
            "Saved reply",
        ]
        assert str(UUID(int=3)) in first.headers["Link"]
        assert 'rel="prev"' in first.headers["Link"]

        insert_run(7, "completed", "New turn")
        older = await offline.client.get(path, params={"before": str(UUID(int=3))})
        older.raise_for_status()
        assert [message["parts"][0]["text"] for message in older.json()] == [
            "Oldest",
            "Saved reply",
        ]
        assert "Link" not in older.headers
        full = await offline.client.get(path)
        assert [message["parts"][0]["text"] for message in full.json()] == [
            "Oldest",
            "Saved reply",
            "Cancelled",
            "Latest",
            "Saved reply",
            "New turn",
        ]
        for params in ({"limit": 51}, {"limit": 0}, {"before": "malformed"}):
            assert (await offline.client.get(path, params=params)).status_code == 422
    engine.dispose()


@pytest.mark.asyncio
async def test_sessions_crud_messages_files_and_limit(offline):
    assert (
        await offline.client.post("/api/sessions", headers={"Authorization": ""})
    ).status_code == 401
    assert (await offline.client.post("/api/sessions", json={"title": ""})).status_code == 422

    created_default = await offline.client.post("/api/sessions")
    assert created_default.status_code == 201
    default_body = created_default.json()
    assert default_body["title"] == "New session"
    assert default_body["daytona_sandbox_id"] is None

    created_custom = await offline.client.post("/api/sessions", json={"title": "Q4 Report"})
    assert created_custom.status_code == 201
    custom_id = created_custom.json()["id"]
    assert created_custom.json()["title"] == "Q4 Report"

    listed_all = await offline.client.get("/api/sessions")
    assert listed_all.status_code == 200
    assert len(listed_all.json()) == 3

    listed_limited = await offline.client.get("/api/sessions", params={"limit": 2})
    assert listed_limited.status_code == 200
    assert len(listed_limited.json()) == 2
    assert (await offline.client.get("/api/sessions", params={"limit": 0})).status_code == 422
    assert (await offline.client.get("/api/sessions", params={"limit": 501})).status_code == 422
    original = next(
        item for item in listed_all.json() if item["id"] == str(offline.store.session_id)
    )
    assert all(item["id"] != original["id"] for item in listed_limited.json())
    assert (await offline.client.get(offline.base)).json() == original
    assert (await offline.client.get(f"/api/sessions/{uuid4()}")).status_code == 404

    # Messages endpoint: 404 for unknown session, 200 returns persisted UI messages.
    assert (await offline.client.get(f"/api/sessions/{uuid4()}/messages")).status_code == 404
    expected_messages = [
        {"id": "r1:user", "role": "user", "parts": [{"type": "text", "text": "Hi"}]},
        {"id": "r1", "role": "assistant", "parts": [{"type": "text", "text": "Hello"}]},
    ]
    offline.store.ui_messages_by_session[offline.store.session_id] = expected_messages
    msgs_resp = await offline.client.get(f"{offline.base}/messages")
    assert msgs_resp.status_code == 200
    assert msgs_resp.json() == expected_messages
    assert offline.store.checkouts == 0

    # Files endpoint: 404 for unknown session, sorted FileView list for existing session.
    assert (await offline.client.get(f"/api/sessions/{uuid4()}/files")).status_code == 404
    offline.store.add(
        db.File(
            session_id=offline.store.session_id,
            path="files/z.csv",
            sha256="1" * 64,
            size_bytes=10,
            kind="input",
        )
    )
    offline.store.add(
        db.File(
            session_id=offline.store.session_id,
            path="artifacts/a.txt",
            sha256="2" * 64,
            size_bytes=20,
            kind="artifact",
        )
    )
    files_resp = await offline.client.get(f"{offline.base}/files")
    assert files_resp.status_code == 200
    assert [item["path"] for item in files_resp.json()] == ["artifacts/a.txt", "files/z.csv"]

    # Delete session: 404 unknown, 409 active root, 204 deletes DB + Daytona sandbox.
    assert (await offline.client.delete(f"/api/sessions/{uuid4()}")).status_code == 404
    offline.store.active = uuid4()
    assert (await offline.client.delete(offline.base)).status_code == 409
    offline.store.active = None

    deleted_resp = await offline.client.delete(offline.base)
    assert deleted_resp.status_code == 204
    assert offline.store.session_id not in offline.store.sessions
    offline.daytona.v2.get.assert_not_awaited()
    offline.daytona.v2.delete.assert_awaited_once_with("test-sandbox", timeout=30)
    assert offline.store.checkouts == 0

    # A missing VM permits cleanup. A provider failure leaves a retryable tombstone.
    custom_uuid = UUID(custom_id)
    offline.store.sessions[custom_uuid].daytona_sandbox_id = "remote-missing"
    offline.daytona.v2.delete.side_effect = NotFoundError("VM already deleted")
    assert (await offline.client.delete(f"/api/sessions/{custom_id}")).status_code == 204
    assert custom_uuid not in offline.store.sessions

    default_uuid = UUID(default_body["id"])
    offline.store.sessions[default_uuid].daytona_sandbox_id = "remote-error"
    offline.daytona.v2.delete.side_effect = DaytonaError("Gateway down", status_code=503)
    incomplete = await offline.client.delete(f"/api/sessions/{default_uuid}")
    assert incomplete.status_code == 503
    assert offline.store.sessions[default_uuid].deleting
    session_path = f"/api/sessions/{default_uuid}"
    listed = await offline.client.get("/api/sessions")
    assert any(item["id"] == str(default_uuid) and item["deleting"] for item in listed.json())
    detail = await offline.client.get(session_path)
    assert detail.status_code == 200 and detail.json()["deleting"]
    files = await offline.client.get(session_path + "/files")
    assert files.status_code == 404
    offline.daytona.v2.delete.side_effect = None
    retried = await offline.client.delete(session_path)
    assert retried.status_code == 204
    assert default_uuid not in offline.store.sessions


@pytest.mark.asyncio
async def test_memories_crud_filters_and_limit(offline):
    assert (
        await offline.client.get("/api/memories", headers={"Authorization": ""})
    ).status_code == 401

    other_session = uuid4()
    m1 = db.Memory(
        id=UUID("00000000-0000-0000-0000-000000000001"),
        session_id=offline.store.session_id,
        content="Session unapproved fact",
        approved=False,
    )
    m2 = db.Memory(
        id=UUID("00000000-0000-0000-0000-000000000002"),
        session_id=offline.store.session_id,
        content="Session approved fact",
        approved=True,
    )
    m3 = db.Memory(
        id=UUID("00000000-0000-0000-0000-000000000003"),
        session_id=None,
        content="Global approved fact",
        approved=True,
    )
    m4 = db.Memory(
        id=UUID("00000000-0000-0000-0000-000000000004"),
        session_id=other_session,
        content="Other session fact",
        approved=False,
    )
    for memory in (m1, m2, m3, m4):
        offline.store.add(memory)

    all_resp = await offline.client.get("/api/memories")
    assert all_resp.status_code == 200
    assert [item["id"] for item in all_resp.json()] == [
        str(m4.id),
        str(m3.id),
        str(m2.id),
        str(m1.id),
    ]

    limited = await offline.client.get("/api/memories", params={"limit": 2})
    assert limited.status_code == 200
    assert [item["id"] for item in limited.json()] == [str(m4.id), str(m3.id)]
    assert (await offline.client.get("/api/memories", params={"limit": 0})).status_code == 422
    assert (await offline.client.get("/api/memories", params={"limit": 501})).status_code == 422

    by_session = await offline.client.get(
        "/api/memories", params={"session_id": str(offline.store.session_id)}
    )
    assert by_session.status_code == 200
    assert [item["id"] for item in by_session.json()] == [str(m2.id), str(m1.id)]

    only_approved = await offline.client.get("/api/memories", params={"approved": "true"})
    assert only_approved.status_code == 200
    assert [item["id"] for item in only_approved.json()] == [str(m3.id), str(m2.id)]

    combined = await offline.client.get(
        "/api/memories",
        params={"session_id": str(offline.store.session_id), "approved": "false"},
    )
    assert combined.status_code == 200
    assert [item["id"] for item in combined.json()] == [str(m1.id)]

    # PATCH /api/memories/{id}
    assert (await offline.client.patch(f"/api/memories/{m1.id}", json={})).status_code == 422
    assert (
        await offline.client.patch(f"/api/memories/{uuid4()}", json={"approved": True})
    ).status_code == 404
    patched = await offline.client.patch(
        f"/api/memories/{m1.id}",
        json={"content": "Updated fact", "approved": True},
    )
    assert patched.status_code == 200
    assert patched.json()["content"] == "Updated fact"
    assert patched.json()["approved"] is True

    # DELETE /api/memories/{id}
    assert (await offline.client.delete(f"/api/memories/{uuid4()}")).status_code == 404
    deleted = await offline.client.delete(f"/api/memories/{m1.id}")
    assert deleted.status_code == 204
    assert m1.id not in offline.store.memories


@pytest.mark.asyncio
async def test_zero_db_checkouts_across_storage_and_sandbox_bind_races(offline, monkeypatch):
    session = (await offline.client.post("/api/sessions", json={"title": "Race"})).json()
    sid = UUID(session["id"])
    base = f"/api/sessions/{sid}"
    response = await offline.client.post(
        base + "/files", files={"file": ("sales.csv", b"a,b\n1,2\n")}
    )
    assert response.status_code == 201
    lean_download = await offline.client.get(base + "/files/files/sales.csv")
    assert lean_download.content == b"a,b\n1,2\n"
    assert offline.store.sessions[sid].daytona_sandbox_id is None
    offline.daytona.v2.get.assert_not_awaited()
    offline.storage.put.assert_awaited_once()
    offline.storage.download.assert_awaited_once()

    # A later owner uses the same create identity after an uncertain reply.
    recovered = SimpleNamespace(id="recovered-sandbox", state="started")
    session = offline.store.sessions[sid]

    async def allocate(_daytona, _settings, name, *, recovering):
        assert offline.store.checkouts == 0
        assert name == session.pending_sandbox_name
        if not recovering:
            raise interpreter.SandboxStartupError("create response unavailable")
        return recovered

    monkeypatch.setattr(
        workspace_routes, "allocate_session_sandbox", AsyncMock(side_effect=allocate)
    )
    monkeypatch.setattr(workspace_routes, "prepare_sandbox", AsyncMock())
    first_id = offline.store.active_by_session[sid] = uuid4()
    with pytest.raises(interpreter.SandboxStartupError, match="create response"):
        await chat_routes._chat_sandbox(
            api.app.state.runtime, session, offline.settings, owner_run_id=first_id
        )
    pending = session.pending_sandbox_name
    replacement_id = offline.store.active_by_session[sid] = uuid4()
    sandbox = await chat_routes._chat_sandbox(
        api.app.state.runtime, session, offline.settings, owner_run_id=replacement_id
    )
    assert sandbox is recovered and pending == f"fleet-{first_id.hex}"
    assert session.pending_sandbox_name is None and session.daytona_sandbox_id == recovered.id
    offline.store.active_by_session.pop(sid)
    assert offline.store.checkouts == 0

    # An upload lease fences a concurrent delete and turn, then releases on completion.
    putting = asyncio.Event()
    proceed = asyncio.Event()
    put = offline.storage.put

    async def hold_put(key, local, *, register):
        putting.set()
        await proceed.wait()
        return await put(key, local, register=register)

    monkeypatch.setattr(offline.storage, "put", hold_put)
    uploading = asyncio.create_task(
        offline.client.post(base + "/files", files={"file": ("next.csv", b"b\n2\n")})
    )
    try:
        await asyncio.wait_for(putting.wait(), 2)
        blocked = await offline.client.delete(base)
        assert blocked.status_code == 409
        assert (await offline.client.post(base + "/chat", json=_PAYLOAD)).status_code == 409
        assert offline.store.checkouts == 0
    finally:
        proceed.set()
    assert (await uploading).status_code == 201
    deleted = await offline.client.delete(base)
    assert deleted.status_code == 204
    assert not any(key.startswith(offline.storage.prefix(sid)) for key in offline.storage.objects)


@pytest.mark.asyncio
async def test_lazy_stale_run_reaping_on_turn_conflict(offline, monkeypatch):
    # Case A: active non-stale turn -> reap returns 0 -> HTTP 409.
    offline.store.active = uuid4()
    offline.reap.reset_mock()
    offline.reap.return_value = 0
    offline.reap.side_effect = None
    blocked = await offline.client.post(f"{offline.base}/chat", json=_PAYLOAD)
    assert blocked.status_code == 409
    offline.reap.assert_awaited_once_with(
        offline.store,
        offline.settings.stale_run_timeout_seconds,
        session_id=offline.store.session_id,
    )

    # Case B: stale turn -> reap clears it and returns 1 -> retry admits turn (HTTP 200).
    async def reap_stale(_sessionmaker, _timeout, *, session_id=None):
        assert session_id == offline.store.session_id
        offline.store.active = None
        return 1

    async def quick_turn(_settings, *, run_id, emit, **_):
        await emit(run_id, agent.RunStartedEvent("workspace"))
        await emit(run_id, agent.RunResultEvent("Recovered"))
        return dspy.Prediction(answer="Recovered", history=dspy.History(messages=[]))

    offline.reap.reset_mock()
    offline.reap.side_effect = reap_stale
    monkeypatch.setattr(agent, "run_workspace_task", quick_turn)

    admitted = await offline.client.post(f"{offline.base}/chat", json=_PAYLOAD)
    assert admitted.status_code == 200
    assert "Recovered" in admitted.text
    offline.reap.assert_awaited_once_with(
        offline.store,
        offline.settings.stale_run_timeout_seconds,
        session_id=offline.store.session_id,
    )

    # Case C: reap reports > 0, but another concurrent turn grabs the lock before retry -> 409.
    offline.store.active = uuid4()
    offline.reap.reset_mock()
    offline.reap.side_effect = None
    offline.reap.return_value = 1
    still_blocked = await offline.client.post(f"{offline.base}/chat", json=_PAYLOAD)
    assert still_blocked.status_code == 409


@pytest.mark.asyncio
async def test_max_concurrent_turns_per_worker_enforced_by_turn_semaphore(offline, monkeypatch):
    # Verify lifespan sizes turn_semaphore to settings.max_concurrent_turns_per_worker.
    runtime = api.app.state.runtime
    assert runtime.turn_semaphore._value == offline.settings.max_concurrent_turns_per_worker

    # Constrain concurrency to 1 and run two turns on different sessions concurrently.
    api.app.state.runtime = replace(runtime, turn_semaphore=asyncio.Semaphore(1))
    other = (await offline.client.post("/api/sessions", json={"title": "Session B"})).json()
    other_id = other["id"]
    offline.store.sessions[UUID(other_id)].daytona_sandbox_id = "test-sandbox"

    turn1_entered = asyncio.Event()
    release_turn1 = asyncio.Event()
    turn2_entered = asyncio.Event()
    events_order: list[str] = []

    async def controlled_turn(_settings, *, task, run_id, emit, **_):
        await emit(run_id, agent.RunStartedEvent("workspace"))
        if "First" in task:
            events_order.append("turn1:start")
            turn1_entered.set()
            await release_turn1.wait()
            events_order.append("turn1:end")
            answer = "First done"
        else:
            events_order.append("turn2:start")
            turn2_entered.set()
            answer = "Second done"
        await emit(run_id, agent.RunResultEvent(answer))
        return dspy.Prediction(answer=answer, history=dspy.History(messages=[]))

    monkeypatch.setattr(agent, "run_workspace_task", controlled_turn)

    payload1 = {"messages": [{"role": "user", "parts": [{"type": "text", "text": "First @table"}]}]}
    payload2 = {
        "messages": [{"role": "user", "parts": [{"type": "text", "text": "Second @table"}]}]
    }

    task1 = asyncio.create_task(offline.client.post(f"{offline.base}/chat", json=payload1))
    await asyncio.wait_for(turn1_entered.wait(), timeout=2)

    task2 = asyncio.create_task(
        offline.client.post(f"/api/sessions/{other_id}/chat", json=payload2)
    )
    await asyncio.sleep(0.05)
    assert not turn2_entered.is_set()
    assert events_order == ["turn1:start"]

    release_turn1.set()
    resp1, resp2 = await asyncio.wait_for(asyncio.gather(task1, task2), timeout=3)
    assert resp1.status_code == 200 and "First done" in resp1.text
    assert resp2.status_code == 200 and "Second done" in resp2.text
    assert events_order == ["turn1:start", "turn1:end", "turn2:start"]


@pytest.mark.asyncio
@pytest.mark.parametrize("slot_available_at_disconnect", [False, True])
async def test_queued_client_disconnect_never_admits_root(
    offline, monkeypatch, slot_available_at_disconnect
):
    disconnected = asyncio.Event()
    queued = asyncio.Event()

    class QueueProbe(asyncio.Semaphore):
        async def acquire(self):
            queued.set()
            return await super().acquire()

    semaphore = QueueProbe(0)
    api.app.state.runtime = replace(api.app.state.runtime, turn_semaphore=semaphore)
    admit = AsyncMock()
    monkeypatch.setattr(db, "acquire_root_run", admit)
    sent = []
    request_sent = False

    async def receive():
        nonlocal request_sent
        if not request_sent:
            request_sent = True
            return {
                "type": "http.request",
                "body": json.dumps(_PAYLOAD).encode(),
                "more_body": False,
            }
        await disconnected.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "method": "POST",
        "scheme": "https",
        "path": f"{offline.base}/chat",
        "root_path": "",
        "query_string": b"",
        "headers": [
            (b"authorization", b"Bearer offline-token"),
            (b"content-type", b"application/json"),
        ],
    }
    request = asyncio.create_task(api.app(scope, receive, send))
    try:
        await asyncio.wait_for(queued.wait(), timeout=2)
        if slot_available_at_disconnect:
            semaphore.release()
    finally:
        disconnected.set()
        await asyncio.wait_for(request, timeout=2)
    assert sent[0]["status"] == 499
    assert semaphore._value == int(slot_available_at_disconnect)
    admit.assert_not_awaited()
    offline.finish.assert_not_awaited()


@pytest.mark.asyncio
async def test_chat_admission_timeout_leaves_root_uncreated(offline, monkeypatch):
    semaphore = asyncio.Semaphore(0)
    api.app.state.runtime = replace(api.app.state.runtime, turn_semaphore=semaphore)
    monkeypatch.setattr(chat_routes, "_CHAT_ADMISSION_SECONDS", 0.02)
    admit = AsyncMock()
    monkeypatch.setattr(db, "acquire_root_run", admit)

    response = await offline.client.post(f"{offline.base}/chat", json=_PAYLOAD)
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "1"
    assert semaphore._value == 0
    admit.assert_not_awaited()
    offline.finish.assert_not_awaited()


@pytest.mark.asyncio
async def test_chat_capacity_is_free_while_memory_is_still_running(offline, monkeypatch):
    runtime = replace(api.app.state.runtime, turn_semaphore=asyncio.Semaphore(1))
    api.app.state.runtime = runtime
    consolidating = asyncio.Event()
    finish_memory = asyncio.Event()

    async def controlled_turn(_settings, *, task, run_id, emit, **_):
        await emit(run_id, agent.RunStartedEvent("workspace"))
        return dspy.Prediction(answer=f"Completed {task}", history=dspy.History(messages=[]))

    async def controlled_memory(_settings, prompt, *_args, **_kwargs):
        if prompt.startswith("First"):
            consolidating.set()
            await finish_memory.wait()

    def payload(prompt):
        return {"messages": [{"role": "user", "parts": [{"type": "text", "text": prompt}]}]}

    monkeypatch.setattr(agent, "run_workspace_task", controlled_turn)
    monkeypatch.setattr(agent, "consolidate_memory", controlled_memory)
    first = asyncio.create_task(offline.client.post(f"{offline.base}/chat", json=payload("First")))
    try:
        await asyncio.wait_for(consolidating.wait(), timeout=2)
        assert offline.store.active is None
        second = await asyncio.wait_for(
            offline.client.post(f"{offline.base}/chat", json=payload("Second")), timeout=2
        )
        assert second.status_code == 200 and "Completed Second" in second.text
        assert not first.done()
    finally:
        finish_memory.set()
        completed = await asyncio.wait_for(first, timeout=2)
    assert completed.status_code == 200 and "Completed First" in completed.text
    assert runtime.turn_semaphore._value == 1
    assert runtime.memory_semaphore._value == min(
        4, runtime.settings.max_concurrent_turns_per_worker
    )


@pytest.mark.asyncio
async def test_pinned_skills_merge_decision_lm_reuse_and_overwritten_artifacts(
    offline, monkeypatch, tmp_path
):
    runtime = api.app.state.runtime
    charts_skill = SkillDefinition(
        name="charts",
        description="Plot charts",
        body="Use matplotlib.",
        path=tmp_path / "charts" / "SKILL.md",
    )
    api.app.state.runtime = replace(
        runtime,
        skills=SkillRegistry(
            tuple(sorted((*runtime.skills.skills, charts_skill), key=lambda s: s.name))
        ),
    )

    # Pre-populate existing files to test hydration and overwrite detection.
    prior_input = offline.storage.seed(offline.store, "files/input.csv", b"old input", kind="input")
    prior_artifact = offline.storage.seed(offline.store, "artifacts/report.txt", b"old report")
    before = {
        record.path: (record.sha256, record.size_bytes) for record in (prior_input, prior_artifact)
    }
    new_files = {"artifacts/report.txt": b"new report", "artifacts/summary.json": b'{"rows":2}'}
    offline.remote_files.update(new_files)

    observed: dict[str, object] = {}
    real_select_skills = chat_routes.select_skills

    def spy_select_skills(settings, registry, task, pinned_skills=None, **kwargs):
        observed["select_decision_lm"] = kwargs.get("decision_lm")
        observed["pinned_skills"] = list(pinned_skills or [])
        return real_select_skills(settings, registry, task, pinned_skills, **kwargs)

    async def spy_list_artifacts(_sandbox, known):
        observed["known_files"] = dict(known)
        return [
            {
                "path": "/workspace/" + path,
                "sha256": hashlib.sha256(body).hexdigest(),
                "size_bytes": len(body),
            }
            for path, body in new_files.items()
        ]

    async def fake_turn(_settings, *, selection, run_id, emit, **_):
        observed["active_skills"] = [skill.name for skill in selection.active_skills]
        await emit(run_id, agent.RunStartedEvent("workspace"))
        await emit(run_id, agent.RunResultEvent("Updated artifacts"))
        return dspy.Prediction(answer="Updated artifacts", history=dspy.History(messages=[]))

    def fail_new_lm(*_args, **_kwargs):
        pytest.fail("Unchanged decision_model must reuse runtime.decision_lm without dspy.LM()")

    monkeypatch.setattr(chat_routes, "select_skills", spy_select_skills)
    monkeypatch.setattr(chat_routes, "list_new_artifacts", spy_list_artifacts)
    monkeypatch.setattr(agent, "run_workspace_task", fake_turn)
    monkeypatch.setattr(dspy, "LM", fail_new_lm)

    payload = {
        "messages": [
            {"role": "user", "parts": [{"type": "text", "text": "Analyze @table and @charts"}]}
        ],
        "pinned_skills": ["@charts"],
    }
    response = await offline.client.post(f"{offline.base}/chat", json=payload)
    assert response.status_code == 200

    # @skill mentions merged with pinned_skills, deduplicated and order-preserving.
    assert observed["pinned_skills"] == ["charts", "table"]
    assert observed["active_skills"] == ["charts", "table"]

    # Both select_skills and consolidate_memory reuse runtime.decision_lm directly.
    assert observed["select_decision_lm"] is runtime.decision_lm
    assert offline.memory.await_args.kwargs["decision_lm"] is runtime.decision_lm

    # list_new_artifacts received {path: (sha256, size_bytes)} for overwrite detection.
    assert observed["known_files"] == before

    # Final data-file-tree replaces existing artifacts/report.txt without duplicating it.
    chunks = response.text.strip().split("\n\n")
    frames = [
        json.loads(next(line[6:] for line in chunk.splitlines() if line.startswith("data: ")))
        for chunk in chunks[:-1]
    ]
    file_tree_frames = [frame for frame in frames if frame["type"] == "data-file-tree"]
    assert len(file_tree_frames) == 2
    final_files = file_tree_frames[-1]["data"]["files"]
    assert [entry["path"] for entry in final_files] == [
        "artifacts/report.txt",
        "artifacts/summary.json",
        "files/input.csv",
    ]
    assert final_files[0]["sha256"] == hashlib.sha256(new_files["artifacts/report.txt"]).hexdigest()
    assert final_files[0]["size_bytes"] == len(new_files["artifacts/report.txt"])
    assert [item["path"] for item in offline.finish.await_args.kwargs["files"]] == list(new_files)
    assert (
        offline.store.files[(offline.store.session_id, "artifacts/report.txt")].sha256
        == final_files[0]["sha256"]
    )


@pytest.mark.asyncio
async def test_optimized_program_state_cached_once_at_startup(monkeypatch, tmp_path):
    skill = tmp_path / "table"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: table\ndescription: Analyze tabular data\n---\nInspect the data first.\n"
    )
    model = DummyLM([{"plan": "Analyze", "code": "SUBMIT('Cached', [])"}])
    base_settings = Settings(
        _env_file=None,
        database_url="postgresql://localhost/unused",
        database_direct_url="postgresql://localhost/unused",
        daytona_api_key="offline",
        daytona_image="offline",
        llm_model="openai/gpt-4o-mini",
        sub_llm_model="openai/gpt-4o-mini",
        llm_api_key="offline",
        mlflow_tracking_uri=None,
        api_bearer_token="offline-token",
        skills_dir=tmp_path,
    )
    program = agent.build_program(base_settings, [], sub_lm=model)
    artifact_path = tmp_path / "rlm_program.json"
    program.save(artifact_path)

    store = OfflineStore()
    engine = SimpleNamespace(dispose=AsyncMock())
    sandbox = SimpleNamespace(id="test-sandbox", state="started", fs=SimpleNamespace())
    daytona = SimpleNamespace(
        v2=SimpleNamespace(get=AsyncMock(return_value=sandbox), delete=AsyncMock())
    )

    @asynccontextmanager
    async def daytona_client(_settings):
        yield daytona

    monkeypatch.setattr(api, "make_daytona_client", daytona_client)
    monkeypatch.setattr(api, "ObjectStorage", OfflineStorage)
    monkeypatch.setattr(chat_routes, "reset_managed_workspace", AsyncMock())
    monkeypatch.setattr(chat_routes, "reconcile_managed_workspace", AsyncMock(return_value=set()))
    monkeypatch.setattr(chat_routes, "upload_workspace_file", AsyncMock())
    monkeypatch.setattr(agent, "_model", lambda *_args: model)
    monkeypatch.setattr(db, "create_db", lambda _settings: (engine, store))
    monkeypatch.setattr(db, "reap_stale_runs", AsyncMock(return_value=0))
    monkeypatch.setattr(db, "get_session_or_raise", store.get_session)
    monkeypatch.setattr(db, "acquire_root_run", store.acquire_root)
    monkeypatch.setattr(db, "finish_run", AsyncMock(side_effect=store.finish))
    monkeypatch.setattr(db, "list_session_files", AsyncMock(return_value=[]))
    monkeypatch.setattr(db, "get_session_prior_turns", AsyncMock(return_value=[]))
    monkeypatch.setattr(db, "search_memories", AsyncMock(return_value=[]))
    monkeypatch.setattr(db, "recall_procedures", AsyncMock(return_value=[]))
    monkeypatch.setattr(db, "get_workspace_settings_overrides", store.get_workspace_overrides)
    monkeypatch.setattr(chat_routes, "list_new_artifacts", AsyncMock(return_value=[]))
    monkeypatch.setattr(agent, "consolidate_memory", AsyncMock())

    # Missing configured optimized_program_path fails fast during lifespan startup.
    missing_settings = base_settings.model_copy(
        update={"optimized_program_path": tmp_path / "missing.json"}
    )
    monkeypatch.setattr(api, "Settings", lambda: missing_settings)
    with pytest.raises(FileNotFoundError):
        async with api.lifespan(api.app):
            pass

    # Valid path is loaded once at startup and reused even after removing the file on disk.
    valid_settings = base_settings.model_copy(update={"optimized_program_path": artifact_path})
    monkeypatch.setattr(api, "Settings", lambda: valid_settings)
    seen_states: list[object] = []

    async def capture_state(_settings, *, optimized_state, run_id, emit, **_):
        seen_states.append(optimized_state)
        await emit(run_id, agent.RunStartedEvent("workspace"))
        await emit(run_id, agent.RunResultEvent("Cached"))
        return dspy.Prediction(answer="Cached", history=dspy.History(messages=[]))

    monkeypatch.setattr(agent, "run_workspace_task", capture_state)

    async with api.lifespan(api.app):
        cached = api.app.state.runtime.optimized_program_state
        assert isinstance(cached, dict)
        artifact_path.unlink()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api.app),
            base_url="https://test",
            headers=_AUTH,
        ) as client:
            resp = await client.post(f"/api/sessions/{store.session_id}/chat", json=_PAYLOAD)
            assert resp.status_code == 200
            assert "Cached" in resp.text
        assert seen_states == [cached]


@pytest.mark.asyncio
async def test_configured_web_research_catalog_and_pins(offline, monkeypatch):
    runtime = api.app.state.runtime
    skill = runtime.settings.skills_dir / "web-research" / "SKILL.md"
    skill.parent.mkdir()
    skill.write_text(
        "---\nname: web-research\ndescription: Read local notes\n---\nUse local notes."
    )
    api.app.state.runtime = replace(runtime, skills=SkillRegistry.load(runtime.settings.skills_dir))
    payload = {
        "messages": [{"role": "user", "parts": [{"type": "text", "text": "Read notes"}]}],
        "pinned_skills": ["web-research"],
    }

    async def run(_settings, *, selection, host_tools, **_):
        assert [skill.path for skill in selection.active_skills] == [skill]
        assert host_tools == {}
        return dspy.Prediction(answer="Used local notes", history=dspy.History(messages=[]))

    run_mock = AsyncMock(side_effect=run)
    monkeypatch.setattr(agent, "run_workspace_task", run_mock)
    catalog = (await offline.client.get("/api/skills")).json()
    assert "web-research" in {item["name"] for item in catalog}
    response = await offline.client.post(f"{offline.base}/chat", json=payload)
    assert response.status_code == 200 and "Used local notes" in response.text

    shipped = Path(__file__).resolve().parents[1] / "skills" / "web-research" / "SKILL.md"
    skill.write_bytes(shipped.read_bytes())
    api.app.state.runtime = replace(
        runtime,
        skills=SkillRegistry.load(runtime.settings.skills_dir),
        settings=runtime.settings.model_copy(update={"tavily_api_key": SecretStr("mock-host-key")}),
    )
    catalog = (await offline.client.get("/api/skills")).json()
    assert "web-research" not in {item["name"] for item in catalog}
    response = await offline.client.post(f"{offline.base}/chat", json=payload)
    assert response.status_code == 422 and "bundled web-research" in response.text
    run_mock.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["search", "open", "failure", "cap", "installed", "ordinary"])
async def test_web_research_through_chat(offline, monkeypatch, tmp_path, scenario):
    from test_phase2_engine import _LocalSandbox

    runtime = api.app.state.runtime
    registry = SkillRegistry.load(Path(__file__).resolve().parents[1] / "skills")
    registry.install(
        tmp_path, b"---\nname: other\ndescription: Test\nallowed-tools: web_open\n---\nRead."
    )
    url = "https://example.org/report"
    route = offline.base + "/chat"
    payload = {
        "messages": [{"role": "user", "parts": [{"type": "text", "text": "Read report"}]}],
        "pinned_skills": ["web-research"],
    }
    api.app.state.runtime = replace(runtime, skills=registry)
    response = await offline.client.post(route, json=payload)
    assert response.status_code == 422 and "TAVILY_API_KEY" in response.text
    if scenario == "open":
        catalog = (await offline.client.get("/api/skills")).json()
        assert "data-analysis" in {skill["name"] for skill in catalog}
        assert "web-research" not in {skill["name"] for skill in catalog}

    requests = []

    def post(endpoint, *, headers, json, timeout):
        assert headers["Authorization"] == "Bearer mock-host-key"
        action = endpoint.rsplit("/", 1)[1]
        requests.append(action)
        request = httpx.Request("POST", endpoint)
        if scenario == "failure":
            if len(requests) == 1:
                return httpx.Response(
                    200, request=request, json={"results": [], "failed_results": [{"url": url}]}
                )
            if len(requests) == 2:
                raise httpx.ReadTimeout("timeout", request=request)
            return httpx.Response(429, request=request)
        if action == "search":
            assert json["include_answer"] is False and json["max_results"] == 5
            result = {"results": [{"url": url, "title": "Report", "content": "summary"}]}
        else:
            assert json["urls"] == [url]
            result = {
                "results": [
                    {"url": url, "raw_content": "Verified 27. [Source](/source)" + " " * 16000}
                ]
            }
        return httpx.Response(200, json=result, request=request)

    monkeypatch.setattr(httpx, "post", post)
    open_call = f"web_open({url!r})"
    code = {
        "search": "hit = web_search('public figures')['results'][0]\npage = web_open(hit['url'])",
        "open": "page = " + open_call,
        "cap": "for _ in range(7): web_search('sample')\n"
        "result = spawn_agent('read twice')\nassert result['answer'] == 'budget verified'",
        "failure": f"assert 'error' in {open_call}\nassert 'error' in {open_call}\n"
        f"assert '429' in {open_call}['error']",
        "installed": "assert 'web_open' not in globals()",
        "ordinary": "assert 'web_search' not in globals()",
    }[scenario]
    if scenario in {"search", "open"}:
        code += "\nassert page['truncated'] and '[Source](/source)' in page['markdown']"
        code += "\nSUBMIT(page['markdown'][:12] + ' [Report](' + page['url'] + ')', [])"
    else:
        code += "\nSUBMIT('Passed', [])"
    steps = [{"plan": "Check the tool", "code": code}]
    if scenario == "cap":
        steps.append(
            {
                "plan": "Check shared allowance",
                "code": f"assert 'markdown' in {open_call}\n"
                f"assert 'eight-call' in {open_call}['error']\n"
                "SUBMIT('budget verified', [])",
            }
        )
    if scenario in {"ordinary", "installed"}:
        payload["pinned_skills"] = ["data-analysis" if scenario == "ordinary" else "other"]
    model = DummyLM(steps)
    api.app.state.runtime = replace(
        runtime,
        skills=registry,
        lm=model,
        sub_lm=model,
        settings=runtime.settings.model_copy(update={"tavily_api_key": SecretStr("mock-host-key")}),
    )
    if scenario == "open":
        catalog = (await offline.client.get("/api/skills")).json()
        assert "web-research" in {skill["name"] for skill in catalog}
        payload = {
            "messages": [{"role": "user", "parts": [{"type": "text", "text": f"Open {url}"}]}],
        }
    monkeypatch.setattr(
        chat_routes, "_chat_sandbox", AsyncMock(return_value=_LocalSandbox(tmp_path))
    )
    response = await offline.client.post(route, json=payload)
    expected = {
        "search": ["search", "extract"],
        "open": ["extract"],
        "cap": ["search"] * 7 + ["extract"],
        "failure": ["extract"] * 3,
        "installed": [],
        "ordinary": [],
    }
    assert requests == expected[scenario], response.text[-3000:]
    events = [
        json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: {")
    ]
    web_events = [event for event in events if event["type"] == "data-web-call"]
    outcomes = [
        event["data"]["outcome"] for event in web_events if event["data"]["outcome"] != "running"
    ]
    assert (
        outcomes
        == {
            "search": ["results", "truncated"],
            "open": ["truncated"],
            "failure": ["failed"] * 3,
            "cap": ["results"] * 7 + ["truncated", "exhausted"],
            "installed": [],
            "ordinary": [],
        }[scenario]
    )
    saved = offline.finish.await_args.kwargs["ui_parts"]
    saved_web = [part for part in saved if part["type"] == "data-web-call"]
    assert {part["id"]: part for part in saved_web} == {event["id"]: event for event in web_events}
    if saved_web:
        assert saved_web[-1]["data"]["limit"] == 8
        assert saved_web[-1]["data"]["used"] == len(requests)
        assert saved_web[-1]["data"]["target"] == url
        assert saved_web[-1]["data"]["elapsed_ms"] >= 0
    (details,) = [part for part in saved if part["type"] == "data-turn-details"]
    assert details in events
    assert details["data"]["runtime"] == "dspy.RLM"
    assert details["data"]["completion"] == "submit"
    assert details["data"]["root_actions"] == 1
    assert details["data"]["child_rlms"] == int(scenario == "cap")
    assert details["data"]["sub_lm_queries"] == 0
    assert details["data"]["trace_url"] is None
    if scenario == "cap":
        # Re-enter the real chat route. The skill tools must receive a new allowance.
        model = DummyLM([{"plan": "Read page", "code": f"{open_call}\nSUBMIT('Fresh', [])"}])
        api.app.state.runtime = replace(api.app.state.runtime, lm=model)
        fresh = await offline.client.post(route, json=payload)
        assert "Fresh" in fresh.text and len(requests) == 9
        (next_web,) = [
            part["data"]
            for part in offline.finish.await_args.kwargs["ui_parts"]
            if part["type"] == "data-web-call"
        ]
        assert next_web["used"] == 1 and next_web["limit"] == 8
    answer = "Verified 27. [Report]" if scenario in {"open", "search"} else "Passed"
    assert answer in response.text and "mock-host-key" not in response.text


def test_turn_details_link_targets_the_mlflow_trace(offline):
    runtime = api.app.state.runtime
    runtime = replace(
        runtime,
        mlflow_experiment_id="1",
        settings=runtime.settings.model_copy(
            update={"mlflow_tracking_uri": "http://localhost:5001/metrics?token=excluded"}
        ),
    )
    assert chat_routes._trace_link(runtime, "tr-test") == (
        "http://localhost:5001/metrics/#/experiments/1/traces?traceId=tr-test"
    )


async def _assert_chat_commits_once(offline, monkeypatch):
    async def answer(_settings, *, run_id, emit, **_):
        await emit(run_id, agent.RunStartedEvent("workspace"))
        await emit(run_id, agent.RunResultEvent("Verified"))
        return dspy.Prediction(answer="Verified", history=dspy.History(messages=[]))

    model = AsyncMock(side_effect=answer)
    monkeypatch.setattr(agent, "run_workspace_task", model)

    ready = await offline.client.get("/readyz")
    assert ready.status_code == 200 and ready.json() == {"status": "ok"}
    response = await offline.client.post(f"{offline.base}/chat", json=_PAYLOAD)
    assert response.status_code == 200 and "Verified" in response.text
    assert "data: [DONE]" in response.text
    model.assert_awaited_once()
    offline.finish.assert_awaited_once()
    assert offline.finish.await_args.kwargs["status"] == "completed"


@pytest.mark.asyncio
@pytest.mark.parametrize("tracking_endpoint", ["stalled", "refused"], indirect=True)
async def test_mlflow_unreachable_during_lifespan(offline, monkeypatch, tracking_endpoint, caplog):
    if tracking_endpoint.mode == "stalled":
        await asyncio.wait_for(tracking_endpoint.accepted.wait(), timeout=3)
    await _assert_chat_commits_once(offline, monkeypatch)
    async with asyncio.timeout(4):
        while "MLflow unavailable" not in caplog.text:
            await asyncio.sleep(0.01)
    assert api.app.state.runtime.tracking_pending


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["trace_update", "span_entry", "span_exit", "callback_init"])
async def test_mlflow_failure_after_admission(offline, monkeypatch, trace_uri, failure):
    runtime = replace(
        api.app.state.runtime,
        settings=offline.settings.model_copy(update={"mlflow_tracking_uri": trace_uri}),
    )
    monkeypatch.setattr(api.app.state, "runtime", runtime)
    original_span = mlflow.start_span

    @contextmanager
    def faulty_span(*args, **kwargs):
        if failure == "span_entry":
            raise OSError("start failed")
        with original_span(*args, **kwargs) as active:
            yield active
        if failure == "span_exit":
            raise OSError("end failed")

    monkeypatch.setattr(mlflow, "start_span", faulty_span)
    if failure == "trace_update":
        monkeypatch.setattr(mlflow, "update_current_trace", Mock(side_effect=OSError("offline")))

    if failure == "callback_init":
        monkeypatch.setattr("fleet_rlm.tracing.FleetMlflowCallback", Mock(side_effect=OSError()))

    await _assert_chat_commits_once(offline, monkeypatch)


@pytest.mark.asyncio
async def test_mlflow_setup_discards_late_result_and_recovers(offline, monkeypatch, trace_uri):
    from fleet_rlm import telemetry

    runtime = replace(
        api.app.state.runtime,
        settings=offline.settings.model_copy(update={"mlflow_tracking_uri": trace_uri}),
        tracking_pending=True,
    )
    release = threading.Event()
    entered = threading.Event()
    attempts = 0

    def lookup(*_):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            entered.set()
            assert release.wait(5)
            return "late"
        return "0"

    monkeypatch.setattr(telemetry, "_experiment_id", lookup)
    monkeypatch.setattr(telemetry, "_SETUP_SECONDS", 0.05)
    monkeypatch.setattr(telemetry, "_RETRY_SECONDS", 0.01)
    monkeypatch.setattr(mlflow.dspy, "autolog", lambda: None)
    destination = Mock()
    monkeypatch.setattr(mlflow.tracing, "set_destination", destination)
    try:
        async with telemetry.tracking_client(runtime, "optimizer"):
            assert await asyncio.to_thread(entered.wait, 2)
            await asyncio.sleep(0.12)
            assert runtime.tracking_pending and attempts == 1
            release.set()
            async with asyncio.timeout(2):
                while runtime.tracking_pending:
                    await asyncio.sleep(0.005)
            assert runtime.mlflow_experiment_id == "0"
            destination.assert_called_once()
            assert destination.call_args.args[0].experiment_id == "0"
    finally:
        release.set()


@pytest.mark.parametrize("failure", ["entry", "exit"])
def test_mlflow_context_preserves_turn_cancellation(monkeypatch, failure):
    from fleet_rlm.tracing import without_tracing

    @contextmanager
    def faulty_context(**_):
        if failure == "entry":
            raise OSError("context entry failed")
        yield
        raise OSError("context exit failed")

    monkeypatch.setattr(mlflow.tracing, "context", faulty_context)
    cancellation = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError) as raised, without_tracing():
        raise cancellation
    assert raised.value is cancellation


@pytest.mark.parametrize("blocked", ["setup", "flush"])
def test_mlflow_shutdown_releases_event_loop_with_blocked_sdk(monkeypatch, blocked):
    from fleet_rlm import telemetry

    entered = threading.Event()
    release = threading.Event()
    stopped = threading.Event()
    runtime = SimpleNamespace(
        settings=SimpleNamespace(
            mlflow_tracking_uri="http://127.0.0.1:1", mlflow_experiment="chat"
        ),
        tracking_pending=True,
    )

    def stuck(*_):
        entered.set()
        release.wait()
        return "0"

    monkeypatch.setattr(telemetry, "_FLUSH_SECONDS", 0.05)
    monkeypatch.setattr(
        telemetry, "_experiment_id", stuck if blocked == "setup" else lambda *_: "0"
    )
    monkeypatch.setattr(mlflow, "set_tracking_uri", lambda _: None)
    monkeypatch.setattr(mlflow.tracing, "set_destination", lambda _: None)
    monkeypatch.setattr(mlflow.dspy, "autolog", lambda: None)
    monkeypatch.setattr(mlflow, "flush_trace_async_logging", stuck)

    async def serve():
        async with telemetry.tracking_client(runtime, "optimizer"):
            while runtime.tracking_pending and not entered.is_set():
                await asyncio.sleep(0.005)

    def run_worker():
        asyncio.run(serve())
        stopped.set()

    worker = threading.Thread(target=run_worker)
    worker.start()
    try:
        assert entered.wait(2)
        assert stopped.wait(2), "loop shutdown waited for the MLflow SDK"
    finally:
        release.set()
        worker.join(3)


@pytest.mark.asyncio
async def test_optimizer_lookup_retries_after_chat_is_ready(offline, monkeypatch, trace_uri):
    from fleet_rlm import telemetry

    runtime = replace(
        api.app.state.runtime,
        settings=offline.settings.model_copy(update={"mlflow_tracking_uri": trace_uri}),
        tracking_pending=True,
    )
    calls = []

    def lookup(_settings, name):
        calls.append(name)
        if name == "optimizer":
            assert not runtime.tracking_pending and runtime.mlflow_experiment_id == "0"
            if calls.count(name) == 1:
                raise OSError("tracker unavailable")
            return "1"
        return "0"

    monkeypatch.setattr(telemetry, "_experiment_id", lookup)
    monkeypatch.setattr(telemetry, "_RETRY_SECONDS", 0.01)
    monkeypatch.setattr(mlflow.dspy, "autolog", lambda: None)
    async with asyncio.timeout(2):
        await telemetry._initialize(runtime, "optimizer")
    assert calls == [runtime.settings.mlflow_experiment, "optimizer", "optimizer"]
    assert runtime.mlflow_optimization_experiment_id == "1"


@pytest.mark.asyncio
async def test_trace_link_requires_a_stored_trace(offline, monkeypatch):
    from mlflow.exceptions import MlflowException

    runtime = api.app.state.runtime
    settings = runtime.settings.model_copy(update={"mlflow_tracking_uri": "http://127.0.0.1:5001"})
    monkeypatch.setattr(
        api.app.state, "runtime", replace(runtime, settings=settings, mlflow_experiment_id="1")
    )
    stored = SimpleNamespace(
        info=SimpleNamespace(
            trace_location=SimpleNamespace(mlflow_experiment=SimpleNamespace(experiment_id="1"))
        )
    )
    lookup = Mock(side_effect=[MlflowException("tracker offline"), stored])
    monkeypatch.setattr(
        mlflow, "MlflowClient", Mock(return_value=SimpleNamespace(get_trace=lookup))
    )
    trace_id = "tr-" + "a" * 32
    path = f"/api/traces/{trace_id}/link"
    assert (await offline.client.get(path)).json() == {"url": None}
    assert (await offline.client.get(path)).json() == {
        "url": f"http://127.0.0.1:5001/#/experiments/1/traces?traceId={trace_id}"
    }
    lookup.assert_called_with(trace_id, display=False)


@pytest.mark.asyncio
async def test_workspace_hydration_overlaps_four_independent_files(monkeypatch):
    active = set()
    restored = set()
    four_started = asyncio.Event()

    async def download(key, path, **kwargs):
        assert path not in active
        active.add(path)
        assert len(active) <= 4
        if len(active) == 4:
            four_started.set()
        await asyncio.wait_for(four_started.wait(), timeout=2)
        path.write_text(key)

    async def upload(sandbox, path, destination, **kwargs):
        assert path.read_text() == destination
        active.remove(path)
        restored.add(destination)

    monkeypatch.setattr(chat_routes, "reset_managed_workspace", AsyncMock())
    monkeypatch.setattr(chat_routes, "upload_workspace_file", upload)
    files = [
        SimpleNamespace(path=name, object_key=name, sha256="", size_bytes=0)
        for name in (f"artifacts/{index}.csv" for index in range(6))
    ]
    await chat_routes._hydrate_workspace(
        SimpleNamespace(storage=SimpleNamespace(download=download)), Mock(), files, 5
    )
    assert restored == {file.path for file in files}
