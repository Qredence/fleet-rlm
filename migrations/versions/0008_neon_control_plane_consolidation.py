"""Consolidate Neon control-plane metadata and identity ownership.

Revision ID: 0008_neon_control_plane_consolidation
Revises: 0007_neon_performance_indexes
Create Date: 2026-03-06
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0008_neon_control_plane_consolidation"
down_revision = "0007_neon_performance_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tenants", sa.Column("slug", sa.String(length=128), nullable=True))
    op.create_unique_constraint("uq_tenants_slug", "tenants", ["slug"])
    op.create_index("ix_tenants_status", "tenants", ["status"])

    op.add_column(
        "sandbox_sessions",
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_sandbox_sessions_tenant_user__users_tenant_id_id",
        "sandbox_sessions",
        "users",
        ["tenant_id", "created_by_user_id"],
        ["tenant_id", "id"],
        ondelete="RESTRICT",
    )

    op.add_column("memory_items", sa.Column("uri", sa.Text(), nullable=True))

    op.add_column(
        "tenant_subscriptions",
        sa.Column("purchaser_tenant_id", sa.String(length=128), nullable=True),
    )
    op.create_index(
        "ix_tenant_subscriptions_status", "tenant_subscriptions", ["status"]
    )


def downgrade() -> None:
    op.drop_index("ix_tenant_subscriptions_status", table_name="tenant_subscriptions")
    op.drop_column("tenant_subscriptions", "purchaser_tenant_id")

    op.drop_column("memory_items", "uri")

    op.drop_constraint(
        "fk_sandbox_sessions_tenant_user__users_tenant_id_id",
        "sandbox_sessions",
        type_="foreignkey",
    )
    op.drop_column("sandbox_sessions", "created_by_user_id")

    op.drop_index("ix_tenants_status", table_name="tenants")
    op.drop_constraint("uq_tenants_slug", "tenants", type_="unique")
    op.drop_column("tenants", "slug")
