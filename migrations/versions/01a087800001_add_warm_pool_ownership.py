"""Persist explicit Fleet ownership of Daytona warm pools.

Revision ID: 01a087800001
Revises: 019fe0010001
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "01a087800001"
down_revision = "019fe0010001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fleet_warm_pool_ownership",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pool_id", sa.String(length=255), nullable=False),
        sa.Column("campaign", sa.String(length=128), nullable=False),
        sa.Column("snapshot", sa.String(length=255), nullable=False),
        sa.Column("target", sa.String(length=128), nullable=True),
        sa.Column("manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("candidate_sha", sa.String(length=64), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pool_id", name="uq_fleet_warm_pool_ownership_pool"),
    )


def downgrade() -> None:
    op.drop_table("fleet_warm_pool_ownership")
