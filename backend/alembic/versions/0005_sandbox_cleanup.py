"""Fence an allocation when a turn expires or its worker cannot stop."""

import sqlalchemy as sa
from alembic import op

revision = "0005_sandbox_cleanup"
down_revision = "0004_daytona_v2_cutover"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sessions",
        sa.Column("sandbox_needs_cleanup", sa.Boolean(), server_default=sa.false(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("sessions", "sandbox_needs_cleanup")
