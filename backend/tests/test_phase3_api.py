"""Offline Phase 3 checks against FastAPI's real ASGI routes and SSE transport."""

import asyncio
import hashlib
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import dspy
import httpx
import pytest
import pytest_asyncio
from dspy.utils.dummies import DummyLM

from fleet_rlm import api, db
from fleet_rlm.config import Settings

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
        self.active = None
        self.checkouts = 0
        self.files = {}
        self.statements = []
        self.workspace_overrides = {}

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
        if model is db.Session and identity == self.session_id:
            return self.session
        return None

    async def execute(self, query):
        assert self.checkouts > 0
        self.statements.append(query)

    async def rollback(self):
        pass

    async def scalar(self, query):
        if getattr(query, "is_update", False):
            params = query.compile().params
            if params.get("id_1") != self.session_id:
                return None
            if "title" in params:
                self.session.title = params["title"]
            if "model_override" in params:
                self.session.model_override = params["model_override"]
            self.session.updated_at = datetime.now(UTC)
            return self.session
        if query.column_descriptions[0]["entity"] is db.Run:
            return self.active
        file = self.files.get(query.compile().params["path_1"])
        if file and query.column_descriptions[0]["name"] == "id":
            return file.id
        return file

    def add(self, record):
        record.id = uuid4()
        record.created_at = datetime.now(UTC)
        self.files[record.path] = record

    async def get_session(self, _sessionmaker, session_id):
        async with self() as connection:
            session = await connection.get(db.Session, session_id)
            if session is None:
                raise db.SessionNotFoundError(session_id)
            return session

    async def acquire_root(self, _sessionmaker, session_id, prompt):
        async with self():
            assert session_id == self.session_id and prompt == "Analyze @table"
            if self.active:
                raise db.TurnConflictError
            self.active = uuid4()
            return SimpleNamespace(id=self.active)

    async def finish(self, _sessionmaker, run_id, *, status, **details):
        async with self():
            if run_id == self.active:
                self.active = None
                if status == "completed":
                    self.session.history_json = details["history_json"]
            return True

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
    reap = AsyncMock()
    finish = AsyncMock(side_effect=store.finish)
    create_child = AsyncMock(return_value=SimpleNamespace(id=uuid4()))
    artifact = {"path": "/workspace/artifacts/report.txt", "size_bytes": 1, "sha256": "a" * 64}
    upsert_artifact = AsyncMock()

    async def upload(chunks, _path, **_options):
        async for _ in chunks:
            pass

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
    daytona = SimpleNamespace(get=AsyncMock(return_value=sandbox))

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
    monkeypatch.setattr(db, "acquire_root_run", store.acquire_root)
    monkeypatch.setattr(db, "create_child_run", create_child)
    monkeypatch.setattr(db, "finish_run", finish)
    monkeypatch.setattr(db, "list_session_files", AsyncMock(return_value=[]))
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
                finish=finish,
                create_child=create_child,
                upsert_artifact=upsert_artifact,
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
    offline.daytona.get.assert_not_awaited()
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
    offline.daytona.get.assert_not_awaited()


@pytest.mark.parametrize("name", ["../outside.csv", r"..\outside.csv", "/absolute.csv", "C:one"])
@pytest.mark.asyncio
async def test_upload_rejects_unsafe_names(offline, name):
    response = await offline.client.post(f"{offline.base}/files", files={"file": (name, b"x")})
    assert response.status_code == 400
    offline.daytona.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_download_requires_a_safe_registered_path(offline):
    path = f"{offline.base}/files"
    response = await offline.client.get(f"{path}/artifacts/%2e%2e/secret")
    assert response.status_code == 400
    response = await offline.client.get(f"{path}/artifacts/report.txt")
    assert response.status_code == 404
    offline.daytona.get.assert_not_awaited()

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
        "llm_api_key_configured": True,
        "bearer_auth_enabled": True,
        "mlflow_tracking_uri": None,
        "optimized_program_path": None,
        "skill_selector_path": None,
        "memory_gate_path": None,
        "skills_count": 1,
    }
    assert set(data["effective"]) == {
        "llm_model",
        "sub_llm_model",
        "decision_model",
        "llm_base_url",
        "rlm_max_iters",
        "rlm_max_llm_calls",
        "rlm_max_depth",
        "rlm_max_output_chars",
        "history_max_turns",
        "history_max_chars",
        "daytona_snapshot",
        "daytona_target",
        "daytona_auto_stop_minutes",
        "daytona_exec_timeout_seconds",
        "mlflow_experiment",
    }

    for invalid_payload in (
        {"llm_api_key": "injected"},
        {"daytona_api_key": "injected"},
        {"database_url": "postgresql://attacker/db"},
        {"api_bearer_token": "override"},
        {"rlm_max_iters": 0},
        {"rlm_max_iters": 101},
        {"rlm_max_llm_calls": 0},
        {"rlm_max_llm_calls": 501},
        {"rlm_max_depth": 0},
        {"rlm_max_depth": 9},
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
        {"mlflow_experiment": ""},
    ):
        rejected = await offline.client.patch("/api/settings", json=invalid_payload)
        assert rejected.status_code == 422, invalid_payload

    patched = await offline.client.patch(
        "/api/settings",
        json={
            "llm_model": "openai/gpt-4.1",
            "llm_base_url": "https://gateway.example/v1",
            "rlm_max_iters": 12,
            "daytona_target": "eu",
            "daytona_exec_timeout_seconds": 45,
        },
    )
    assert patched.status_code == 200
    body = patched.json()
    assert body["overrides"] == {
        "llm_model": "openai/gpt-4.1",
        "llm_base_url": "https://gateway.example/v1",
        "rlm_max_iters": 12,
        "daytona_target": "eu",
        "daytona_exec_timeout_seconds": 45,
    }
    assert body["effective"]["llm_model"] == "openai/gpt-4.1"
    assert body["effective"]["llm_base_url"] == "https://gateway.example/v1"
    assert body["effective"]["rlm_max_iters"] == 12
    assert body["effective"]["daytona_target"] == "eu"
    assert body["effective"]["daytona_exec_timeout_seconds"] == 45
    assert body["defaults"]["llm_model"] == "openai/gpt-4o-mini"
    assert body["defaults"]["rlm_max_iters"] == 20

    cleared = await offline.client.patch(
        "/api/settings",
        json={"rlm_max_iters": None, "llm_base_url": "", "daytona_target": "   "},
    )
    assert cleared.status_code == 200
    cleared_body = cleared.json()
    assert cleared_body["overrides"] == {
        "llm_model": "openai/gpt-4.1",
        "daytona_exec_timeout_seconds": 45,
    }
    assert cleared_body["effective"]["rlm_max_iters"] == 20
    assert cleared_body["effective"]["llm_base_url"] is None
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
    startup_sub_lm = runtime.program.sub_lm
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
            "llm_base_url": "https://override.example/v1",
            "rlm_max_iters": 7,
            "daytona_exec_timeout_seconds": 33,
        },
    )
    assert patch_resp.status_code == 200

    observed: dict[str, object] = {}

    async def capture_turn(settings, *, model_override, sub_lm, run_id, emit, **_):
        observed["settings"] = settings
        observed["model_override"] = model_override
        observed["sub_lm"] = sub_lm
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
    assert effective.llm_base_url == "https://override.example/v1"
    assert effective.rlm_max_iters == 7
    assert effective.daytona_exec_timeout_seconds == 33
    assert observed["model_override"] is not startup_lm
    assert observed["sub_lm"] is not startup_sub_lm
    assert created_models == [
        ("openai/gpt-4.1", "https://override.example/v1"),
        ("openai/gpt-4.1-mini", "https://override.example/v1"),
    ]
    assert created_decision_models == ["typesafe:jev-2026-01", "typesafe:jev-2026-01"]
    assert offline.memory.await_args.args[0].decision_model == "typesafe:jev-2026-01"
    # Startup runtime remains immutable across requests.
    assert runtime.settings.rlm_max_iters == 20
    assert runtime.lm is startup_lm
    assert runtime.program.sub_lm is startup_sub_lm
    assert runtime.decision_lm is startup_decision_lm

    file_path = "artifacts/report.txt"
    offline.store.add(db.File(path=file_path))
    download = await offline.client.get(f"{offline.base}/files/{file_path}")
    assert download.status_code == 200
    offline.sandbox.fs.download_file.assert_awaited_once_with(f"/workspace/{file_path}", 33)
