"""SessionLifecycle archive and retirement contracts."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from fleet_rlm.api.dependencies import get_session_prewarm
from fleet_rlm.app_services import RouteServices, RuntimeInventory
from fleet_rlm.sessions.lifecycle import NoOpSessionRetirement, SessionLifecycle
from fleet_rlm.sessions.models import SessionRetirementPendingError
from fleet_rlm.skills.models import SkillSelectionRef
from tests.support.in_memory_stores import InMemoryRunStateStore, InMemorySessionCatalog


class _RecordingRetirement:
    def __init__(self, *, fail: BaseException | None = None) -> None:
        self.calls: list[tuple[object, object]] = []
        self._fail = fail

    async def close_root_session(
        self,
        workspace_id,
        session_id,
        *,
        deadline=None,
    ) -> None:
        del deadline
        self.calls.append((workspace_id, session_id))
        if self._fail is not None:
            raise self._fail


class _BlockingTurnDrain:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def wait_for_session_idle(self, _workspace_id, _session_id, *, deadline) -> None:
        del deadline
        self.started.set()
        await self.release.wait()


@pytest.mark.asyncio
async def test_title_only_update_never_calls_retirement() -> None:
    store = InMemoryRunStateStore()
    catalog = InMemorySessionCatalog(store)
    user_id, workspace_id = uuid4(), uuid4()
    record = await catalog.create(user_id=user_id, workspace_id=workspace_id, title="keep")
    retirement = _RecordingRetirement()
    lifecycle = SessionLifecycle(catalog, retirement)

    updated = await lifecycle.update(
        record.id,
        user_id=user_id,
        workspace_id=workspace_id,
        title="renamed",
        status=None,
    )

    assert updated.title == "renamed"
    assert retirement.calls == []


@pytest.mark.asyncio
async def test_archive_commits_before_retiring_provider_root() -> None:
    store = InMemoryRunStateStore()
    catalog = InMemorySessionCatalog(store)
    user_id, workspace_id = uuid4(), uuid4()
    record = await catalog.create(user_id=user_id, workspace_id=workspace_id, title="archive-me")
    retirement = _RecordingRetirement()
    lifecycle = SessionLifecycle(catalog, retirement)

    updated = await lifecycle.update(
        record.id,
        user_id=user_id,
        workspace_id=workspace_id,
        title=None,
        status="archived",
    )

    assert updated.status == "archived"
    assert retirement.calls == [(workspace_id, record.id)]
    persisted = await catalog.get(record.id, user_id=user_id, workspace_id=workspace_id)
    assert persisted.status == "archived"


@pytest.mark.asyncio
async def test_archive_waits_for_active_turns_before_retiring_provider_root() -> None:
    store = InMemoryRunStateStore()
    catalog = InMemorySessionCatalog(store)
    user_id, workspace_id = uuid4(), uuid4()
    record = await catalog.create(user_id=user_id, workspace_id=workspace_id, title="archive-me")
    retirement = _RecordingRetirement()
    drain = _BlockingTurnDrain()
    lifecycle = SessionLifecycle(catalog, retirement, active_turn_drain=drain)

    update = asyncio.create_task(
        lifecycle.update(
            record.id,
            user_id=user_id,
            workspace_id=workspace_id,
            title=None,
            status="archived",
        )
    )
    await drain.started.wait()
    assert retirement.calls == []

    drain.release.set()
    _ = await update
    assert retirement.calls == [(workspace_id, record.id)]


@pytest.mark.asyncio
async def test_archive_raises_pending_when_retirement_fails() -> None:
    store = InMemoryRunStateStore()
    catalog = InMemorySessionCatalog(store)
    user_id, workspace_id = uuid4(), uuid4()
    record = await catalog.create(user_id=user_id, workspace_id=workspace_id, title="archive-me")
    lifecycle = SessionLifecycle(catalog, _RecordingRetirement(fail=RuntimeError("provider unavailable")))

    with pytest.raises(SessionRetirementPendingError) as raised:
        await lifecycle.update(
            record.id,
            user_id=user_id,
            workspace_id=workspace_id,
            title=None,
            status="archived",
        )

    assert raised.value.session_id == record.id
    persisted = await catalog.get(record.id, user_id=user_id, workspace_id=workspace_id)
    assert persisted.status == "archived"


@pytest.mark.asyncio
async def test_archive_propagates_cancellation() -> None:
    store = InMemoryRunStateStore()
    catalog = InMemorySessionCatalog(store)
    user_id, workspace_id = uuid4(), uuid4()
    record = await catalog.create(user_id=user_id, workspace_id=workspace_id, title="archive-me")
    lifecycle = SessionLifecycle(catalog, _RecordingRetirement(fail=asyncio.CancelledError()))

    with pytest.raises(asyncio.CancelledError):
        await lifecycle.update(
            record.id,
            user_id=user_id,
            workspace_id=workspace_id,
            title=None,
            status="archived",
        )


@pytest.mark.asyncio
async def test_noop_retirement_succeeds_for_testing_composition() -> None:
    store = InMemoryRunStateStore()
    catalog = InMemorySessionCatalog(store)
    user_id, workspace_id = uuid4(), uuid4()
    record = await catalog.create(user_id=user_id, workspace_id=workspace_id, title="archive-me")
    lifecycle = SessionLifecycle(catalog, NoOpSessionRetirement())

    updated = await lifecycle.update(
        record.id,
        user_id=user_id,
        workspace_id=workspace_id,
        title=None,
        status="archived",
    )

    assert updated.status == "archived"


def test_turn_input_codec_reads_v1_rows_from_the_canonical_baseline() -> None:
    from fleet_rlm.sessions.models import TurnInput, TurnInputCodec

    skill_id = UUID("00000000-0000-0000-0000-000000000002")
    current = TurnInput("inspect", skill_selections=(SkillSelectionRef(skill_id, "2.0.0"),))

    assert TurnInputCodec.decode(TurnInputCodec.encode(current)) == current
    assert TurnInputCodec.decode(
        {
            "schema_version": 1,
            "text": "legacy",
            "attachment_ids": [],
        }
    ) == TurnInput("legacy")


def test_turn_input_rejects_duplicate_or_oversized_skill_selections() -> None:
    from fleet_rlm.sessions.models import TurnInput, TurnInputValidationError

    selection = SkillSelectionRef(UUID(int=1), "1.0.0")

    with pytest.raises(TurnInputValidationError):
        TurnInput("inspect", skill_selections=(selection, selection))

    with pytest.raises(TurnInputValidationError):
        TurnInput(
            "inspect",
            skill_selections=tuple(SkillSelectionRef(UUID(int=index), "1.0.0") for index in range(1, 6)),
        )


def test_sequence_cursor_is_an_actual_nonnegative_sequence() -> None:
    from fleet_rlm.sessions.models import SequenceCursor

    assert SequenceCursor().after_sequence is None
    assert SequenceCursor(after_sequence=0).after_sequence == 0
    assert SequenceCursor(after_sequence=41).next_after_sequence(42) == 42

    with pytest.raises(ValueError):
        SequenceCursor(after_sequence=-1)


class _RecordingPrewarmManager:
    """Session-manager double recording prewarm scheduling."""

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

    def schedule_prewarm(self, session_id, user_id, workspace_id) -> asyncio.Task[None]:
        async def run_prewarm() -> None:
            try:
                await self.prewarm_session(
                    session_id,
                    user_id=user_id,
                    workspace_id=workspace_id,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                return

        return asyncio.create_task(run_prewarm(), name=f"fleet-session-prewarm-{session_id}")


class _Request:
    def __init__(self, app: object) -> None:
        self.app = app


class _App:
    def __init__(self, *, ready: bool, inventory: RuntimeInventory | None) -> None:
        self.state = SimpleNamespace(
            composition_ready=ready,
            runtime_inventory=inventory,
            route_services=inventory.route_services if inventory is not None else None,
        )


def _inventory(manager: _RecordingPrewarmManager | None) -> RuntimeInventory:
    routes = RouteServices(
        turn_runtime=object(),
        attachment_lifecycle=object(),
        artifact_reader=object(),
        session_catalog=object(),
        session_lifecycle=object(),
        config_policy=object(),
        workspace_volume_gateway=object(),
        workspace_file_service=object(),
        daytona_runtime=manager,
    )
    return RuntimeInventory(route_services=routes, daytona_runtime_owner=manager)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_prewarm_dependency_returns_manager_scheduler() -> None:
    manager = _RecordingPrewarmManager()
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
    manager = _RecordingPrewarmManager(fail=RuntimeError("provider unavailable"))
    request = _Request(_App(ready=True, inventory=_inventory(manager)))

    schedule = get_session_prewarm(request)  # type: ignore[arg-type]
    assert schedule is not None

    task = schedule(uuid4(), uuid4(), uuid4())
    await asyncio.wait_for(task, timeout=5)


@pytest.mark.asyncio
async def test_prewarm_absent_without_composed_manager() -> None:
    request = _Request(_App(ready=True, inventory=_inventory(None)))

    assert get_session_prewarm(request) is None  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_prewarm_absent_until_composition_ready() -> None:
    manager = _RecordingPrewarmManager()
    request = _Request(_App(ready=False, inventory=_inventory(manager)))

    with pytest.raises(HTTPException) as raised:
        get_session_prewarm(request)  # type: ignore[arg-type]
    assert raised.value.status_code == 503
