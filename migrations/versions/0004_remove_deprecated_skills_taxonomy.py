"""Remove deprecated skills taxonomy and usage schema.

Revision ID: 0004_remove_deprecated_skills_taxonomy
Revises: 0003_skills_taxonomy_and_usage
Create Date: 2026-02-22
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "0004_remove_deprecated_skills_taxonomy"
down_revision = "0003_skills_taxonomy_and_usage"
branch_labels = None
depends_on = None


RLS_TABLES = [
    "skill_taxonomies",
    "taxonomy_terms",
    "skills",
    "skill_versions",
    "skill_term_links",
    "run_skill_usages",
]


def upgrade() -> None:
    # Widen Alembic's version tracking column before switching to longer
    # descriptive revision ids in later migrations.
    op.execute("ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(128)")

    # Drop tenant RLS policies before dropping the tables they target.
    for table_name in reversed(RLS_TABLES):
        op.execute(
            f"DROP POLICY IF EXISTS tenant_isolation_{table_name} ON {table_name}"
        )

    # Drop indexes explicitly (mirrors 0003 downgrade order) before table removal.
    op.execute("DROP INDEX IF EXISTS ix_run_skill_usages_tenant_step")
    op.execute("DROP INDEX IF EXISTS ix_run_skill_usages_tenant_skill_started_at")
    op.execute("DROP INDEX IF EXISTS ix_run_skill_usages_tenant_run_started_at")
    op.execute("DROP INDEX IF EXISTS ix_skill_term_links_tenant_term")
    op.execute("DROP INDEX IF EXISTS ix_skill_term_links_tenant_skill")
    op.execute("DROP INDEX IF EXISTS uq_skill_versions_tenant_skill_current")
    op.execute("DROP INDEX IF EXISTS ix_skill_versions_tenant_skill_version_num")
    op.execute("DROP INDEX IF EXISTS ix_skills_tenant_display_name")
    op.execute("DROP INDEX IF EXISTS ix_skills_tenant_status")
    op.execute("DROP INDEX IF EXISTS ix_skills_tenant_created_at")
    op.execute("DROP INDEX IF EXISTS ix_taxonomy_terms_synonyms")
    op.execute("DROP INDEX IF EXISTS ix_taxonomy_terms_tenant_slug")
    op.execute("DROP INDEX IF EXISTS ix_taxonomy_terms_tenant_taxonomy_parent_sort")
    op.execute("DROP INDEX IF EXISTS ix_skill_taxonomies_tenant_key")
    op.execute("DROP INDEX IF EXISTS ix_skill_taxonomies_tenant_created_at")

    # Drop tables in FK dependency order.
    op.execute("DROP TABLE IF EXISTS run_skill_usages")
    op.execute("DROP TABLE IF EXISTS skill_term_links")
    op.execute("DROP TABLE IF EXISTS skill_versions")
    op.execute("DROP TABLE IF EXISTS skills")
    op.execute("DROP TABLE IF EXISTS taxonomy_terms")
    op.execute("DROP TABLE IF EXISTS skill_taxonomies")

    # Remove deprecated enum types once no columns depend on them.
    op.execute("DROP TYPE IF EXISTS skill_usage_status")
    op.execute("DROP TYPE IF EXISTS skill_link_source")
    op.execute("DROP TYPE IF EXISTS skill_status")
    op.execute("DROP TYPE IF EXISTS skill_source")


def downgrade() -> None:
    msg = (
        "Downgrade for 0004_remove_deprecated_skills_taxonomy is intentionally "
        "not implemented because it would need to recreate dropped tables/types "
        "and cannot restore deleted data."
    )
    raise NotImplementedError(msg)
