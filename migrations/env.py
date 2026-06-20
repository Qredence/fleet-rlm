"""Alembic environment for fleet-rlm Neon Postgres migrations."""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from dotenv import load_dotenv
from sqlalchemy import engine_from_config, pool

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

load_dotenv(ROOT / ".env", override=True)

from fleet_rlm.integrations.database import (  # noqa: E402
    Artifact,  # noqa: F401
    Base,
    ChatSession,  # noqa: F401
    ChatTurn,  # noqa: F401
    Dataset,  # noqa: F401
    DatasetExample,  # noqa: F401
    EvaluationResult,  # noqa: F401
    ExecutionEvent,  # noqa: F401
    ExternalTrace,  # noqa: F401
    Job,  # noqa: F401
    MemoryItem,  # noqa: F401
    MemoryLink,  # noqa: F401
    OptimizationModule,  # noqa: F401
    OptimizationRun,  # noqa: F401
    PromptSnapshot,  # noqa: F401
    RLMProgram,  # noqa: F401
    Run,  # noqa: F401
    RunStep,  # noqa: F401
    SandboxSession,  # noqa: F401
    SessionStateSnapshot,  # noqa: F401
    # Import all active SQLModel models so Alembic metadata has complete schema registry
    Tenant,  # noqa: F401
    TenantSubscription,  # noqa: F401
    TraceFeedback,  # noqa: F401
    User,  # noqa: F401
    Workspace,  # noqa: F401
    WorkspaceMembership,  # noqa: F401
    WorkspaceRuntimeSetting,  # noqa: F401
    models_llm_profiles,  # noqa: E402, F401
    select_database_url,
    to_sync_database_url,
)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


target_metadata = Base.metadata


def _resolve_database_url() -> str:
    database_url = select_database_url(
        runtime_url=os.getenv("DATABASE_URL"),
        admin_url=os.getenv("DATABASE_ADMIN_URL"),
        prefer_admin=True,
    )
    if not database_url:
        configured = config.get_main_option("sqlalchemy.url")
        database_url = configured
    if not database_url:
        raise RuntimeError(
            "DATABASE_ADMIN_URL or DATABASE_URL is not set. "
            "Set DATABASE_ADMIN_URL for direct admin access, DATABASE_URL as a fallback, "
            "or sqlalchemy.url before running migrations."
        )
    return to_sync_database_url(database_url)


def run_migrations_offline() -> None:
    url = _resolve_database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = _resolve_database_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        future=True,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
