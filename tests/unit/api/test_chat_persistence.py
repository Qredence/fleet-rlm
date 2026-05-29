from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest


@pytest.mark.asyncio
async def test_load_manifest_from_volume_falls_back_to_legacy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    from fleet_rlm.api.runtime_services.chat_persistence import load_manifest_from_volume
    from fleet_rlm.integrations.daytona.interpreter import DaytonaInterpreter

    reads: list[str] = []

    class FakeDaytonaSession:
        async def aread_file(self, path: str) -> str:
            reads.append(path)
            if path.endswith("/sessions/session-1/conversation.json"):
                raise FileNotFoundError(path)
            return json.dumps({"rev": 7, "state": {"history": []}})

    async def fake_get_session(self):
        return FakeDaytonaSession()

    monkeypatch.setattr(DaytonaInterpreter, "aget_session", fake_get_session)

    interpreter = DaytonaInterpreter.__new__(DaytonaInterpreter)
    interpreter.volume_mount_path = "/data"
    agent = SimpleNamespace(interpreter=interpreter)

    manifest = await load_manifest_from_volume(
        agent,
        "sessions/session-1/conversation.json",
        fallback_paths=["meta/workspaces/owner/users/workspace/react-session-session-1.json"],
    )

    assert manifest["rev"] == 7
    assert reads == [
        "/data/sessions/session-1/conversation.json",
        "/data/meta/workspaces/owner/users/workspace/react-session-session-1.json",
    ]


@pytest.mark.asyncio
async def test_save_manifest_to_volume_writes_phase_one_conversation_path(monkeypatch: pytest.MonkeyPatch) -> None:
    from fleet_rlm.api.runtime_services.chat_persistence import save_manifest_to_volume
    from fleet_rlm.integrations.daytona.interpreter import DaytonaInterpreter

    writes: list[tuple[str, str]] = []

    class FakeDaytonaSession:
        async def awrite_file(self, path: str, content: str) -> str:
            writes.append((path, content))
            return path

    async def fake_get_session(self):
        return FakeDaytonaSession()

    monkeypatch.setattr(DaytonaInterpreter, "aget_session", fake_get_session)

    interpreter = DaytonaInterpreter.__new__(DaytonaInterpreter)
    interpreter.volume_mount_path = "/data"
    agent = SimpleNamespace(interpreter=interpreter)

    saved_path = await save_manifest_to_volume(agent, "sessions/session-1/conversation.json", {"rev": 1})

    assert saved_path == "/data/sessions/session-1/conversation.json"
    assert writes[0][0] == "/data/sessions/session-1/conversation.json"
    assert json.loads(writes[0][1]) == {"rev": 1}


@pytest.mark.asyncio
async def test_manifest_volume_io_does_not_create_daytona_session_when_disallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fleet_rlm.api.runtime_services.chat_persistence import load_manifest_from_volume, save_manifest_to_volume
    from fleet_rlm.integrations.daytona.interpreter import DaytonaInterpreter

    async def forbidden_get_session(self):
        raise AssertionError("aget_session should not be called during cleanup persistence")

    async def forbidden_execute(*args: object, **kwargs: object):
        raise AssertionError("aexecute should not run when session creation is disallowed")

    monkeypatch.setattr(DaytonaInterpreter, "aget_session", forbidden_get_session)

    interpreter = DaytonaInterpreter.__new__(DaytonaInterpreter)
    interpreter.volume_mount_path = "/data"
    interpreter._workspace = SimpleNamespace(_session=None)
    interpreter.aexecute = forbidden_execute
    agent = SimpleNamespace(interpreter=interpreter)

    loaded = await load_manifest_from_volume(
        agent,
        "sessions/session-1/conversation.json",
        allow_session_create=False,
    )
    saved = await save_manifest_to_volume(
        agent,
        "sessions/session-1/conversation.json",
        {"rev": 1},
        allow_session_create=False,
    )

    assert loaded == {}
    assert saved is None


@pytest.mark.asyncio
async def test_persist_session_state_skips_volume_without_creating_cleanup_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fleet_rlm.api.dependencies import SessionCacheDeps
    from fleet_rlm.api.runtime_services.chat_persistence import persist_session_state
    from fleet_rlm.integrations.daytona.interpreter import DaytonaInterpreter

    async def forbidden_get_session(self):
        raise AssertionError("aget_session should not be called during cleanup persistence")

    async def forbidden_execute(*args: object, **kwargs: object):
        raise AssertionError("aexecute should not run when session creation is disallowed")

    monkeypatch.setattr(DaytonaInterpreter, "aget_session", forbidden_get_session)

    interpreter = DaytonaInterpreter.__new__(DaytonaInterpreter)
    interpreter.volume_mount_path = "/data"
    interpreter._workspace = SimpleNamespace(_session=None)
    interpreter.aexecute = forbidden_execute
    agent = SimpleNamespace(
        interpreter=interpreter,
        export_session_state=lambda: {"history": [], "documents": {}},
    )
    session_cache = SessionCacheDeps()
    session_record = {
        "session_id": "session-1",
        "key": "workspace:user:session-1",
        "session": {},
        "manifest": {"rev": 0},
    }

    await persist_session_state(
        session_cache=session_cache,
        agent=agent,
        session_record=session_record,
        active_manifest_path="sessions/session-1/conversation.json",
        active_run_db_id=None,
        interpreter=interpreter,
        repository=None,
        identity_rows=None,
        persistence_required=True,
        include_volume_save=True,
        allow_volume_session_create=False,
    )

    assert session_record["manifest"]["rev"] == 1
    assert session_cache.sessions["workspace:user:session-1"] is session_record


@pytest.mark.asyncio
async def test_manifest_volume_io_uses_existing_daytona_session_without_creating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fleet_rlm.api.runtime_services.chat_persistence import load_manifest_from_volume, save_manifest_to_volume
    from fleet_rlm.integrations.daytona.interpreter import DaytonaInterpreter

    class FakeDaytonaSession:
        def __init__(self) -> None:
            self.reads: list[str] = []
            self.writes: list[tuple[str, str]] = []

        async def aread_file(self, path: str) -> str:
            self.reads.append(path)
            return json.dumps({"rev": 2, "state": {"history": []}})

        async def awrite_file(self, path: str, content: str) -> str:
            self.writes.append((path, content))
            return path

    async def forbidden_get_session(self):
        raise AssertionError("aget_session should not be called when a session is already active")

    monkeypatch.setattr(DaytonaInterpreter, "aget_session", forbidden_get_session)

    session = FakeDaytonaSession()
    interpreter = DaytonaInterpreter.__new__(DaytonaInterpreter)
    interpreter.volume_mount_path = "/data"
    interpreter._workspace = SimpleNamespace(_session=session)
    agent = SimpleNamespace(interpreter=interpreter)

    loaded = await load_manifest_from_volume(
        agent,
        "sessions/session-1/conversation.json",
        allow_session_create=False,
    )
    saved = await save_manifest_to_volume(
        agent,
        "sessions/session-1/conversation.json",
        {"rev": 3},
        allow_session_create=False,
    )

    assert loaded["rev"] == 2
    assert session.reads == ["/data/sessions/session-1/conversation.json"]
    assert saved == "/data/sessions/session-1/conversation.json"
    assert json.loads(session.writes[0][1]) == {"rev": 3}


@pytest.mark.asyncio
async def test_disconnect_cleanup_disallows_volume_session_creation() -> None:
    from fleet_rlm.api.runtime_services.chat_persistence import handle_chat_disconnect

    calls: list[dict[str, Any]] = []

    async def local_persist(**kwargs: Any) -> None:
        calls.append(dict(kwargs))

    cancel_flag: dict[str, bool] = {}
    await handle_chat_disconnect(
        pending_receive_task=None,
        stream_task=None,
        cancel_flag=cancel_flag,
        local_persist=local_persist,
        lifecycle=None,
    )

    assert cancel_flag["cancelled"] is True
    assert calls == [{"include_volume_save": True, "allow_volume_session_create": False}]


@pytest.mark.asyncio
async def test_stream_error_cleanup_disallows_volume_session_creation() -> None:
    from fleet_rlm.api.routers.ws.transport import handle_chat_loop_exception

    calls: list[dict[str, Any]] = []
    sent: list[dict[str, Any]] = []

    class FakeWebSocket:
        async def send_json(self, payload: dict[str, Any]) -> None:
            sent.append(payload)

    async def local_persist(**kwargs: Any) -> None:
        calls.append(dict(kwargs))

    await handle_chat_loop_exception(
        websocket=FakeWebSocket(),
        exc=RuntimeError("startup failed"),
        pending_receive_task=None,
        stream_task=None,
        local_persist=local_persist,
        lifecycle=None,
    )

    assert sent[0]["type"] == "error"
    assert calls == [{"include_volume_save": True, "allow_volume_session_create": False}]


@pytest.mark.asyncio
async def test_ensure_session_volume_layout_creates_scratchpad_and_workspace_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fleet_rlm.api.runtime_services.chat_persistence import ensure_session_volume_layout
    from fleet_rlm.integrations.daytona.interpreter import DaytonaInterpreter

    commands: list[str] = []

    class FakeDaytonaSession:
        workspace_path = "/workspace/repo"
        sandbox = SimpleNamespace(process=SimpleNamespace(exec=lambda command: commands.append(command)))

    async def fake_get_session(self):
        return FakeDaytonaSession()

    monkeypatch.setattr(DaytonaInterpreter, "aget_session", fake_get_session)

    interpreter = DaytonaInterpreter.__new__(DaytonaInterpreter)
    interpreter.volume_mount_path = "/data"
    agent = SimpleNamespace(interpreter=interpreter)

    layout = await ensure_session_volume_layout(agent, "session-1")

    assert layout == {
        "scratchpad_path": "/data/sessions/session-1/scratchpad",
        "workspace_link_path": "/data/sessions/session-1/workspace",
    }
    assert commands == [
        "mkdir -p /data/sessions/session-1/scratchpad && rm -rf /data/sessions/session-1/workspace && ln -s /workspace/repo /data/sessions/session-1/workspace"
    ]
