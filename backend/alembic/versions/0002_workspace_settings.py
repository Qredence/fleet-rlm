"""Create the singleton workspace_settings table."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002_workspace_settings"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workspace_settings",
        sa.Column("id", sa.SmallInteger(), primary_key=True),
        sa.Column(
            "overrides_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("id = 1", name="ck_workspace_settings_singleton"),
    )


def downgrade() -> None:
    op.drop_table("workspace_settings")
