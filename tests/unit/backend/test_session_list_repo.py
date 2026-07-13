"""SQL Session Catalog queries (offline SQLite)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from fleet_rlm.persistence.database import (
    create_async_engine_from_url,
    create_session_factory,
    create_tables,
)
from fleet_rlm.persistence.repositories import SqlAlchemySessionCatalog
from fleet_rlm.sessions.errors import SessionNotFoundError


async def _repo():
    engine = create_async_engine_from_url("sqlite+aiosqlite:///:memory:")
    await create_tables(engine)
    return SqlAlchemySessionCatalog(create_session_factory(engine)), engine


@pytest.mark.asyncio
async def test_list_filters_by_owner_and_status() -> None:
    repo, engine = await _repo()
    user, ws = uuid4(), uuid4()
    other_ws = uuid4()
    a = await repo.create(user_id=user, workspace_id=ws, title="Alpha chat")
    await repo.create(user_id=user, workspace_id=ws, title="Beta notes")
    await repo.create(user_id=user, workspace_id=other_ws, title="Other workspace")
    await repo.archive(a.id, user_id=user, workspace_id=ws)

    active = await repo.list(user_id=user, workspace_id=ws, status="active", search=None, limit=10, offset=0)
    assert active.total == 1
    assert active.items[0].title == "Beta notes"

    archived = await repo.list(user_id=user, workspace_id=ws, status="archived", search=None, limit=10, offset=0)
    assert archived.total == 1
    assert archived.items[0].id == a.id

    searched = await repo.list(user_id=user, workspace_id=ws, status=None, search="beta", limit=10, offset=0)
    assert searched.total == 1
    assert searched.items[0].title == "Beta notes"

    await engine.dispose()


@pytest.mark.asyncio
async def test_get_owned_hides_foreign_workspace() -> None:
    repo, engine = await _repo()
    user, ws_a, ws_b = uuid4(), uuid4(), uuid4()
    created = await repo.create(user_id=user, workspace_id=ws_a, title="mine")
    with pytest.raises(SessionNotFoundError):
        await repo.get(created.id, user_id=user, workspace_id=ws_b)
    got = await repo.get(created.id, user_id=user, workspace_id=ws_a)
    assert got.id == created.id
    await engine.dispose()
