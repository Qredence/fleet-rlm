"""impl-05: foundation session schema and repository (no live providers)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from fleet_rlm_clean.persistence.database import (
    DatabaseNotConfiguredError,
    create_async_engine_from_url,
    create_session_factory,
    create_tables,
    normalize_database_url,
)
from fleet_rlm_clean.persistence.models import Base
from fleet_rlm_clean.sessions.errors import SessionNotFoundError
from fleet_rlm_clean.sessions.repository import SessionRepository


def test_normalize_database_url_upgrades_drivers() -> None:
    assert normalize_database_url("sqlite:///:memory:").startswith("sqlite+aiosqlite://")
    assert normalize_database_url("postgresql://u:p@h/db").startswith("postgresql+asyncpg://")
    assert normalize_database_url("postgres://u:p@h/db").startswith("postgresql+asyncpg://")
    with pytest.raises(DatabaseNotConfiguredError):
        normalize_database_url("   ")


def test_foundation_tables_are_registered() -> None:
    names = set(Base.metadata.tables)
    expected = {
        "clean_users",
        "clean_workspaces",
        "clean_sessions",
        "clean_turns",
        "clean_runs",
        "clean_session_checkpoints",
        "clean_sandbox_bindings",
        "clean_attachments",
        "clean_artifacts",
        "clean_skills",
    }
    assert expected <= names


@pytest.mark.asyncio
async def test_empty_database_boots_and_session_round_trip() -> None:
    engine = create_async_engine_from_url("sqlite+aiosqlite:///:memory:")
    await create_tables(engine)
    factory = create_session_factory(engine)
    repo = SessionRepository(factory)

    user_id = uuid4()
    workspace_id = uuid4()
    created = await repo.create(user_id=user_id, workspace_id=workspace_id, title="t1")
    loaded = await repo.load(created.id)

    assert loaded.session.id == created.id
    assert loaded.session.user_id == user_id
    assert loaded.session.workspace_id == workspace_id
    assert loaded.session.checkpoint_version == 0
    assert loaded.turns == ()

    await engine.dispose()


@pytest.mark.asyncio
async def test_load_missing_session_raises() -> None:
    engine = create_async_engine_from_url("sqlite+aiosqlite:///:memory:")
    await create_tables(engine)
    repo = SessionRepository(create_session_factory(engine))

    with pytest.raises(SessionNotFoundError):
        await repo.load(uuid4())

    await engine.dispose()


def test_settings_accept_database_url() -> None:
    from fleet_rlm_clean.config import Settings

    settings = Settings(database_url="sqlite+aiosqlite:///:memory:")
    assert settings.database_url == "sqlite+aiosqlite:///:memory:"
