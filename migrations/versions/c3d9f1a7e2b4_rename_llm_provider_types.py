"""rename_llm_provider_types

Revision ID: c3d9f1a7e2b4
Revises: h4d5e6f7g8h9
Create Date: 2026-07-01 03:00:00.000000

One-shot backfill of ``llm_provider_profiles.provider_type`` rows from the
former 5 vendor-flavored literals to the new 3 wire-format-named literals:

    openai / openai_compatible / litellm_proxy / google -> openai_chat_completion
    anthropic / anthropic_compatible                      -> anthropic_messages

The Postgres column is ``VARCHAR(64)`` — no enum constraint, so no DDL is
needed and this migration is purely a data backfill.

IMPORTANT TRADEOFF: real ``openai`` profiles (which used the OpenAI Responses
API) lose their association with Responses under the uniform default
backfill to ``openai_chat_completion``. POST-DEPLOY AUDIT STEP: any user who
had a genuine OpenAI Responses profile must edit each affected profile in the
UI and switch its provider_type from ``openai_chat_completion`` (the backfill
default) to ``openai_responses``. If no real-OpenAI profiles exist today
(typical BYOK setup with Alibaba/OpenRouter/OpenAI-compatible custom bases),
this tradeoff is invisible.

Downgrade is a no-op: legacy strings are not recoverable after a conceptual
rename — preserving the original mapping would require an audit table that
this one-shot migration does not create.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "c3d9f1a7e2b4"
down_revision = "h4d5e6f7g8h9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # WHERE guard makes the migration idempotent: already-migrated rows
    # (openai_responses / openai_chat_completion / anthropic_messages) and any
    # off-contract legacy values are left untouched on replay (restore,
    # `alembic stamp` recovery, multi-tenant DB, downstream fork init).
    # Without this guard, a second pass would silently flip user-corrected
    # openai_profiles rows back to openai_chat_completion.
    op.execute(
        """
        UPDATE llm_provider_profiles SET provider_type =
          CASE
            WHEN provider_type = 'openai' OR provider_type = 'openai_compatible'
                 OR provider_type = 'litellm_proxy' OR provider_type = 'google'
              THEN 'openai_chat_completion'
            WHEN provider_type = 'anthropic' OR provider_type = 'anthropic_compatible'
              THEN 'anthropic_messages'
            ELSE 'openai_chat_completion'
          END
        WHERE provider_type NOT IN ('openai_responses', 'openai_chat_completion', 'anthropic_messages')
        """
    )


def downgrade() -> None:
    # One-way conceptual rename — downgrade is a no-op; legacy strings are
    # not recoverable without an audit table this migration does not create.
    pass
