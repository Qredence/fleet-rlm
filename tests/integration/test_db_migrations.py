from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from fleet_rlm.db import DatabaseManager

DATABASE_URL = os.getenv("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="DATABASE_URL not configured",
)


@pytest.mark.asyncio
async def test_migrations_apply_and_core_tables_exist():
    repo_root = Path(__file__).resolve().parents[2]

    cfg = Config(str(repo_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(repo_root / "migrations"))
    command.upgrade(cfg, "head")

    assert DATABASE_URL is not None
    db = DatabaseManager(DATABASE_URL)
    try:
        async with db.session() as session:
            async with session.begin():
                result = await session.execute(
                    text(
                        """
                        select table_name
                        from information_schema.tables
                        where table_schema = 'public'
                          and table_name in (
                            'tenants',
                            'users',
                            'runs',
                            'run_steps',
                            'artifacts',
                            'memory_items',
                            'jobs'
                          )
                        order by table_name
                        """
                    )
                )
                names = [row[0] for row in result.fetchall()]
        assert names == [
            "artifacts",
            "jobs",
            "memory_items",
            "run_steps",
            "runs",
            "tenants",
            "users",
        ]
    finally:
        await db.dispose()
