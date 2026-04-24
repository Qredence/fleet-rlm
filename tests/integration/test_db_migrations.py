from __future__ import annotations

from pathlib import Path

import pytest
import psycopg
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from fleet_rlm.integrations.database import DatabaseManager

pytestmark = [
    pytest.mark.db,
]


@pytest.mark.asyncio
async def test_migrations_apply_and_core_tables_exist(
    require_migration_database_url: str,
):
    """Verify that all Alembic migrations apply cleanly and expected tables exist.

    This test requires a *dedicated* test database. It prefers
    ``DATABASE_ADMIN_URL`` for the destructive reset + migration path and falls
    back to ``DATABASE_URL`` when a separate admin URL is not configured. It
    drops and recreates the ``public`` schema before running migrations so that
    Alembic starts from a completely empty state. Do not run it against a shared
    or production database.
    """
    repo_root = Path(__file__).resolve().parents[2]

    cfg = Config(str(repo_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(repo_root / "migrations"))
    with psycopg.connect(require_migration_database_url) as conn:
        with conn.cursor() as cur:
            cur.execute("DROP SCHEMA public CASCADE")
            cur.execute("CREATE SCHEMA public")
        conn.commit()
    command.upgrade(cfg, "head")

    db = DatabaseManager(require_migration_database_url)
    try:
        async with db.session() as session:
            async with session.begin():
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

                column_result = await session.execute(
                    text(
                        """
                        select table_name, column_name
                        from information_schema.columns
                        where table_schema = 'public'
                          and (
                            (table_name = 'workspaces' and column_name = 'slug')
                            or (table_name = 'execution_runs' and column_name = 'workspace_id')
                            or (table_name = 'sandbox_sessions' and column_name = 'created_by_user_id')
                            or (table_name = 'memory_items' and column_name = 'workspace_id')
                            or (table_name = 'tenant_subscriptions' and column_name = 'purchaser_tenant_id')
                            or (table_name = 'trace_feedback' and column_name = 'external_trace_id')
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
                            'ix_tenant_subscriptions_status',
                            'ix_jobs_workspace_status_available',
                            'ix_execution_events_run_sequence'
                          )
                        order by indexname
                        """
                    )
                )
                control_plane_indexes = {row[0] for row in index_result.fetchall()}

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

                rls_result = await session.execute(
                    text(
                        """
                        select c.relname, c.relrowsecurity, c.relforcerowsecurity
                        from pg_class c
                        join pg_namespace n on n.oid = c.relnamespace
                        where n.nspname = 'public'
                          and c.relname in ('users', 'chat_sessions', 'jobs')
                        order by c.relname
                        """
                    )
                )
                rls_state = {
                    row[0]: (bool(row[1]), bool(row[2]))
                    for row in rls_result.fetchall()
                }

                uuid_helper_result = await session.execute(
                    text("select to_regprocedure('app.uuid_v7()')")
                )
                uuid_helper = uuid_helper_result.scalar_one()

        expected_tables = {
            "artifacts",
            "chat_sessions",
            "chat_turns",
            "dataset_examples",
            "datasets",
            "evaluation_results",
            "execution_events",
            "execution_runs",
            "execution_steps",
            "external_traces",
            "jobs",
            "memory_items",
            "memory_links",
            "optimization_modules",
            "optimization_runs",
            "outbox_events",
            "program_versions",
            "prompt_snapshots",
            "sandbox_sessions",
            "session_state_snapshots",
            "tenant_memberships",
            "tenant_subscriptions",
            "tenants",
            "trace_feedback",
            "users",
            "volume_objects",
            "workspace_memberships",
            "workspace_runtime_settings",
            "workspace_volumes",
            "workspaces",
        }
        assert all_table_names - {"alembic_version"} == expected_tables
        assert control_plane_columns == {
            ("execution_runs", "workspace_id"),
            ("memory_items", "workspace_id"),
            ("sandbox_sessions", "created_by_user_id"),
            ("tenant_subscriptions", "purchaser_tenant_id"),
            ("trace_feedback", "external_trace_id"),
            ("workspaces", "slug"),
        }
        assert control_plane_indexes == {
            "ix_execution_events_run_sequence",
            "ix_jobs_workspace_status_available",
            "ix_tenant_subscriptions_status",
            "ix_tenants_status",
        }
        assert rls_state == {
            "chat_sessions": (True, True),
            "jobs": (True, True),
            "users": (True, True),
        }
        assert uuid_helper == "app.uuid_v7()"

        for deprecated_table in {
            "skill_taxonomies",
            "taxonomy_terms",
            "skills",
            "skill_versions",
            "skill_term_links",
            "run_skill_usages",
            "memberships",
            "runs",
            "run_steps",
            "rlm_programs",
            "rlm_traces",
        }:
            assert deprecated_table not in all_table_names

        for deprecated_enum in {
            "skill_source",
            "skill_status",
            "skill_link_source",
            "skill_usage_status",
        }:
            assert deprecated_enum not in enum_names
        for required_enum in {
            "workspace_status",
            "workspace_role",
            "run_type",
            "chat_session_status",
            "chat_turn_status",
            "artifact_provider",
            "memory_status",
            "outbox_status",
        }:
            assert required_enum in enum_names
    finally:
        await db.dispose()
