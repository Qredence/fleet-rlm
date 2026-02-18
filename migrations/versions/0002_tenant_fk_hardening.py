"""Harden tenant-scoped foreign keys and integrity constraints.

Revision ID: 0002_tenant_fk_hardening
Revises: 0001_neon_core_schema
Create Date: 2026-02-18
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "0002_tenant_fk_hardening"
down_revision = "0001_neon_core_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint("uq_users_tenant_id_id", "users", ["tenant_id", "id"])
    op.create_unique_constraint(
        "uq_sandbox_sessions_tenant_id_id",
        "sandbox_sessions",
        ["tenant_id", "id"],
    )
    op.create_unique_constraint("uq_runs_tenant_id_id", "runs", ["tenant_id", "id"])
    op.create_unique_constraint(
        "uq_run_steps_tenant_id_id",
        "run_steps",
        ["tenant_id", "id"],
    )

    op.execute(
        "ALTER TABLE memberships DROP CONSTRAINT IF EXISTS memberships_user_id_fkey"
    )
    op.execute(
        "ALTER TABLE runs DROP CONSTRAINT IF EXISTS runs_created_by_user_id_fkey"
    )
    op.execute(
        "ALTER TABLE runs DROP CONSTRAINT IF EXISTS runs_sandbox_session_id_fkey"
    )
    op.execute("ALTER TABLE run_steps DROP CONSTRAINT IF EXISTS run_steps_run_id_fkey")
    op.execute("ALTER TABLE artifacts DROP CONSTRAINT IF EXISTS artifacts_run_id_fkey")
    op.execute("ALTER TABLE artifacts DROP CONSTRAINT IF EXISTS artifacts_step_id_fkey")

    op.create_foreign_key(
        "fk_memberships_tenant_user__users_tenant_id_id",
        "memberships",
        "users",
        ["tenant_id", "user_id"],
        ["tenant_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_runs_tenant_created_by_user__users_tenant_id_id",
        "runs",
        "users",
        ["tenant_id", "created_by_user_id"],
        ["tenant_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_runs_tenant_sandbox_session__sandbox_sessions_tenant_id_id",
        "runs",
        "sandbox_sessions",
        ["tenant_id", "sandbox_session_id"],
        ["tenant_id", "id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_run_steps_tenant_run__runs_tenant_id_id",
        "run_steps",
        "runs",
        ["tenant_id", "run_id"],
        ["tenant_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_artifacts_tenant_run__runs_tenant_id_id",
        "artifacts",
        "runs",
        ["tenant_id", "run_id"],
        ["tenant_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_artifacts_tenant_step__run_steps_tenant_id_id",
        "artifacts",
        "run_steps",
        ["tenant_id", "step_id"],
        ["tenant_id", "id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_artifacts_tenant_step__run_steps_tenant_id_id",
        "artifacts",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_artifacts_tenant_run__runs_tenant_id_id",
        "artifacts",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_run_steps_tenant_run__runs_tenant_id_id",
        "run_steps",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_runs_tenant_sandbox_session__sandbox_sessions_tenant_id_id",
        "runs",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_runs_tenant_created_by_user__users_tenant_id_id",
        "runs",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_memberships_tenant_user__users_tenant_id_id",
        "memberships",
        type_="foreignkey",
    )

    op.create_foreign_key(
        "memberships_user_id_fkey",
        "memberships",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "runs_created_by_user_id_fkey",
        "runs",
        "users",
        ["created_by_user_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "runs_sandbox_session_id_fkey",
        "runs",
        "sandbox_sessions",
        ["sandbox_session_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "run_steps_run_id_fkey",
        "run_steps",
        "runs",
        ["run_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "artifacts_run_id_fkey",
        "artifacts",
        "runs",
        ["run_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "artifacts_step_id_fkey",
        "artifacts",
        "run_steps",
        ["step_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.drop_constraint("uq_run_steps_tenant_id_id", "run_steps", type_="unique")
    op.drop_constraint("uq_runs_tenant_id_id", "runs", type_="unique")
    op.drop_constraint(
        "uq_sandbox_sessions_tenant_id_id",
        "sandbox_sessions",
        type_="unique",
    )
    op.drop_constraint("uq_users_tenant_id_id", "users", type_="unique")
