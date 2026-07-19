from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from fleet_rlm.persistence.database import (
    DatabaseCompatibilityError,
    DatabaseConnectionError,
    check_database_compatibility,
    create_async_engine_from_url,
)


async def _set_revision(database_url: str, revision: str) -> None:
    engine = create_async_engine_from_url(database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
            await connection.execute(
                text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
                {"revision": revision},
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_database_compatibility_rejects_database_without_alembic_revision(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'empty.sqlite3'}"

    with pytest.raises(DatabaseCompatibilityError, match="Alembic head"):
        await check_database_compatibility(database_url)


@pytest.mark.asyncio
async def test_database_compatibility_accepts_exact_alembic_head(tmp_path: Path) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'head.sqlite3'}"
    await _set_revision(database_url, "019f8c1d2e3f")

    await check_database_compatibility(database_url)


@pytest.mark.asyncio
async def test_database_compatibility_rejects_mismatched_revision(tmp_path: Path) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'stale.sqlite3'}"
    await _set_revision(database_url, "stale-revision")

    with pytest.raises(DatabaseCompatibilityError, match="Alembic head"):
        await check_database_compatibility(database_url)


@pytest.mark.asyncio
async def test_database_compatibility_bounds_connection_failure(tmp_path: Path) -> None:
    database_path = tmp_path / "missing" / "database.sqlite3"
    database_url = f"sqlite+aiosqlite:///{database_path}"

    with pytest.raises(DatabaseConnectionError, match="connectivity check failed") as error:
        await check_database_compatibility(database_url)

    assert str(database_path) not in str(error.value)


def test_existing_baseline_database_upgrades_to_settling_head(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(__file__).resolve().parents[3]
    database_path = tmp_path / "existing.sqlite3"
    database_url = f"sqlite:///{database_path}"
    monkeypatch.setenv("FLEET_DATABASE_URL", database_url)
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))

    command.upgrade(config, "019f5b3c96bd")
    with create_engine(database_url).connect() as connection:
        assert "terminal_intent" not in {column["name"] for column in inspect(connection).get_columns("fleet_runs")}

    command.upgrade(config, "head")
    with create_engine(database_url).connect() as connection:
        columns = {column["name"] for column in inspect(connection).get_columns("fleet_runs")}
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    assert {"terminal_intent", "recovery_metadata_json"} <= columns
    assert revision == "019f8c1d2e3f"
