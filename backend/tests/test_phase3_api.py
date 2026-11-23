"""Offline Phase 3 checks against FastAPI's real ASGI routes and SSE transport."""

import asyncio
import hashlib
import json
import logging
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import dspy
import httpx
import pytest
import pytest_asyncio
from dspy.utils.dummies import DummyLM

from fleet_rlm import api, db
from fleet_rlm.config import Settings
from fleet_rlm.interpreter import DaytonaError
from fleet_rlm.skills import SkillDefinition, SkillRegistry

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


class OfflineStore:
    def __init__(self):
        now = datetime.now(UTC)
        self.session_id = uuid4()
        self.session = db.Session(
            id=self.session_id,
            title="Test session",
            history_json={"messages": []},
            daytona_sandbox_id="test-sandbox",
            model_override=None,
            created_at=now,
            updated_at=now,
        )
        self.sessions: dict[UUID, db.Session] = {self.session_id: self.session}
        self.active_by_session: dict[UUID, UUID] = {}
        self.checkouts = 0
        self.files: dict[tuple[UUID, str], db.File] = {}
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
                if session is None:
                    return None
                if "title" in params:
                    session.title = params["title"]
                if "model_override" in params:
                    session.model_override = params["model_override"]
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
            if session is None:
                raise db.SessionNotFoundError(session_id)
            return session

    async def bind_sandbox(self, _sessionmaker, session_id, sandbox_id):
        async with self():
            session = self.sessions.get(session_id)
            if session is None:
                raise db.SessionNotFoundError(session_id)
            if session.daytona_sandbox_id is None:
                session.daytona_sandbox_id = sandbox_id
                session.updated_at = datetime.now(UTC)
                return sandbox_id, True
            return session.daytona_sandbox_id, False

    async def acquire_root(self, _sessionmaker, session_id, _prompt):
        async with self():
            if session_id not in self.sessions:
                raise db.SessionNotFoundError(session_id)
            if session_id in self.active_by_session:
                raise db.TurnConflictError
            run_id = uuid4()
            self.active_by_session[session_id] = run_id
            return SimpleNamespace(id=run_id)

    async def finish(self, _sessionmaker, run_id, *, status, **details):
        async with self():
            for session_id, active_id in list(self.active_by_session.items()):
                if active_id == run_id:
                    self.active_by_session.pop(session_id, None)
                    if status == "completed":
                        self.sessions[session_id].history_json = details["history_json"]
                    break
            return True

    async def upsert_file(self, _sessionmaker, session_id, *, path, sha256, size_bytes, kind):
        async with self():
            if session_id not in self.sessions:
                raise db.SessionNotFoundError(session_id)
            existing = self.files.get((session_id, path))
            if existing is None:
                existing = db.File(
                    id=uuid4(),
                    session_id=session_id,
                    path=path,
                    sha256=sha256,
                    size_bytes=size_bytes,
                    kind=kind,
                    created_at=datetime.now(UTC),
                )
                self.files[(session_id, path)] = existing
            else:
                existing.sha256 = sha256
                existing.size_bytes = size_bytes
                existing.kind = kind
            return existing

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


@pytest_asyncio.fixture
async def offline(monkeypatch, tmp_path):
    skill = tmp_path / "table"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: table\ndescription: Analyze tabular data\n---\nInspect the data first.\n"
    )
    settings = Settings(
        _env_file=None,
        database_url="postgresql://localhost/unused",
        database_direct_url="postgresql://localhost/unused",
        daytona_api_key="offline",
        daytona_volume_id="offline",
        llm_model="openai/gpt-4o-mini",
        sub_llm_model="openai/gpt-4o-mini",
        llm_api_key="offline",
        mlflow_tracking_uri=None,
        api_bearer_token="offline-token",
        cors_origins=["https://client.example"],
        skills_dir=tmp_path,
    )
    store = OfflineStore()
    engine = SimpleNamespace(dispose=AsyncMock())
    reap = AsyncMock(return_value=0)
    finish = AsyncMock(side_effect=store.finish)
    create_child = AsyncMock(return_value=SimpleNamespace(id=uuid4()))
    artifact = {"path": "/workspace/artifacts/report.txt", "size_bytes": 1, "sha256": "a" * 64}
    upsert_artifact = AsyncMock(side_effect=store.upsert_file)
    list_files = AsyncMock(side_effect=store.list_files)
    bind_sandbox = AsyncMock(side_effect=store.bind_sandbox)
    ui_messages = AsyncMock(side_effect=store.ui_messages)

    async def upload(chunks, _path, **_options):
        assert store.checkouts == 0
        async for _ in chunks:
            pass

    async def delete_sandbox(_sandbox, **_options):
        assert store.checkouts == 0

    sandbox = SimpleNamespace(
        id="test-sandbox",
        state="started",
        fs=SimpleNamespace(
            upload_file_stream=AsyncMock(side_effect=upload),
            move_files=AsyncMock(),
            download_file=AsyncMock(return_value=b"report"),
            delete_file=AsyncMock(),
        ),
    )
    daytona = SimpleNamespace(
        v0=SimpleNamespace(
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
    monkeypatch.setattr(api.agent, "_model", lambda *_args: model)
    monkeypatch.setattr(db, "create_db", lambda _settings: (engine, store))
    monkeypatch.setattr(db, "reap_stale_runs", reap)
    monkeypatch.setattr(db, "get_session_or_raise", store.get_session)
    monkeypatch.setattr(db, "bind_session_sandbox_if_unset", bind_sandbox)
    monkeypatch.setattr(db, "acquire_root_run", store.acquire_root)
    monkeypatch.setattr(db, "create_child_run", create_child)
    monkeypatch.setattr(db, "finish_run", finish)
    monkeypatch.setattr(db, "list_session_files", list_files)
    monkeypatch.setattr(db, "get_session_ui_messages", ui_messages)
    monkeypatch.setattr(db, "search_memories", AsyncMock(return_value=[]))
    monkeypatch.setattr(db, "upsert_file_record", upsert_artifact)
    monkeypatch.setattr(db, "get_workspace_settings_overrides", store.get_workspace_overrides)
    monkeypatch.setattr(db, "update_workspace_settings_overrides", store.update_workspace_overrides)
    monkeypatch.setattr(db, "reset_workspace_settings_overrides", store.reset_workspace_overrides)
    monkeypatch.setattr(api, "list_new_artifacts", AsyncMock(return_value=[artifact]))
    memory = AsyncMock(side_effect=RuntimeError("Optional memory gate failed"))
    monkeypatch.setattr(api.agent, "consolidate_memory", memory)

    async with api.lifespan(api.app):
        reap.assert_awaited_once_with(store, settings.stale_run_timeout_seconds)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api.app),
            base_url="http://test",
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
                upsert_artifact=upsert_artifact,
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
    assert response.headers["access-control-expose-headers"] == "x-vercel-ai-ui-message-stream"

    offline.settings.api_bearer_token = None
    assert (
        await offline.client.get("/api/skills", headers={"Authorization": ""})
    ).status_code == 200


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
    offline.daytona.v0.get.assert_not_awaited()
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
    offline.sandbox.fs.upload_file_stream.assert_awaited_once()


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
    offline.daytona.v0.get.assert_not_awaited()


@pytest.mark.parametrize("name", ["../outside.csv", r"..\outside.csv", "/absolute.csv", "C:one"])
@pytest.mark.asyncio
async def test_upload_rejects_unsafe_names(offline, name):
    response = await offline.client.post(f"{offline.base}/files", files={"file": (name, b"x")})
    assert response.status_code == 400
    offline.daytona.v0.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_download_requires_a_safe_registered_path(offline):
    path = f"{offline.base}/files"
    response = await offline.client.get(f"{path}/artifacts/%2e%2e/secret")
    assert response.status_code == 400
    response = await offline.client.get(f"{path}/artifacts/report.txt")
    assert response.status_code == 404
    offline.daytona.v0.get.assert_not_awaited()

    file_path = "artifacts/report 1.txt"
    offline.store.add(db.File(path=file_path))
    response = await offline.client.get(f"{path}/artifacts/report%201.txt")
    assert response.content == b"report"
    assert response.headers["content-disposition"] == "attachment; filename*=UTF-8''report%201.txt"
    offline.sandbox.fs.download_file.assert_awaited_once_with(
        f"/workspace/{file_path}", offline.settings.daytona_exec_timeout_seconds
    )


def test_ui_frames_and_lossless_nested_snapshots():
    root = api.ui_stream.UiPartAccumulator("root")
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
    child = api.ui_stream.UiPartAccumulator("child")
    child.subagent("b", {"task": "Verify", "kind": "llm_query"})
    grandchild = api.ui_stream.UiPartAccumulator("grandchild")
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
        id=uuid4(), session_id=None, content="Use USD for financial totals", approved=True
    )
    session_memory = db.Memory(
        id=uuid4(),
        session_id=offline.store.session_id,
        content="Prefix summaries with COBALT-QA",
        approved=True,
    )
    monkeypatch.setattr(db, "search_memories", AsyncMock(return_value=[global_memory]))
    session_lookup = AsyncMock(return_value=[session_memory])
    monkeypatch.setattr(db, "list_approved_session_memories", session_lookup)
    turn = AsyncMock(return_value=dspy.Prediction(answer="Done", history=dspy.History(messages=[])))
    monkeypatch.setattr(api.agent, "run_workspace_task", turn)

    response = await offline.client.post(
        f"{offline.base}/chat",
        json={
            "messages": [{"role": "user", "parts": [{"type": "text", "text": "Use a skill"}]}],
            "pinned_skills": [skill_name],
        },
    )

    assert response.status_code == 200
    if skill_name == "skill-creator":
        session_lookup.assert_awaited_once_with(offline.store, offline.store.session_id)
        assert turn.await_args.kwargs["memories"] == [session_memory.content]
    else:
        session_lookup.assert_not_awaited()
        assert turn.await_args.kwargs["memories"] == [global_memory.content]


@pytest.mark.asyncio
async def test_consolidate_searches_prompt_and_answer_including_unapproved_memories(
    offline, monkeypatch
):
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
    monkeypatch.setattr(api.agent, "run_workspace_task", turn)

    response = await offline.client.post(
        f"{offline.base}/chat",
        json={
            "messages": [{"role": "user", "parts": [{"type": "text", "text": "remember it"}]}],
            "pinned_skills": ["table"],
        },
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


@pytest.mark.asyncio
async def test_post_sse_frames_and_persistence(offline, monkeypatch):
    child_id, nested_id = uuid4(), uuid4()
    offline.create_child.side_effect = [SimpleNamespace(id=child_id), SimpleNamespace(id=nested_id)]

    async def complete(_settings, *, task, run_id, selection, emit, callbacks, **_):
        assert offline.store.checkouts == 0
        assert task == "Analyze @table" and selection.active_skills[0].name == "table"
        await emit(run_id, api.agent.RunStartedEvent("workspace"))
        await emit(run_id, api.agent.ReasoningEvent("Inspect the data"))
        await emit(run_id, api.agent.PythonExecStartedEvent(1, "```python\nprint(x)\n```"))
        await emit(run_id, api.agent.PythonExecFailedEvent(1, "NameError: x"))

        child = api.agent.ChildRun(str(child_id), run_id, 1, "subagent", "Read the table")
        assert await callbacks.on_child_start(child) == child_id
        await emit(run_id, api.agent.SubagentStartedEvent(child))
        await emit(child.id, api.agent.RunStartedEvent("workspace"))

        nested = api.agent.ChildRun(str(nested_id), child.id, 2, "llm_query", "Check the totals")
        assert await callbacks.on_child_start(nested) == nested_id
        await emit(child.id, api.agent.SubagentStartedEvent(nested))
        await emit(nested.id, api.agent.RunStartedEvent("llm_query"))
        await emit(nested.id, api.agent.RunResultEvent("Totals checked"))
        offline.finish.assert_not_awaited()
        assert offline.store.statements == []
        await callbacks.on_child_finish(nested, "completed", "Totals checked", None)
        await emit(child.id, api.agent.SubagentCompletedEvent(nested.id))

        await emit(child.id, api.agent.RunResultEvent("Table read"))
        assert offline.finish.await_count == 1
        assert offline.store.statements == []
        await callbacks.on_child_finish(child, "completed", "Table read", None)
        await emit(run_id, api.agent.SubagentCompletedEvent(child.id))
        await emit(run_id, api.agent.RunResultEvent("Verified"))
        return dspy.Prediction(answer="Verified", history=dspy.History(messages=[{"task": task}]))

    monkeypatch.setattr(api.agent, "run_workspace_task", complete)
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
        "tool-output-error",
        "tool-input-available",
        *["tool-output-available"] * 15,
        "data-file-tree",
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
    assert frames[2]["id"] == frames[-5]["id"] == f"{root_id}:file-tree"
    assert frames[-5]["data"]["files"][0]["path"] == "artifacts/report.txt"
    assert [frame["id"] for frame in frames[3:6]] == [f"{root_id}:1"] * 3
    assert frames[6]["toolCallId"] == frames[8]["toolCallId"] == f"{root_id}:2"
    assert frames[6]["inputEncoding"] == "text"
    assert frames[7]["inputTextDelta"] == "print(x)"
    assert frames[8]["errorText"] == "NameError: x"
    assert frames[9]["toolCallId"] == f"{root_id}:3"
    progress = frames[10:-5]
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
    assert root_finish.kwargs["ui_parts"][-2]["output"] == progress[-1]["output"]
    assert root_finish.kwargs["ui_parts"][1] == frames[-5]
    offline.upsert_artifact.assert_awaited_once()
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


@pytest.mark.parametrize("status", ["failed", "cancelled"])
@pytest.mark.asyncio
async def test_incomplete_child_snapshot_survives_root_teardown(offline, monkeypatch, status):
    async def interrupt(_settings, *, run_id, emit, callbacks, **_):
        await emit(run_id, api.agent.RunStartedEvent("workspace"))
        child_id = await callbacks.on_child_start(
            api.agent.ChildRun("placeholder", run_id, 1, "subagent", "Read the table")
        )
        child = api.agent.ChildRun(str(child_id), run_id, 1, "subagent", "Read the table")
        await emit(run_id, api.agent.SubagentStartedEvent(child))
        await emit(child.id, api.agent.RunStartedEvent("workspace"))
        await emit(child.id, api.agent.PythonExecStartedEvent(1, "print('working')"))
        assert offline.store.statements == []
        offline.finish.assert_not_awaited()
        if status == "cancelled":
            raise asyncio.CancelledError
        raise RuntimeError("Turn interrupted before the child was finalized")

    monkeypatch.setattr(api.agent, "run_workspace_task", interrupt)
    response = await offline.client.post(f"{offline.base}/chat", json=_PAYLOAD)
    assert response.status_code == 200
    assert ("data: [DONE]" in response.text) is (status == "failed")
    offline.finish.assert_awaited_once()
    assert offline.finish.await_args.kwargs["status"] == status
    child_part = offline.finish.await_args.kwargs["ui_parts"][-1]
    assert child_part["preliminary"] is True
    parts = child_part["output"]["parts"]
    assert parts[-1]["state"] == "input-streaming"
    assert parts[-1]["input"] == "print('working')"

    child_update, fallback_update = offline.store.statements
    assert child_update.compile().params["status"] == status
    assert child_update.compile().params["id_1"] == offline.create_child.return_value.id
    assert child_update.compile().params["ui_parts"] == parts
    assert "ui_parts" not in fallback_update.compile().params


@pytest.mark.parametrize("asgi_version", ["2.3", "2.4"])
@pytest.mark.asyncio
async def test_disconnect_aborts_context_and_cancels_run(offline, asgi_version):
    executing = asyncio.Event()
    disconnected = asyncio.Event()
    context = object()

    async def execute(code, **_options):
        if "Verified" not in code:
            return SimpleNamespace(error=None)
        executing.set()
        await asyncio.Future()

    sandbox = offline.sandbox
    sandbox.code_interpreter = SimpleNamespace(
        create_context=AsyncMock(return_value=context),
        delete_context=AsyncMock(),
        run_code=execute,
    )
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
        "scheme": "http",
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

    sandbox.code_interpreter.delete_context.assert_awaited_once_with(
        context, request_timeout=offline.settings.daytona_exec_timeout_seconds
    )
    mailbox = sandbox.fs.delete_file.await_args.args[0]
    assert mailbox.startswith("/tmp/fleet-rpc-")
    assert sandbox.fs.delete_file.await_args.kwargs["recursive"] is True
    assert offline.finish.await_args.args == (offline.store, root_id)
    assert offline.finish.await_args.kwargs["status"] == "cancelled"
    part = offline.finish.await_args.kwargs["ui_parts"][-1]
    assert part["state"] == "input-streaming"
    assert part["input"] == "SUBMIT('Verified', [])"
    assert offline.store.active is None and offline.store.checkouts == 0
    assert not any(b"[DONE]" in message.get("body", b"") for message in sent)
    offline.memory.assert_not_awaited()


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
        "daytona_sandbox_id",
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
        "daytona_volume_id": "offline",
        "daytona_snapshot": "fleet-rlm-py313-dspy35-v1",
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
        "daytona_target",
        "daytona_auto_stop_minutes",
        "daytona_exec_timeout_seconds",
    }

    for invalid_payload in (
        {"llm_api_key": "injected"},
        {"daytona_api_key": "injected"},
        {"database_url": "postgresql://attacker/db"},
        {"api_bearer_token": "override"},
        {"llm_base_url": "https://attacker.example/v1"},
        {"daytona_snapshot": "custom-snapshot"},
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
        {"daytona_auto_stop_minutes": 0},
        {"daytona_auto_stop_minutes": 1441},
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
            "daytona_snapshot": "legacy-snapshot",
            "mlflow_experiment": "legacy-experiment",
        }
    )
    sanitized = await offline.client.get("/api/settings")
    assert sanitized.status_code == 200
    assert sanitized.json()["overrides"] == {}
    assert sanitized.json()["status"]["llm_base_url"] is None
    assert sanitized.json()["status"]["daytona_snapshot"] == "fleet-rlm-py313-dspy35-v1"
    assert sanitized.json()["status"]["mlflow_experiment"] == "fleet-rlm"

    patched = await offline.client.patch(
        "/api/settings",
        json={
            "llm_model": "openai/gpt-4.1",
            "rlm_max_iters": 12,
            "rlm_max_depth": 7,
            "history_max_turns": 0,
            "daytona_target": "eu",
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
        "daytona_target": "eu",
        "daytona_exec_timeout_seconds": 45,
    }
    assert body["effective"]["llm_model"] == "openai/gpt-4.1"
    assert body["effective"]["rlm_max_iters"] == 12
    assert body["effective"]["rlm_max_depth"] == 7
    assert body["effective"]["history_max_turns"] == 0
    assert body["effective"]["daytona_target"] == "eu"
    assert body["effective"]["daytona_exec_timeout_seconds"] == 45
    assert body["defaults"]["llm_model"] == "openai/gpt-4o-mini"
    assert body["defaults"]["rlm_max_iters"] == 20
    assert body["defaults"]["rlm_max_depth"] == 4

    cleared = await offline.client.patch(
        "/api/settings",
        json={"rlm_max_iters": None, "daytona_target": "   "},
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
    assert cleared_body["effective"]["daytona_target"] is None

    reset = await offline.client.delete("/api/settings")
    assert reset.status_code == 200
    reset_body = reset.json()
    assert reset_body["overrides"] == {}
    assert reset_body["effective"] == reset_body["defaults"]


@pytest.mark.asyncio
async def test_run_turn_and_files_apply_neon_settings_overrides(offline, monkeypatch):
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

    monkeypatch.setattr(api.agent, "_model", fake_model)
    monkeypatch.setattr(api.dspy, "LM", fake_decision_lm)

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

    observed: dict[str, object] = {}

    async def capture_turn(settings, *, model_override, sub_lm, optimized_state, run_id, emit, **_):
        observed["settings"] = settings
        observed["model_override"] = model_override
        observed["sub_lm"] = sub_lm
        observed["optimized_state"] = optimized_state
        await emit(run_id, api.agent.RunStartedEvent("workspace"))
        await emit(run_id, api.agent.RunResultEvent("Overridden"))
        return dspy.Prediction(answer="Overridden", history=dspy.History(messages=[]))

    monkeypatch.setattr(api.agent, "run_workspace_task", capture_turn)

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
    assert observed["model_override"] is not startup_lm
    assert observed["sub_lm"] is not startup_sub_lm
    assert observed["optimized_state"] is None
    assert created_models == [
        ("openai/gpt-4.1", None),
        ("openai/gpt-4.1-mini", None),
    ]
    assert created_decision_models == ["typesafe:jev-2026-01", "typesafe:jev-2026-01"]
    assert offline.memory.await_args.args[0].decision_model == "typesafe:jev-2026-01"
    assert offline.memory.await_args.kwargs["decision_lm"] is not startup_decision_lm
    # Startup runtime remains immutable across requests.
    assert runtime.settings.rlm_max_iters == 20
    assert runtime.lm is startup_lm
    assert runtime.sub_lm is startup_sub_lm
    assert runtime.decision_lm is startup_decision_lm

    file_path = "artifacts/report.txt"
    offline.store.add(db.File(path=file_path))
    download = await offline.client.get(f"{offline.base}/files/{file_path}")
    assert download.status_code == 200
    offline.sandbox.fs.download_file.assert_awaited_once_with(f"/workspace/{file_path}", 33)


@pytest.mark.asyncio
async def test_sessions_crud_messages_files_and_limit(offline, caplog):
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
    offline.daytona.v0.get.assert_awaited_once_with("test-sandbox")
    offline.daytona.v0.delete.assert_awaited_once_with(offline.sandbox, wait=True)
    assert offline.store.checkouts == 0

    # Deleting a session whose remote sandbox cleanup returns 404 or 500 still returns 204.
    custom_uuid = UUID(custom_id)
    offline.store.sessions[custom_uuid].daytona_sandbox_id = "remote-missing"
    offline.daytona.v0.get.side_effect = DaytonaError("Not found", status_code=404)
    assert (await offline.client.delete(f"/api/sessions/{custom_id}")).status_code == 204
    assert custom_uuid not in offline.store.sessions

    default_uuid = UUID(default_body["id"])
    offline.store.sessions[default_uuid].daytona_sandbox_id = "remote-error"
    offline.daytona.v0.get.side_effect = DaytonaError("Gateway down", status_code=503)
    with caplog.at_level(logging.WARNING, logger=api.__name__):
        assert (await offline.client.delete(f"/api/sessions/{default_uuid}")).status_code == 204
    assert default_uuid not in offline.store.sessions
    assert "Could not delete Daytona sandbox remote-error" in caplog.text


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
async def test_zero_db_checkouts_across_daytona_io_and_sandbox_bind_races(offline, monkeypatch):
    observed_checkouts: dict[str, int] = {}

    async def checked_upload(chunks, _path, **_options):
        observed_checkouts["upload_file_stream"] = offline.store.checkouts
        async for _ in chunks:
            pass

    async def checked_move(*_args, **_kwargs):
        observed_checkouts["move_files"] = offline.store.checkouts

    async def checked_download(*_args, **_kwargs):
        observed_checkouts["download_file"] = offline.store.checkouts
        return b"hello"

    async def checked_delete(_sandbox, **_kwargs):
        observed_checkouts["daytona_delete"] = offline.store.checkouts

    offline.sandbox.fs.upload_file_stream = AsyncMock(side_effect=checked_upload)
    offline.sandbox.fs.move_files = AsyncMock(side_effect=checked_move)
    offline.sandbox.fs.download_file = AsyncMock(side_effect=checked_download)
    offline.daytona.v0.delete = AsyncMock(side_effect=checked_delete)

    # 1. Normal upload + download + delete never hold DB checkouts during Daytona calls.
    upload_resp = await offline.client.post(
        f"{offline.base}/files", files={"file": ("sales.csv", b"a,b\n1,2\n")}
    )
    assert upload_resp.status_code == 201
    assert observed_checkouts["upload_file_stream"] == 0
    assert observed_checkouts["move_files"] == 0
    assert offline.store.checkouts == 0

    dl_resp = await offline.client.get(f"{offline.base}/files/files/sales.csv")
    assert dl_resp.status_code == 200
    assert observed_checkouts["download_file"] == 0
    assert offline.store.checkouts == 0

    del_resp = await offline.client.delete(offline.base)
    assert del_resp.status_code == 204
    assert observed_checkouts["daytona_delete"] == 0
    assert offline.store.checkouts == 0

    # 2. Concurrent sandbox bind race during upload: loser deletes duplicate & adopts winner.
    race_session = (await offline.client.post("/api/sessions", json={"title": "Race"})).json()
    race_id = UUID(race_session["id"])
    dup_sandbox = SimpleNamespace(
        id="dup-sandbox",
        state="started",
        fs=SimpleNamespace(
            upload_file_stream=AsyncMock(),
            move_files=AsyncMock(),
            delete_file=AsyncMock(),
        ),
    )
    winner_uploads: list[str] = []

    async def winner_upload(chunks, path, **_options):
        assert offline.store.checkouts == 0
        winner_uploads.append(path)
        async for _ in chunks:
            pass

    winner_sandbox = SimpleNamespace(
        id="winner-sandbox",
        state="started",
        fs=SimpleNamespace(
            upload_file_stream=AsyncMock(side_effect=winner_upload),
            move_files=AsyncMock(),
            delete_file=AsyncMock(),
        ),
    )

    async def fake_ensure(_daytona, _settings, _sid, existing_id):
        assert offline.store.checkouts == 0
        if existing_id is None:
            return dup_sandbox, "dup-sandbox", True
        assert existing_id == "winner-sandbox"
        return winner_sandbox, "winner-sandbox", False

    async def lose_race(_sessionmaker, sid, _sandbox_id):
        offline.store.sessions[sid].daytona_sandbox_id = "winner-sandbox"
        return "winner-sandbox", False

    monkeypatch.setattr(api, "ensure_session_sandbox", fake_ensure)
    monkeypatch.setattr(db, "bind_session_sandbox_if_unset", AsyncMock(side_effect=lose_race))
    offline.daytona.v0.delete.reset_mock()

    race_resp = await offline.client.post(
        f"/api/sessions/{race_id}/files", files={"file": ("race.csv", b"x\n1\n")}
    )
    assert race_resp.status_code == 201
    offline.daytona.v0.delete.assert_awaited_once_with(dup_sandbox, wait=True)
    assert len(winner_uploads) == 1
    assert offline.store.checkouts == 0

    # 3. Session deleted mid-upload cleans up uploaded file in Daytona and returns 404.
    async def delete_mid_upload(*_args, **_kwargs):
        offline.store.sessions.pop(race_id, None)

    winner_sandbox.fs.move_files = AsyncMock(side_effect=delete_mid_upload)
    mid_resp = await offline.client.post(
        f"/api/sessions/{race_id}/files", files={"file": ("orphan.csv", b"y\n2\n")}
    )
    assert mid_resp.status_code == 404
    winner_sandbox.fs.delete_file.assert_awaited_once_with(
        "/workspace/files/orphan.csv",
        request_timeout=offline.settings.daytona_exec_timeout_seconds,
    )

    # 4. _chat_sandbox also deletes duplicate and adopts winner on concurrent race.
    chat_session = db.Session(id=uuid4(), title="Chat race", daytona_sandbox_id=None)
    offline.store.add(chat_session)
    offline.daytona.v0.delete.reset_mock()
    adopted = await api._chat_sandbox(api.app.state.runtime, chat_session, offline.settings)
    assert adopted is winner_sandbox
    offline.daytona.v0.delete.assert_awaited_once_with(dup_sandbox, wait=True)

    # 5. Session deleted while sandbox is being created deletes orphan sandbox and returns 404.
    vanishing_session = db.Session(id=uuid4(), title="Vanishing", daytona_sandbox_id=None)
    offline.store.add(vanishing_session)

    async def vanish_on_bind(_sessionmaker, sid, _sandbox_id):
        offline.store.sessions.pop(sid, None)
        raise db.SessionNotFoundError(sid)

    monkeypatch.setattr(db, "bind_session_sandbox_if_unset", AsyncMock(side_effect=vanish_on_bind))
    offline.daytona.v0.delete.reset_mock()
    vanish_resp = await offline.client.post(
        f"/api/sessions/{vanishing_session.id}/files", files={"file": ("gone.csv", b"z\n3\n")}
    )
    assert vanish_resp.status_code == 404
    offline.daytona.v0.delete.assert_awaited_once_with(dup_sandbox, wait=True)


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
        await emit(run_id, api.agent.RunStartedEvent("workspace"))
        await emit(run_id, api.agent.RunResultEvent("Recovered"))
        return dspy.Prediction(answer="Recovered", history=dspy.History(messages=[]))

    offline.reap.reset_mock()
    offline.reap.side_effect = reap_stale
    monkeypatch.setattr(api.agent, "run_workspace_task", quick_turn)

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
        await emit(run_id, api.agent.RunStartedEvent("workspace"))
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
        await emit(run_id, api.agent.RunResultEvent(answer))
        return dspy.Prediction(answer=answer, history=dspy.History(messages=[]))

    monkeypatch.setattr(api.agent, "run_workspace_task", controlled_turn)

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

    # Pre-populate existing input file and existing artifact to test overwrite detection.
    offline.store.add(
        db.File(
            session_id=offline.store.session_id,
            path="files/input.csv",
            sha256="1" * 64,
            size_bytes=42,
            kind="input",
        )
    )
    offline.store.add(
        db.File(
            session_id=offline.store.session_id,
            path="artifacts/report.txt",
            sha256="0" * 64,
            size_bytes=5,
            kind="artifact",
        )
    )

    observed: dict[str, object] = {}
    real_select_skills = api.select_skills

    def spy_select_skills(settings, registry, task, pinned_skills=None, **kwargs):
        observed["select_decision_lm"] = kwargs.get("decision_lm")
        observed["pinned_skills"] = list(pinned_skills or [])
        return real_select_skills(settings, registry, task, pinned_skills, **kwargs)

    async def spy_list_artifacts(_sandbox, known):
        observed["known_files"] = dict(known)
        return [
            {
                "path": "/workspace/artifacts/report.txt",
                "sha256": "b" * 64,
                "size_bytes": 12,
            },
            {
                "path": "/workspace/artifacts/summary.json",
                "sha256": "c" * 64,
                "size_bytes": 24,
            },
        ]

    async def fake_turn(_settings, *, selection, run_id, emit, **_):
        observed["active_skills"] = [skill.name for skill in selection.active_skills]
        await emit(run_id, api.agent.RunStartedEvent("workspace"))
        await emit(run_id, api.agent.RunResultEvent("Updated artifacts"))
        return dspy.Prediction(answer="Updated artifacts", history=dspy.History(messages=[]))

    def fail_new_lm(*_args, **_kwargs):
        pytest.fail("Unchanged decision_model must reuse runtime.decision_lm without dspy.LM()")

    monkeypatch.setattr(api, "select_skills", spy_select_skills)
    monkeypatch.setattr(api, "list_new_artifacts", spy_list_artifacts)
    monkeypatch.setattr(api.agent, "run_workspace_task", fake_turn)
    monkeypatch.setattr(api.dspy, "LM", fail_new_lm)

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
    assert observed["known_files"] == {
        "files/input.csv": ("1" * 64, 42),
        "artifacts/report.txt": ("0" * 64, 5),
    }

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
    assert final_files[0]["sha256"] == "b" * 64
    assert final_files[0]["size_bytes"] == 12

    # Both overwritten and newly created artifacts were upserted into Neon.
    upserted_paths = [call.kwargs["path"] for call in offline.upsert_artifact.await_args_list]
    assert upserted_paths == ["artifacts/report.txt", "artifacts/summary.json"]
    assert (
        offline.store.files[(offline.store.session_id, "artifacts/report.txt")].sha256 == "b" * 64
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
        daytona_volume_id="offline",
        llm_model="openai/gpt-4o-mini",
        sub_llm_model="openai/gpt-4o-mini",
        llm_api_key="offline",
        mlflow_tracking_uri=None,
        api_bearer_token="offline-token",
        skills_dir=tmp_path,
    )
    program = api.agent.build_program(base_settings, [], sub_lm=model)
    artifact_path = tmp_path / "rlm_program.json"
    program.save(artifact_path)

    store = OfflineStore()
    engine = SimpleNamespace(dispose=AsyncMock())
    sandbox = SimpleNamespace(id="test-sandbox", state="started", fs=SimpleNamespace())
    daytona = SimpleNamespace(
        v0=SimpleNamespace(get=AsyncMock(return_value=sandbox), delete=AsyncMock())
    )

    @asynccontextmanager
    async def daytona_client(_settings):
        yield daytona

    monkeypatch.setattr(api, "make_daytona_client", daytona_client)
    monkeypatch.setattr(api.agent, "_model", lambda *_args: model)
    monkeypatch.setattr(db, "create_db", lambda _settings: (engine, store))
    monkeypatch.setattr(db, "reap_stale_runs", AsyncMock(return_value=0))
    monkeypatch.setattr(db, "get_session_or_raise", store.get_session)
    monkeypatch.setattr(db, "acquire_root_run", store.acquire_root)
    monkeypatch.setattr(db, "finish_run", AsyncMock(side_effect=store.finish))
    monkeypatch.setattr(db, "list_session_files", AsyncMock(return_value=[]))
    monkeypatch.setattr(db, "search_memories", AsyncMock(return_value=[]))
    monkeypatch.setattr(db, "get_workspace_settings_overrides", store.get_workspace_overrides)
    monkeypatch.setattr(api, "list_new_artifacts", AsyncMock(return_value=[]))
    monkeypatch.setattr(api.agent, "consolidate_memory", AsyncMock())

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
        await emit(run_id, api.agent.RunStartedEvent("workspace"))
        await emit(run_id, api.agent.RunResultEvent("Cached"))
        return dspy.Prediction(answer="Cached", history=dspy.History(messages=[]))

    monkeypatch.setattr(api.agent, "run_workspace_task", capture_state)

    async with api.lifespan(api.app):
        cached = api.app.state.runtime.optimized_program_state
        assert isinstance(cached, dict)
        artifact_path.unlink()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api.app),
            base_url="http://test",
            headers=_AUTH,
        ) as client:
            resp = await client.post(f"/api/sessions/{store.session_id}/chat", json=_PAYLOAD)
            assert resp.status_code == 200
            assert "Cached" in resp.text
        assert seen_states == [cached]
