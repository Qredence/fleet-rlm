from __future__ import annotations

from types import SimpleNamespace

import pytest

from dspy.primitives import CodeInterpreterError
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
        self.deleted_sessions: list[str] = []

    async def create_session(self, session_id: str) -> None:
        self.created_sessions.append(session_id)

    async def execute_session_command(self, session_id: str, request: object) -> None:
        self.commands.append((session_id, request))

    async def delete_session(self, session_id: str) -> None:
        self.deleted_sessions.append(session_id)


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


@pytest.mark.asyncio
async def test_daytona_bridge_retries_on_health_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Broker startup should retry and reset partial state between attempts."""
    sandbox = _FakeSandbox()
    bridge = DaytonaToolBridge(
        sandbox=sandbox,
        context=object(),
        broker_start_retries=2,
        broker_health_timeout=1.0,
    )

    health_attempts = 0

    async def _failing_then_success(self, timeout: float = 30.0) -> None:
        nonlocal health_attempts
        health_attempts += 1
        if health_attempts < 2:
            raise CodeInterpreterError("health check failed")

    monkeypatch.setattr(DaytonaToolBridge, "_await_health", _failing_then_success)

    await bridge.aensure_started()

    assert health_attempts == 2
    assert bridge._broker_url == "https://preview.daytona.test/3000"
    assert bridge._broker_token == "tok"
    assert bridge._broker_session_id is not None
    # The failed first session should be cleaned up.
    assert len(sandbox.process.deleted_sessions) == 1


@pytest.mark.asyncio
async def test_daytona_bridge_resets_state_after_all_retries_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If all broker startup retries fail, the bridge must not cache broken state."""
    sandbox = _FakeSandbox()
    bridge = DaytonaToolBridge(
        sandbox=sandbox,
        context=object(),
        broker_start_retries=1,
        broker_health_timeout=1.0,
    )

    monkeypatch.setattr(
        DaytonaToolBridge,
        "_await_health",
        lambda _self, timeout: (_ for _ in ()).throw(
            CodeInterpreterError("health check failed")
        ),
    )

    with pytest.raises(CodeInterpreterError):
        await bridge.aensure_started()

    # Partial state must be reset so the next call can retry from scratch.
    assert bridge._broker_url is None
    assert bridge._broker_token is None
    assert bridge._broker_session_id is None
    # Both created sessions should be deleted (one per attempt).
    assert len(sandbox.process.deleted_sessions) == 2
