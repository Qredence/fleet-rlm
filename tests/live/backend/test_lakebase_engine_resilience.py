"""Opt-in live proof: backend engine resilience against real Lakebase Postgres.

Gate: FLEET_LIVE=1

(1) check_database_compatibility passes against the configured instance.
(2) create_async_engine_from_url applies the Postgres pool policy.
(3) A DML round-trip through create_session_factory proves the pooled runtime
    write path end to end.
"""

from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest
from dotenv import load_dotenv
from sqlalchemy import text

from fleet_rlm.persistence.database import (
    _POSTGRES_POOL_PRE_PING,
    _POSTGRES_POOL_RECYCLE_SECONDS,
    check_database_compatibility,
    create_async_engine_from_url,
    create_session_factory,
)
from fleet_rlm.persistence.repositories import SqlAlchemySessionCatalog

pytestmark = [pytest.mark.db]

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _live_enabled() -> bool:
    return os.environ.get("FLEET_LIVE", "").strip() in {"1", "true", "yes"}


def _load_repo_env() -> None:
    """Load repo ``.env`` into the process without overriding exported values."""
    load_dotenv(_REPO_ROOT / ".env", override=False)


def _skip_unless_live_postgres() -> str:
    if not _live_enabled():
        pytest.skip("Set FLEET_LIVE=1 for live Lakebase engine resilience tests")
    url = os.environ.get("FLEET_DATABASE_URL") or ""
    if not url:
        pytest.skip("FLEET_DATABASE_URL not configured")
    if not (url.startswith("postgres://") or url.startswith("postgresql")):
        pytest.skip("FLEET_DATABASE_URL is not a Postgres URL")
    return url


@pytest.mark.asyncio
async def test_lakebase_database_at_alembic_head() -> None:
    _load_repo_env()
    url = _skip_unless_live_postgres()
    await check_database_compatibility(url)


@pytest.mark.asyncio
async def test_lakebase_engine_applies_pool_policy() -> None:
    _load_repo_env()
    url = _skip_unless_live_postgres()
    engine = create_async_engine_from_url(url)
    try:
        pool = engine.sync_engine.pool
        assert pool._pre_ping is _POSTGRES_POOL_PRE_PING
        assert pool._recycle == _POSTGRES_POOL_RECYCLE_SECONDS
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_lakebase_pooled_dml_round_trip() -> None:
    _load_repo_env()
    url = _skip_unless_live_postgres()
    engine = create_async_engine_from_url(url)
    factory = create_session_factory(engine)
    user_id = uuid4()
    subject = f"fleet-live-engine-{user_id.hex}"
    inserted = False
    try:
        async with factory() as session:
            assert (await session.execute(text("SELECT 1"))).scalar() == 1

        async with engine.begin() as conn:
            await conn.execute(
                text("INSERT INTO fleet_users (id, external_subject) VALUES (:id, :subject)"),
                {"id": user_id, "subject": subject},
            )
            inserted = True

        async with factory() as session:
            fetched = (
                await session.execute(
                    text("SELECT external_subject FROM fleet_users WHERE id = :id"),
                    {"id": user_id},
                )
            ).scalar()
            assert fetched == subject

        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM fleet_users WHERE id = :id"), {"id": user_id})
            inserted = False

        async with factory() as session:
            remaining = (
                await session.execute(
                    text("SELECT count(*) FROM fleet_users WHERE id = :id"),
                    {"id": user_id},
                )
            ).scalar()
            assert remaining == 0
    finally:
        # Remove the fixture row even if an assertion or the network failed
        # mid-test so the shared dev instance does not accumulate orphans; a
        # single PK-scoped DELETE in one transaction keeps cleanup atomic.
        if inserted:
            async with engine.begin() as conn:
                await conn.execute(text("DELETE FROM fleet_users WHERE id = :id"), {"id": user_id})
        await engine.dispose()


@pytest.mark.asyncio
async def test_lakebase_create_session_seeds_parents_before_child() -> None:
    """Repro for the Lakebase FK violation on ``POST /api/sessions``.

    ``SqlAlchemySessionCatalog.create()`` previously added the parent user /
    workspace rows and the dependent session row, then flushed once. Against a
    fresh/re-pinged Lakebase connection the child INSERT could be issued before
    the parents were visible to ``fleet_sessions_workspace_id_fkey``. The fix
    flushes parents first; this test proves the create path end to end.
    """
    _load_repo_env()
    url = _skip_unless_live_postgres()
    engine = create_async_engine_from_url(url)
    repo = SqlAlchemySessionCatalog(create_session_factory(engine))
    user_id = uuid4()
    workspace_id = uuid4()
    session_id = None
    try:
        record = await repo.create(user_id=user_id, workspace_id=workspace_id, title="live-fk-repro")
        session_id = record.id
        fetched = await repo.get(session_id, user_id=user_id, workspace_id=workspace_id)
        assert fetched.id == session_id
        assert fetched.title == "live-fk-repro"
    finally:
        if session_id is not None:
            async with engine.begin() as conn:
                # Delete child then parents, capturing FK cascade cleanup.
                await conn.execute(text("DELETE FROM fleet_sessions WHERE id = :id"), {"id": session_id})
                await conn.execute(text("DELETE FROM fleet_workspaces WHERE id = :id"), {"id": workspace_id})
                await conn.execute(text("DELETE FROM fleet_users WHERE id = :id"), {"id": user_id})
        await engine.dispose()
