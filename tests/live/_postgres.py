"""Shared scenario setup; not a collected test module."""

import os
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, text

from fleet_rlm.persistence.database import (
    check_database_compatibility,
    create_async_engine_from_url,
    create_session_factory,
)
from fleet_rlm.persistence.models import SessionRow, UserRow, WorkspaceRow
from fleet_rlm.persistence.repositories import SqlAlchemyRunStateStore, SqlAlchemySessionCatalog
from fleet_rlm.sessions.models import TurnAccess


@pytest_asyncio.fixture
async def postgres_claim_store(record_testsuite_property):
    if os.environ.get("FLEET_LIVE", "").strip() not in {"1", "true", "yes"}:
        pytest.skip("Set FLEET_LIVE=1 to authorize live PostgreSQL tests")
    url = os.environ.get("FLEET_DATABASE_URL", "")
    if not url.startswith(("postgres://", "postgresql")):
        pytest.skip("Export a PostgreSQL FLEET_DATABASE_URL")
    await check_database_compatibility(url)
    engine = create_async_engine_from_url(url)
    factory = create_session_factory(engine)
    access = TurnAccess(uuid4(), uuid4())
    session_id = None
    try:
        async with engine.connect() as connection:
            version = await connection.scalar(text("SHOW server_version_num"))
            heads = (await connection.execute(text("SELECT version_num FROM alembic_version"))).scalars().all()
        record_testsuite_property("fleet.postgres.server_version_num", str(version))
        record_testsuite_property("fleet.postgres.alembic_heads", ",".join(sorted(heads)))
        record = await SqlAlchemySessionCatalog(factory).create(
            user_id=access.user_id, workspace_id=access.workspace_id, title="claim-contention"
        )
        session_id = record.id
        store = SqlAlchemyRunStateStore(factory)
        yield store, factory, access, session_id
    finally:
        try:
            async with factory() as db, db.begin():
                if session_id is not None:
                    await db.execute(delete(SessionRow).where(SessionRow.id == session_id))
                await db.execute(delete(WorkspaceRow).where(WorkspaceRow.id == access.workspace_id))
                await db.execute(delete(UserRow).where(UserRow.id == access.user_id))
        finally:
            await engine.dispose()
