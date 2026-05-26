from __future__ import annotations

import json
from types import SimpleNamespace

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
