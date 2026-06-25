"""harden_identity_constraints

Clean up orphan rows left from pre-RLS migrations and enforce NOT NULL on
tenant_id / user_id for LLM profile tables. Install pg_stat_statements for
query performance monitoring.

Orphan rows were created before migration d31f6d7a8c21 added tenant/user scoping.
Under FORCE RLS they are invisible to every role (NULL ≠ anything in policy
evaluation) — they are dead data that cannot be read, updated, or deleted by
the application. This migration reclaims them.

Also installs pg_stat_statements so slow queries can be identified via
pg_stat_statements view and the Neon slow-query tooling.

Revision ID: f2b3c4d5e6f7
Revises: e1a2b3c4d5e6
Create Date: 2026-06-25 12:00:00.000000
"""

from __future__ import annotations

from alembic import op

revision = "f2b3c4d5e6f7"
down_revision = "e1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Step 1: Delete orphan rows ─────────────────────────────────────
    # These rows have NULL tenant_id or user_id and are invisible under
    # FORCE RLS. They were created before user-scoping was added.
    op.execute("DELETE FROM public.llm_provider_profiles WHERE tenant_id IS NULL OR user_id IS NULL")
    op.execute("DELETE FROM public.llm_role_bindings WHERE tenant_id IS NULL OR user_id IS NULL")

    # ── Step 2: Enforce NOT NULL on identity columns ────────────────────
    # llm_provider_profiles: tenant_id and user_id must always be set.
    # workspace_id remains nullable (profiles can exist without a workspace).
    op.alter_column("llm_provider_profiles", "tenant_id", nullable=False)
    op.alter_column("llm_provider_profiles", "user_id", nullable=False)

    op.alter_column("llm_role_bindings", "tenant_id", nullable=False)
    op.alter_column("llm_role_bindings", "user_id", nullable=False)

    # ── Step 3: Install pg_stat_statements ──────────────────────────────
    # Enables slow query monitoring. The extension is available on Neon
    # and provides query-level performance statistics.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_stat_statements")


def downgrade() -> None:
    # Revert NOT NULL constraints back to nullable
    op.alter_column("llm_role_bindings", "user_id", nullable=True)
    op.alter_column("llm_role_bindings", "tenant_id", nullable=True)

    op.alter_column("llm_provider_profiles", "user_id", nullable=True)
    op.alter_column("llm_provider_profiles", "tenant_id", nullable=True)

    # Note: we do NOT re-insert orphan rows (they were dead data).
    # Note: we do NOT drop pg_stat_statements (monitoring is non-destructive).
