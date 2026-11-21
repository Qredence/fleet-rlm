"""Create the Postgres 18 foundation schema."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "sessions",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("uuidv7()")),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("daytona_sandbox_id", sa.Text(), unique=True),
        sa.Column(
            "history_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("""'{"messages": []}'::jsonb"""),
        ),
        sa.Column("model_override", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_table(
        "runs",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("uuidv7()")),
        sa.Column(
            "session_id",
            sa.Uuid(),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("parent_run_id", sa.Uuid(), sa.ForeignKey("runs.id", ondelete="CASCADE")),
        sa.Column("root_run_id", sa.Uuid(), nullable=False),
        sa.Column("depth", sa.SmallInteger(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text()),
        sa.Column(
            "ui_parts", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'running'")),
        sa.Column("error", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("depth BETWEEN 0 AND 8", name="ck_runs_depth"),
        sa.CheckConstraint("kind IN ('root', 'subagent', 'llm_query')", name="ck_runs_kind"),
        sa.CheckConstraint(
            "status IN ('running', 'completed', 'failed', 'cancelled')", name="ck_runs_status"
        ),
    )
    op.create_index(
        "uq_runs_one_running_root_per_session",
        "runs",
        ["session_id"],
        unique=True,
        postgresql_where=sa.text("parent_run_id IS NULL AND status = 'running'"),
    )
    op.create_index("idx_runs_root_tree", "runs", ["root_run_id", "id"])
    op.create_index(
        "idx_runs_session_roots",
        "runs",
        ["session_id", sa.text("id DESC")],
        postgresql_where=sa.text("parent_run_id IS NULL"),
    )
    op.create_table(
        "files",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("uuidv7()")),
        sa.Column(
            "session_id",
            sa.Uuid(),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("sha256", sa.CHAR(64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("session_id", "path", name="uq_files_session_path"),
        sa.CheckConstraint("kind IN ('input', 'artifact')", name="ck_files_kind"),
    )
    op.create_table(
        "memories",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("uuidv7()")),
        sa.Column("session_id", sa.Uuid(), sa.ForeignKey("sessions.id", ondelete="CASCADE")),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("approved", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "search_tsv",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('english', content)", persisted=True),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("idx_memories_search_tsv", "memories", ["search_tsv"], postgresql_using="gin")


def downgrade() -> None:
    op.drop_table("memories")
    op.drop_table("files")
    op.drop_table("runs")
    op.drop_table("sessions")
