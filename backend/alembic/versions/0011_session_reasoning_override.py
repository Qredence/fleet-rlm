"""Persist model-bound reasoning effort for chat."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0011_session_reasoning_override"
down_revision = "0010_session_titles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sessions", sa.Column("reasoning_override", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("sessions", "reasoning_override")
