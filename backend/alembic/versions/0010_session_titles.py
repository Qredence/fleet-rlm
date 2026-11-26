"""Fill placeholder session titles from the first root prompt."""

from alembic import op

revision = "0010_session_titles"
down_revision = "0009_optimization_extensions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        WITH first_prompts AS (
            SELECT DISTINCT ON (runs.session_id) runs.session_id,
                   btrim(regexp_replace(runs.prompt, '[[:space:]]+', ' ', 'g')) AS title
            FROM runs
            JOIN sessions ON sessions.id = runs.session_id
            WHERE runs.kind = 'root' AND sessions.title = 'New session'
            ORDER BY runs.session_id, runs.created_at, runs.id
        )
        UPDATE sessions
        SET title = CASE WHEN char_length(first_prompts.title) <= 80 THEN first_prompts.title
                         ELSE rtrim(left(first_prompts.title, 79)) || '…' END
        FROM first_prompts
        WHERE sessions.id = first_prompts.session_id
          AND sessions.title = 'New session'
          AND first_prompts.title <> ''
    """)


def downgrade() -> None:
    # A derived title may have been manually edited since the backfill.
    pass
