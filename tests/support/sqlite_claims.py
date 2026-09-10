"""Shared scenario setup; not a collected test module."""

from uuid import uuid4

import pytest_asyncio

from fleet_rlm.persistence.database import create_async_engine_from_url, create_session_factory, create_tables
from fleet_rlm.persistence.repositories import SqlAlchemyRunStateStore, SqlAlchemySessionCatalog
from fleet_rlm.sessions.models import TurnAccess


@pytest_asyncio.fixture
async def postgres_claim_store(tmp_path):
    engine = create_async_engine_from_url(f"sqlite+aiosqlite:///{tmp_path / 'contention.db'}")
    try:
        await create_tables(engine)
        factory = create_session_factory(engine)
        access = TurnAccess(uuid4(), uuid4())
        record = await SqlAlchemySessionCatalog(factory).create(
            user_id=access.user_id, workspace_id=access.workspace_id, title="local-contention"
        )
        yield SqlAlchemyRunStateStore(factory), factory, access, record.id
    finally:
        await engine.dispose()
