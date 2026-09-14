"""SessionLifecycle archive and retirement contracts."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from fleet_rlm.persistence.repositories import InMemoryRunStateStore, InMemorySessionCatalog
from fleet_rlm.sessions.errors import SessionRetirementPendingError
from fleet_rlm.sessions.lifecycle import NoOpSessionRetirement, SessionLifecycle


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
