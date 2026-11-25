"""Store installed Skills in Postgres so every worker shares them."""

import sqlalchemy as sa
from alembic import op

revision = "0008_installed_skills"
down_revision = "0007_turn_lessons"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "installed_skills",
        sa.Column("name", sa.Text(), primary_key=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("raw_markdown", sa.Text(), nullable=False),
        sa.Column("sha256", sa.CHAR(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("installed_skills")
