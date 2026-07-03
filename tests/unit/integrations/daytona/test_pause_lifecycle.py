"""Phase 2.5: pause lifecycle for root sessions."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from fleet_rlm.integrations.daytona import concurrency
from fleet_rlm.integrations.daytona.concurrency import (
    SessionLifecycleConfig,
    get_paused_sandbox_count,
    reap_paused_sandboxes,
    register_paused_sandbox,
    should_pause_root_session,
    unregister_paused_sandbox,
)


@pytest.fixture(autouse=True)
def _reset_paused_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear the paused-sandbox registry between tests."""
    monkeypatch.setattr(concurrency, "_PAUSED_REGISTRY", {})
    # Also reset the dict object in place for code that captured the ref.
    concurrency._PAUSED_REGISTRY.clear()
    monkeypatch.delenv("FLEET_SESSION_LIFECYCLE", raising=False)
    monkeypatch.delenv("FLEET_MAX_PAUSED_SANDBOXES", raising=False)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def test_config_defaults_to_delete(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FLEET_SESSION_LIFECYCLE", raising=False)
    cfg = SessionLifecycleConfig.from_env()
    assert cfg.lifecycle == "delete"
    assert cfg.max_paused_sandboxes == 3
    assert should_pause_root_session() is False


def test_config_pause_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLEET_SESSION_LIFECYCLE", "pause")
    monkeypatch.setenv("FLEET_MAX_PAUSED_SANDBOXES", "5")
    cfg = SessionLifecycleConfig.from_env()
    assert cfg.lifecycle == "pause"
    assert cfg.max_paused_sandboxes == 5
    assert should_pause_root_session() is True


def test_config_invalid_lifecycle_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLEET_SESSION_LIFECYCLE", "hibernate")
    cfg = SessionLifecycleConfig.from_env()
    assert cfg.lifecycle == "delete"
    assert should_pause_root_session() is False


def test_config_invalid_max_paused_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLEET_MAX_PAUSED_SANDBOXES", "not-a-number")
    cfg = SessionLifecycleConfig.from_env()
    assert cfg.max_paused_sandboxes == 3


def test_config_clamps_max_paused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLEET_MAX_PAUSED_SANDBOXES", "999")
    assert SessionLifecycleConfig.from_env().max_paused_sandboxes == 50

    monkeypatch.setenv("FLEET_MAX_PAUSED_SANDBOXES", "-1")
    assert SessionLifecycleConfig.from_env().max_paused_sandboxes == 0


# ---------------------------------------------------------------------------
# Registry + LRU reaper
# ---------------------------------------------------------------------------


class _FakeAsyncClient:
    """Minimal async client double for the reaper."""

    def __init__(self) -> None:
        self.deleted_ids: list[str] = []
        self._sandboxes: dict[str, Any] = {}

    def add(self, sandbox_id: str) -> Any:
        sb = type("Sbx", (), {"id": sandbox_id})()
        self._sandboxes[sandbox_id] = sb
        return sb

    async def get(self, sandbox_id: str) -> Any:
        return self._sandboxes[sandbox_id]

    async def delete(self, sandbox: Any) -> None:
        self.deleted_ids.append(sandbox.id)


class _FakeRuntime:
    def __init__(self) -> None:
        self._async_client = _FakeAsyncClient()
        self.DEFAULT_LABELS = {"managed-by": "fleet-rlm"}

    def _get_async_client(self) -> Any:
        return self._async_client


@pytest.mark.asyncio
async def test_reap_deletes_oldest_over_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """The reaper must delete the oldest paused sandboxes when over limit."""
    monkeypatch.setenv("FLEET_SESSION_LIFECYCLE", "pause")
    monkeypatch.setenv("FLEET_MAX_PAUSED_SANDBOXES", "2")
    runtime = _FakeRuntime()
    # Register 4 paused sandboxes; only the 2 newest should survive.
    for sid in ("old-1", "old-2", "new-1", "new-2"):
        runtime._async_client.add(sid)
        register_paused_sandbox(sid)
        # Stagger timestamps so LRU order is deterministic.
        await asyncio.sleep(0.001)

    reaped = await reap_paused_sandboxes(runtime=runtime)

    assert reaped == 2
    assert runtime._async_client.deleted_ids == ["old-1", "old-2"]
    assert get_paused_sandbox_count() == 2


@pytest.mark.asyncio
async def test_reap_under_limit_deletes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLEET_MAX_PAUSED_SANDBOXES", "5")
    runtime = _FakeRuntime()
    runtime._async_client.add("only-1")
    register_paused_sandbox("only-1")

    reaped = await reap_paused_sandboxes(runtime=runtime)

    assert reaped == 0
    assert get_paused_sandbox_count() == 1


@pytest.mark.asyncio
async def test_reap_zero_limit_deletes_all(monkeypatch: pytest.MonkeyPatch) -> None:
    """When max_paused=0, pausing is effectively disabled: all tracked are reaped."""
    monkeypatch.setenv("FLEET_MAX_PAUSED_SANDBOXES", "0")
    runtime = _FakeRuntime()
    for sid in ("a", "b"):
        runtime._async_client.add(sid)
        register_paused_sandbox(sid)

    reaped = await reap_paused_sandboxes(runtime=runtime)

    assert reaped == 2
    assert get_paused_sandbox_count() == 0


def test_unregister_removes_from_registry() -> None:
    register_paused_sandbox("sbx-1")
    assert get_paused_sandbox_count() == 1
    unregister_paused_sandbox("sbx-1")
    assert get_paused_sandbox_count() == 0
    # Unregistering a missing id is a no-op.
    unregister_paused_sandbox("never-existed")
    assert get_paused_sandbox_count() == 0
