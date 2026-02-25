from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from fleet_rlm.db import DatabaseManager

DATABASE_URL = os.getenv("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL,
        reason="DATABASE_URL not configured",
    ),
    pytest.mark.db,
]


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
                            'modal_volumes',
                            'runs',
                            'run_steps',
                            'artifacts',
                            'rlm_programs',
                            'rlm_traces',
                            'memory_items',
                            'jobs'
                          )
                        order by table_name
                        """
                    )
                )
                names = [row[0] for row in result.fetchall()]

                all_tables_result = await session.execute(
                    text(
                        """
                        select table_name
                        from information_schema.tables
                        where table_schema = 'public'
                        order by table_name
                        """
                    )
                )
                all_table_names = {row[0] for row in all_tables_result.fetchall()}

                enum_result = await session.execute(
                    text(
                        """
                        select t.typname
                        from pg_type t
                        join pg_namespace n on n.oid = t.typnamespace
                        where n.nspname = 'public'
                          and t.typtype = 'e'
                        order by t.typname
                        """
                    )
                )
                enum_names = {row[0] for row in enum_result.fetchall()}
        assert names == [
            "artifacts",
            "jobs",
            "memory_items",
            "modal_volumes",
            "rlm_programs",
            "rlm_traces",
            "run_steps",
            "runs",
            "tenants",
            "users",
        ]
        for deprecated_table in {
            "skill_taxonomies",
            "taxonomy_terms",
            "skills",
            "skill_versions",
            "skill_term_links",
            "run_skill_usages",
        }:
            assert deprecated_table not in all_table_names

        for deprecated_enum in {
            "skill_source",
            "skill_status",
            "skill_link_source",
            "skill_usage_status",
        }:
            assert deprecated_enum not in enum_names
    finally:
        await db.dispose()
