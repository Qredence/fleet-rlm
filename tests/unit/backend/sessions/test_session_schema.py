"""impl-05: foundation session schema and repository (no live providers)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from fleet_rlm.persistence.database import (
    create_async_engine_from_url,
    create_session_factory,
    create_tables,
    normalize_database_url,
)
from fleet_rlm.persistence.repositories import SqlAlchemySessionCatalog
from fleet_rlm.sessions.errors import SessionNotFoundError


def test_normalize_database_url_drops_libpq_channel_binding_for_asyncpg() -> None:
    normalized = normalize_database_url(
        "postgresql+asyncpg://user:password@example.test/fleet?sslmode=require&channel_binding=require",
    )

    assert normalized == "postgresql+asyncpg://user:password@example.test/fleet?ssl=require"


@pytest.mark.asyncio
async def test_empty_database_boots_and_session_round_trip() -> None:
    engine = create_async_engine_from_url("sqlite+aiosqlite:///:memory:")
    await create_tables(engine)
    factory = create_session_factory(engine)
    repo = SqlAlchemySessionCatalog(factory)

    user_id = uuid4()
    workspace_id = uuid4()
    created = await repo.create(user_id=user_id, workspace_id=workspace_id, title="t1")
    loaded = await repo.get(created.id, user_id=user_id, workspace_id=workspace_id)

    assert loaded.id == created.id
    assert loaded.user_id == user_id
    assert loaded.workspace_id == workspace_id
    assert loaded.checkpoint_version == 0

    await engine.dispose()


@pytest.mark.asyncio
async def test_load_missing_session_raises() -> None:
    engine = create_async_engine_from_url("sqlite+aiosqlite:///:memory:")
    await create_tables(engine)
    repo = SqlAlchemySessionCatalog(create_session_factory(engine))

    with pytest.raises(SessionNotFoundError):
        await repo.get(uuid4(), user_id=uuid4(), workspace_id=uuid4())

    await engine.dispose()
