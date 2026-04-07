from __future__ import annotations

from types import SimpleNamespace

import pytest

from fleet_rlm.integrations.daytona.bridge import DaytonaToolBridge


class _FakeFs:
    def __init__(self) -> None:
        self.uploads: list[tuple[bytes, str]] = []

    async def upload_file(self, data: bytes, path: str) -> None:
        self.uploads.append((bytes(data), path))


class _FakeProcess:
    def __init__(self) -> None:
        self.created_sessions: list[str] = []
        self.commands: list[tuple[str, object]] = []

    async def create_session(self, session_id: str) -> None:
        self.created_sessions.append(session_id)

    async def execute_session_command(self, session_id: str, request: object) -> None:
        self.commands.append((session_id, request))


class _FakeSandbox:
    def __init__(self) -> None:
        self.fs = _FakeFs()
        self.process = _FakeProcess()

    async def get_preview_link(self, port: int) -> object:
        return SimpleNamespace(url=f"https://preview.daytona.test/{port}", token="tok")


@pytest.mark.asyncio
async def test_daytona_bridge_awaits_async_preview_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _skip_health(self, timeout: float = 30.0) -> None:
        _ = timeout

    monkeypatch.setattr(DaytonaToolBridge, "_await_health", _skip_health)

    bridge = DaytonaToolBridge(sandbox=_FakeSandbox(), context=object())

    await bridge.aensure_started()

    assert bridge._broker_url == "https://preview.daytona.test/3000"
    assert bridge._broker_token == "tok"
    assert bridge._broker_session_id is not None
