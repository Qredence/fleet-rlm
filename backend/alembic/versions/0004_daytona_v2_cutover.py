"""Reset v0 workspaces and add durable object references."""

import sqlalchemy as sa
from alembic import op

revision = "0004_daytona_v2_cutover"
down_revision = "0003_fk_and_query_indexes"
branch_labels = None
depends_on = None


def _clear_workspaces() -> None:
    op.execute("UPDATE sessions SET daytona_sandbox_id = NULL")
    op.execute("DELETE FROM files")
    op.execute(
        """
        CREATE FUNCTION pg_temp.fleet_remove_file_parts(parts jsonb)
        RETURNS jsonb LANGUAGE sql IMMUTABLE AS $$
          SELECT COALESCE(jsonb_agg(
            CASE
              WHEN part->>'type' = 'tool-subagent'
                AND jsonb_typeof(part #> '{output,parts}') = 'array'
              THEN jsonb_set(part, '{output,parts}',
                pg_temp.fleet_remove_file_parts(part #> '{output,parts}'))
              ELSE part
            END ORDER BY position
          ), '[]'::jsonb)
          FROM jsonb_array_elements(parts) WITH ORDINALITY AS items(part, position)
          WHERE part->>'type' IS DISTINCT FROM 'data-file-tree'
        $$
        """
    )
    op.execute(
        """
        UPDATE runs
        SET ui_parts = pg_temp.fleet_remove_file_parts(ui_parts)
        WHERE jsonb_typeof(ui_parts) = 'array' AND strpos(ui_parts::text, 'data-file-tree') > 0
        """
    )
    op.execute("DROP FUNCTION pg_temp.fleet_remove_file_parts(jsonb)")


def upgrade() -> None:
    _clear_workspaces()
    op.add_column(
        "sessions", sa.Column("deleting", sa.Boolean(), server_default=sa.false(), nullable=False)
    )
    op.add_column("sessions", sa.Column("upload_key", sa.Text()))
    op.add_column("sessions", sa.Column("upload_started_at", sa.DateTime(timezone=True)))
    op.add_column("files", sa.Column("object_key", sa.Text(), nullable=False))
    op.execute(
        "UPDATE workspace_settings SET overrides_json = "
        "overrides_json - 'daytona_target' - 'daytona_auto_stop_minutes'"
    )


def downgrade() -> None:
    # The legacy binding and files can only be restored from the pre-cutover backup.
    _clear_workspaces()
    op.drop_column("files", "object_key")
    op.drop_column("sessions", "upload_started_at")
    op.drop_column("sessions", "upload_key")
    op.drop_column("sessions", "deleting")
