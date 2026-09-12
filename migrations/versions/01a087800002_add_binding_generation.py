"""Persist monotonic Sandbox Binding generations.

Revision ID: 01a087800002
Revises: 01a087800001
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "01a087800002"
down_revision = "01a087800001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("fleet_sandbox_bindings") as batch:
        batch.add_column(sa.Column("generation", sa.Integer(), nullable=False, server_default="1"))
        batch.create_check_constraint("ck_fleet_bindings_generation_positive", "generation >= 1")


def downgrade() -> None:
    with op.batch_alter_table("fleet_sandbox_bindings") as batch:
        batch.drop_constraint("ck_fleet_bindings_generation_positive", type_="check")
        batch.drop_column("generation")
