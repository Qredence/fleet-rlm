from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from fleet_rlm.api.dependencies import SessionCacheDeps


@pytest.mark.asyncio
async def test_switch_session_restores_phase_one_conversation_from_recreated_volume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fleet_rlm.api.routers.ws import session as ws_session
    from fleet_rlm.integrations.daytona.interpreter import DaytonaInterpreter

    reads: list[str] = []
    commands: list[str] = []
    restored_states: list[dict[str, Any]] = []
    persisted: list[dict[str, Any]] = []

    class FakeDaytonaSession:
        workspace_path = "/workspace/repo"
        sandbox = SimpleNamespace(process=SimpleNamespace(exec=lambda command: commands.append(command)))

        async def aread_file(self, path: str) -> str:
            reads.append(path)
            return json.dumps(
                {
                    "metadata": {"source": "phase-one"},
                    "state": {"history": [{"role": "user", "content": "hello"}]},
                }
            )

    async def fake_get_session(self):
        return FakeDaytonaSession()

    async def fake_link_database_session(**kwargs):
        return None

    monkeypatch.setattr(DaytonaInterpreter, "aget_session", fake_get_session)
    monkeypatch.setattr(ws_session, "_link_database_session", fake_link_database_session)

    async def fake_import_session_state(state: dict[str, Any]) -> None:
        restored_states.append(state)

    async def fake_reset(*, clear_sandbox_buffers: bool) -> None:
        restored_states.append({"reset": clear_sandbox_buffers})

    interpreter = DaytonaInterpreter.__new__(DaytonaInterpreter)
    interpreter.volume_mount_path = "/data"
    agent = SimpleNamespace(
        interpreter=interpreter,
        aimport_session_state=fake_import_session_state,
        areset=fake_reset,
    )

    async def local_persist(**kwargs: Any) -> None:
        persisted.append(kwargs)

    key, manifest_path, cached, last_docs_path, context = await ws_session.switch_session_if_needed(
        session_cache=SessionCacheDeps(),
        agent=agent,
        interpreter=interpreter,
        workspace_id="workspace-1",
        user_id="user-1",
        sess_id="session-1",
        owner_tenant_claim="tenant-1",
        owner_user_claim="user-1",
        active_key=None,
        session_record=None,
        last_loaded_docs_path="docs.md",
        local_persist=local_persist,
    )

    assert manifest_path == "sessions/session-1/conversation.json"
    assert reads == ["/data/sessions/session-1/conversation.json"]
    assert restored_states == [{"history": [{"role": "user", "content": "hello"}]}]
    assert cached["manifest"]["metadata"]["source"] == "phase-one"
    assert cached["manifest"]["metadata"]["scratchpad_path"] == "/data/sessions/session-1/scratchpad"
    assert cached["manifest"]["metadata"]["workspace_link_path"] == "/data/sessions/session-1/workspace"
    assert commands == [
        "mkdir -p /data/sessions/session-1/scratchpad && rm -rf /data/sessions/session-1/workspace && ln -s /workspace/repo /data/sessions/session-1/workspace"
    ]
    assert key.endswith(":session-1")
    assert last_docs_path is None
    assert context.session_id == "session-1"
    assert persisted == []
