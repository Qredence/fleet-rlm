from __future__ import annotations

from pathlib import Path

import pytest
import psycopg
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text

from fleet_rlm.integrations.database import DatabaseManager

pytestmark = [
    pytest.mark.db,
]


@pytest.mark.asyncio
async def test_migrations_apply_and_core_tables_exist(require_database_url: str):
    repo_root = Path(__file__).resolve().parents[2]

    cfg = Config(str(repo_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(repo_root / "migrations"))
    head_revision = ScriptDirectory.from_config(cfg).get_current_head()
    with psycopg.connect(require_database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS alembic_version (
                    version_num VARCHAR(128) NOT NULL
                )
                """
            )
            cur.execute("DELETE FROM alembic_version")
            cur.execute(
                "INSERT INTO alembic_version (version_num) VALUES (%s)",
                (head_revision,),
            )
        conn.commit()
    command.upgrade(cfg, "head")

    db = DatabaseManager(require_database_url)
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
                            'memberships',
                            'sandbox_sessions',
                            'modal_volumes',
                            'runs',
                            'run_steps',
                            'artifacts',
                            'rlm_programs',
                            'rlm_traces',
                            'memory_items',
                            'jobs',
                            'tenant_subscriptions'
                          )
                        order by table_name
                        """
                    )
                )
                names = [row[0] for row in result.fetchall()]

                column_result = await session.execute(
                    text(
                        """
                        select table_name, column_name
                        from information_schema.columns
                        where table_schema = 'public'
                          and (
                            (table_name = 'tenants' and column_name = 'slug')
                            or (table_name = 'sandbox_sessions' and column_name = 'created_by_user_id')
                            or (table_name = 'memory_items' and column_name = 'uri')
                            or (table_name = 'tenant_subscriptions' and column_name = 'purchaser_tenant_id')
                          )
                        order by table_name, column_name
                        """
                    )
                )
                control_plane_columns = {
                    (row[0], row[1]) for row in column_result.fetchall()
                }

                index_result = await session.execute(
                    text(
                        """
                        select indexname
                        from pg_indexes
                        where schemaname = 'public'
                          and indexname in (
                            'ix_tenants_status',
                            'ix_tenant_subscriptions_status'
                          )
                        order by indexname
                        """
                    )
                )
                control_plane_indexes = {row[0] for row in index_result.fetchall()}

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
            "memberships",
            "memory_items",
            "modal_volumes",
            "rlm_programs",
            "rlm_traces",
            "run_steps",
            "runs",
            "sandbox_sessions",
            "tenant_subscriptions",
            "tenants",
            "users",
        ]
        assert control_plane_columns == {
            ("memory_items", "uri"),
            ("sandbox_sessions", "created_by_user_id"),
            ("tenant_subscriptions", "purchaser_tenant_id"),
            ("tenants", "slug"),
        }
        assert control_plane_indexes == {
            "ix_tenant_subscriptions_status",
            "ix_tenants_status",
        }
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
