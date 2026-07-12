"""SessionRepository list/update/archive/turns (offline sqlite)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from fleet_rlm_clean.persistence.database import (
    create_async_engine_from_url,
    create_session_factory,
    create_tables,
)
from fleet_rlm_clean.sessions.errors import SessionNotFoundError
from fleet_rlm_clean.sessions.repository import SessionRepository


async def _repo():
    engine = create_async_engine_from_url("sqlite+aiosqlite:///:memory:")
    await create_tables(engine)
    return SessionRepository(create_session_factory(engine)), engine


@pytest.mark.asyncio
async def test_list_filters_by_owner_and_status() -> None:
    repo, engine = await _repo()
    user, ws = uuid4(), uuid4()
    other_ws = uuid4()
    a = await repo.create(user_id=user, workspace_id=ws, title="Alpha chat")
    await repo.create(user_id=user, workspace_id=ws, title="Beta notes")
    await repo.create(user_id=user, workspace_id=other_ws, title="Other workspace")
    await repo.archive(a.id, user_id=user, workspace_id=ws)

    active, total_active = await repo.list(
        user_id=user, workspace_id=ws, status="active", limit=10, offset=0
    )
    assert total_active == 1
    assert active[0].title == "Beta notes"

    archived, total_arch = await repo.list(
        user_id=user, workspace_id=ws, status="archived", limit=10, offset=0
    )
    assert total_arch == 1
    assert archived[0].id == a.id

    searched, total_s = await repo.list(
        user_id=user, workspace_id=ws, search="beta", limit=10, offset=0
    )
    assert total_s == 1
    assert searched[0].title == "Beta notes"

    await engine.dispose()


@pytest.mark.asyncio
async def test_get_owned_hides_foreign_workspace() -> None:
    repo, engine = await _repo()
    user, ws_a, ws_b = uuid4(), uuid4(), uuid4()
    created = await repo.create(user_id=user, workspace_id=ws_a, title="mine")
    with pytest.raises(SessionNotFoundError):
        await repo.get_owned(created.id, user_id=user, workspace_id=ws_b)
    got = await repo.get_owned(created.id, user_id=user, workspace_id=ws_a)
    assert got.id == created.id
    await engine.dispose()


@pytest.mark.asyncio
async def test_list_turns_paginated() -> None:
    repo, engine = await _repo()
    user, ws = uuid4(), uuid4()
    session = await repo.create(user_id=user, workspace_id=ws, title="t")
    claim = await repo.claim_turn(session.id)
    await repo.append_completed_exchange(
        session.id,
        user_text="hello",
        assistant_text="world",
        run_id=claim.run_id,
        expected_checkpoint_version=0,
    )
    turns, total = await repo.list_turns(
        session.id, user_id=user, workspace_id=ws, limit=10, offset=0
    )
    assert total == 2
    assert turns[0].role == "user"
    assert turns[0].content == "hello"
    assert turns[1].role == "assistant"
    assert turns[1].content == "world"
    await engine.dispose()
