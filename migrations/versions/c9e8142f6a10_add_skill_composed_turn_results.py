"""add skill-composed turn details and typed results

Revision ID: c9e8142f6a10
Revises: 7528a210a339
Create Date: 2026-07-13 12:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c9e8142f6a10"
down_revision = "7528a210a339"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("fleet_turns", sa.Column("detail_parts_json", sa.JSON(), nullable=True))
    op.add_column("fleet_turns", sa.Column("structured_output_json", sa.JSON(), nullable=True))
    op.add_column("fleet_turns", sa.Column("result_schema_id", sa.String(length=255), nullable=True))
    op.add_column("fleet_turns", sa.Column("result_schema_version", sa.String(length=64), nullable=True))
    op.add_column("fleet_runs", sa.Column("result_detail_parts_json", sa.JSON(), nullable=True))
    op.add_column("fleet_runs", sa.Column("result_structured_output_json", sa.JSON(), nullable=True))
    op.add_column("fleet_runs", sa.Column("result_schema_id", sa.String(length=255), nullable=True))
    op.add_column("fleet_runs", sa.Column("result_schema_version", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("fleet_runs", "result_schema_version")
    op.drop_column("fleet_runs", "result_schema_id")
    op.drop_column("fleet_runs", "result_structured_output_json")
    op.drop_column("fleet_runs", "result_detail_parts_json")
    op.drop_column("fleet_turns", "result_schema_version")
    op.drop_column("fleet_turns", "result_schema_id")
    op.drop_column("fleet_turns", "structured_output_json")
    op.drop_column("fleet_turns", "detail_parts_json")
