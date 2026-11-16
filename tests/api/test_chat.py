from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fleet_rlm.api.deps import get_store
from fleet_rlm.api.routes import chat
from fleet_rlm.api.routes.chat import router


class ActivityStore:
    def __init__(self, saved: bool = True) -> None:
        self.saved = saved
        self.call: tuple[str, str, list[dict[str, object]]] | None = None

    def save_message_activity(self, session_id: str, message_id: str, activity: list[dict[str, object]]) -> bool:
        self.call = (session_id, message_id, activity)
        return self.saved


def client_for(store: Any, *, settings: Any = None) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.state.settings = settings or SimpleNamespace(
        mlflow_mode="local", mlflow_descriptor=lambda: {"tracking_uri": "http://mlflow.test"}, max_upload_bytes=4
    )
    app.state.mlflow_experiment_id = "experiment"
    app.dependency_overrides[get_store] = lambda: store
    return TestClient(app)


def test_activity_is_saved_on_completed_assistant_message():
    store = ActivityStore()
    activity = [{"type": "data-fleetActivity", "id": "route", "data": {"kind": "route"}}]

    response = client_for(store).post(
        "/sessions/session-a/messages/turn-1_assistant/activity", json={"activity": activity}
    )

    assert response.status_code == 204
    assert store.call == ("session-a", "turn-1_assistant", activity)


def test_activity_rejects_missing_or_invalid_message_and_oversized_payloads():
    assert (
        client_for(ActivityStore(saved=False))
        .post("/sessions/session-a/messages/turn-1_assistant/activity", json={"activity": []})
        .status_code
        == 404
    )
    client = client_for(ActivityStore())
    assert (
        client.post(
            "/sessions/session-a/messages/turn-1_assistant/activity", json={"activity": [{"type": "text"}]}
        ).status_code
        == 422
    )
    oversized = [{"type": "data-fleetActivity", "id": "x", "data": {"detail": "x" * (512 * 1024)}}]
    assert (
        client.post("/sessions/session-a/messages/turn-1_assistant/activity", json={"activity": oversized}).status_code
        == 422
    )


@pytest.mark.parametrize("mode", ["local", "databricks", "unconfigured"])
def test_message_pages_preserve_cursor_and_build_safe_trace_links(mode):
    rows = [
        dict(
            session_id="session-a",
            message_id="message",
            turn_id="turn",
            position=1,
            role="assistant",
            content=[{"type": "text", "text": "saved"}],
            seq=3,
            status="completed",
            trace_id="tr:/trace",
            created_at="2026-11-16T12:00:00Z",
        )
    ]
    calls = []

    def messages(session_id, **kwargs):
        calls.append((session_id, kwargs))
        return rows

    settings = SimpleNamespace(
        mlflow_mode=mode,
        mlflow_descriptor=lambda: (
            {}
            if mode == "unconfigured"
            else {"tracking_uri": "http://mlflow.test", "workspace_host": "https://workspace.test"}
        ),
    )
    client = client_for(SimpleNamespace(messages=messages), settings=settings)
    response = client.get("/sessions/session-a/messages?before=9&limit=2")
    assert response.status_code == 200
    page = response.json()
    assert page["next_before"] == 3
    assert calls == [("session-a", {"before": 9, "limit": 2})]
    link = page["messages"][0]["trace_url"]
    if mode == "unconfigured":
        assert link is None
    else:
        base = "https://workspace.test/ml" if mode == "databricks" else "http://mlflow.test/#"
        assert link == base + "/experiments/experiment/traces?selectedTraceId=tr%3A%2Ftrace"
    rows.clear()
    assert client.get("/sessions/session-a/messages").json() == {"messages": [], "next_before": None}
    assert client.get("/sessions/session-a/messages?limit=0").status_code == 422


def test_published_download_checks_session_ownership_and_inventory_is_passive(monkeypatch):
    record = dict(
        file_id="file-a",
        origin="upload",
        filename="report.txt",
        media_type="text/plain",
        size_bytes=4,
        sha256="a" * 64,
        workspace_path="files/file-a/report.txt",
    )
    calls = []

    def download(file):
        calls.append(file)
        return "https://objects.test/download?signature=test"

    monkeypatch.setattr(chat, "ObjectStorage", lambda _settings: SimpleNamespace(download_url=download))
    private = {**record, "bucket": "private", "object_key": "sessions/session-a/file-a"}
    store = SimpleNamespace(
        published_files=lambda _sid: [record],
        published_file=lambda sid, fid: private if (sid, fid) == ("session-a", "file-a") else None,
        workspace_inventory=lambda _sid: {"indexed_at": None, "files": []},
    )
    client = client_for(store)
    assert client.get("/sessions/session-a/published-files").json()["files"][0]["file_id"] == "file-a"
    response = client.get("/sessions/session-a/published-files/file-a", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "https://objects.test/download?signature=test"
    assert client.get("/sessions/other-session/published-files/file-a").status_code == 404
    assert calls == [private]
    assert client.get("/sessions/session-a/workspace/index").json() == {"indexed_at": None, "files": []}


@pytest.mark.parametrize("fault", [None, "oversized", "invalid_name", "persistence"])
def test_attachment_publication_bounds_uploads_and_removes_temporary_bytes(monkeypatch, fault):
    paths, saved = [], []

    def upload(session_id, path, **kwargs):
        assert session_id == "session-a" and path.read_bytes() == b"data"
        assert kwargs == {"filename": "report.txt", "origin": "upload"}
        paths.append(path)
        return dict(
            file_id="file-a",
            origin="upload",
            filename="report.txt",
            media_type="text/plain",
            size_bytes=4,
            sha256="a" * 64,
            workspace_path="files/file-a/report.txt",
            bucket="private",
            object_key="private-key",
        )

    def save(session_id, record):
        if fault == "persistence":
            raise RuntimeError("private storage detail")
        saved.append((session_id, record))

    monkeypatch.setattr(chat, "ObjectStorage", lambda _settings: SimpleNamespace(upload=upload))
    client = client_for(SimpleNamespace(save_upload=save))
    name = ".." if fault == "invalid_name" else "../report.txt"
    response = client.post(
        "/sessions/session-a/attachments", files={"file": (name, b"extra" if fault == "oversized" else b"data")}
    )
    expected = {None: 200, "oversized": 413, "invalid_name": 422, "persistence": 503}[fault]
    assert response.status_code == expected
    assert all(not path.exists() for path in paths)
    assert "private-key" not in response.text and "private storage detail" not in response.text
    if fault is None:
        assert response.json()["filename"] == "report.txt"
        assert "bucket" not in response.json() and "object_key" not in response.json()
        assert saved[0][0] == "session-a"
    else:
        assert not saved


def test_observer_emits_changes_then_keepalive_and_disconnects_without_cancelling(monkeypatch):
    reads = []

    def messages(_session_id):
        reads.append("messages")
        return []

    store = SimpleNamespace(
        messages=messages,
        published_files=lambda _sid: [],
        workspace_inventory=lambda _sid: {"indexed_at": None, "files": []},
        runtime_settings=lambda _sid: {"mode": "direct"},
    )
    client = client_for(store)
    checks = iter([False, False, True])

    async def disconnected():
        return next(checks)

    async def advance(_seconds):
        pass

    monkeypatch.setattr(chat.asyncio, "sleep", advance)
    request = SimpleNamespace(app=client.app, is_disconnected=disconnected)

    async def observe():
        events = [event async for event in chat.updates("session-a", request, store, client.app.state.settings)]
        assert len(events) == 2
        assert events[0].event == "state"
        assert json.loads(events[0].raw_data)["settings"] == {"mode": "direct"}
        assert events[1].comment == "keepalive"

    asyncio.run(observe())
    assert reads == ["messages", "messages"]
