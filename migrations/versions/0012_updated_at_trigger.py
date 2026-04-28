"""Add DB-level BEFORE UPDATE trigger for updated_at columns.

Revision ID: 0012_updated_at_trigger
Revises: 0011_drop_redundant_indexes
Create Date: 2026-04-27
"""

from __future__ import annotations

from alembic import op

revision = "0012_updated_at_trigger"
down_revision = "0011_drop_redundant_indexes"
branch_labels = None
depends_on = None

_TABLES_WITH_UPDATED_AT = [
    "tenants",
    "users",
    "tenant_memberships",
    "workspaces",
    "workspace_memberships",
    "workspace_runtime_settings",
    "chat_sessions",
    "execution_runs",
    "execution_steps",
    "external_traces",
    "jobs",
    "outbox_events",
    "tenant_subscriptions",
    "memory_items",
    "optimization_modules",
    "datasets",
    "optimization_runs",
    "program_versions",
    "sandbox_sessions",
    "workspace_volumes",
    "volume_objects",
]


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app.set_updated_at()
        RETURNS TRIGGER
        LANGUAGE plpgsql
        AS $$
        BEGIN
          NEW.updated_at = now();
          RETURN NEW;
        END;
        $$;
        """
    )

    for table in _TABLES_WITH_UPDATED_AT:
        trigger_name = f"trg_{table}_updated_at"
        op.execute(f"DROP TRIGGER IF EXISTS {trigger_name} ON public.{table}")
        op.execute(
            f"""
            CREATE TRIGGER {trigger_name}
            BEFORE UPDATE ON public.{table}
            FOR EACH ROW
            EXECUTE FUNCTION app.set_updated_at()
            """
        )


def downgrade() -> None:
    for table in _TABLES_WITH_UPDATED_AT:
        trigger_name = f"trg_{table}_updated_at"
        op.execute(f"DROP TRIGGER IF EXISTS {trigger_name} ON public.{table}")

    op.execute("DROP FUNCTION IF EXISTS app.set_updated_at()")
