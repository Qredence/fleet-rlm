"""Drop the unused fleet_skills table.

The bundled skill catalog is served from memory, and no runtime code reads or
writes fleet_skills. The downgrade recreates the table exactly as the canonical
coordinated turn baseline defined it, empty.

Revision ID: 01a087800004
Revises: 01a087800003
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "01a087800004"
down_revision = "01a087800003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("fleet_skills")


def downgrade() -> None:
    op.create_table(
        "fleet_skills",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("trust", sa.String(length=32), nullable=False),
        sa.Column("visibility", sa.String(length=32), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["fleet_workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
