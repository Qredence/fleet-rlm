"""Session sandbox pre-warm trigger contracts (API dependency layer).

POST /api/sessions schedules a fire-and-forget provider Sandbox pre-warm so
the first Turn reuses a warm binding. The trigger is absent when no session
manager is composed (private deterministic compositions) and rejects the
closed-contract 503 until composition is ready. Background failures are
suppressed by design: the first Turn acquires normally when no warm binding
exists.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from fleet_rlm.api.dependencies import get_session_prewarm
from fleet_rlm.composition.inventory import RuntimeInventory


class _RecordingManager:
    """Session-manager double recording prewarm and fence calls."""

    def __init__(self, *, fail: BaseException | None = None) -> None:
        self.calls: list[tuple[object, object, object]] = []
        self.fenced: list[object] = []
        self._fail = fail

    async def fence_session(self, session_id, *, deadline=None):
        del deadline
        self.fenced.append(session_id)
        return None

    async def prewarm_session(self, session_id, *, user_id, workspace_id, deadline=None) -> bool:
        del deadline
        self.calls.append((session_id, user_id, workspace_id))
        if self._fail is not None:
            raise self._fail
        return True


class _Request:
    """Starlette-shaped request double carrying app.state."""

    def __init__(self, app: object) -> None:
        self.app = app


class _App:
    def __init__(self, *, ready: bool, inventory: RuntimeInventory | None) -> None:
        self.state = SimpleNamespace(
            composition_ready=ready,
            runtime_inventory=inventory,
        )


def _inventory(manager: _RecordingManager | None) -> RuntimeInventory:
    resources = None if manager is None else SimpleNamespace(session_manager=manager)
    return RuntimeInventory(run_environment_resources=resources)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_prewarm_trigger_schedules_background_acquisition() -> None:
    manager = _RecordingManager()
    request = _Request(_App(ready=True, inventory=_inventory(manager)))

    schedule = get_session_prewarm(request)  # type: ignore[arg-type]
    assert schedule is not None

    session_id, user_id, workspace_id = uuid4(), uuid4(), uuid4()
    task = schedule(session_id, user_id, workspace_id)
    assert task.get_name() == f"fleet-session-prewarm-{session_id}"
    await asyncio.wait_for(task, timeout=5)
    assert manager.calls == [(session_id, user_id, workspace_id)]


@pytest.mark.asyncio
async def test_prewarm_failures_are_suppressed() -> None:
    manager = _RecordingManager(fail=RuntimeError("provider unavailable"))
    request = _Request(_App(ready=True, inventory=_inventory(manager)))

    schedule = get_session_prewarm(request)  # type: ignore[arg-type]
    assert schedule is not None

    task = schedule(uuid4(), uuid4(), uuid4())
    await asyncio.wait_for(task, timeout=5)  # must complete, not raise


@pytest.mark.asyncio
async def test_prewarm_absent_without_composed_manager() -> None:
    request = _Request(_App(ready=True, inventory=_inventory(None)))

    assert get_session_prewarm(request) is None  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_prewarm_absent_until_composition_ready() -> None:
    manager = _RecordingManager()
    request = _Request(_App(ready=False, inventory=_inventory(manager)))

    with pytest.raises(HTTPException) as raised:
        get_session_prewarm(request)  # type: ignore[arg-type]
    assert raised.value.status_code == 503
