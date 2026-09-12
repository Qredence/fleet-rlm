"""Execute live-lane assertions locally; this is not PostgreSQL certification."""

from uuid import uuid4

import pytest
import pytest_asyncio

from fleet_rlm.persistence.database import create_async_engine_from_url, create_session_factory, create_tables
from fleet_rlm.persistence.repositories import SqlAlchemyRunStateStore, SqlAlchemySessionCatalog
from fleet_rlm.sessions.models import TurnAccess
from tests.live.backend.test_postgres_contention import (
    test_postgres_cancel_settlement_races_commit,
    test_postgres_concurrent_claims_have_one_owner,
    test_postgres_outbox_claims_are_disjoint,
    test_postgres_recovery_owner_cas_fences_stale_commit,
)

__all__ = [
    "test_postgres_cancel_settlement_races_commit",
    "test_postgres_concurrent_claims_have_one_owner",
    "test_postgres_outbox_claims_are_disjoint",
    "test_postgres_recovery_owner_cas_fences_stale_commit",
]

pytestmark = pytest.mark.asyncio


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
