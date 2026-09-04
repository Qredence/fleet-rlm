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
    await _set_revision(database_url, "019fb7e2c4d1")

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
    assert revision == "019fb7e2c4d1"


def test_existing_baseline_database_upgrades_to_memory_intents_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P23: baseline databases reach the promotion-intent head with the new table."""
    root = Path(__file__).resolve().parents[3]
    database_path = tmp_path / "existing_p23.sqlite3"
    database_url = f"sqlite:///{database_path}"
    monkeypatch.setenv("FLEET_DATABASE_URL", database_url)
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))

    command.upgrade(config, "019f5b3c96bd")
    with create_engine(database_url).connect() as connection:
        assert "fleet_memory_promotion_intents" not in set(inspect(connection).get_table_names())

    command.upgrade(config, "head")
    with create_engine(database_url).connect() as connection:
        tables = set(inspect(connection).get_table_names())
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        intent_columns = {
            column["name"] for column in inspect(connection).get_columns("fleet_memory_promotion_intents")
        }
    assert "fleet_memory_promotion_intents" in tables
    assert {
        "run_id",
        "candidate_id",
        "candidate_ordinal",
        "memory_id",
        "record_text",
        "status",
        "attempts",
        "next_attempt_at",
        "claim_owner",
        "claim_heartbeat_at",
        "completion_reason",
    } <= intent_columns
    assert revision == "019fb7e2c4d1"


def test_relational_integrity_migration_round_trips_constraints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = Path(__file__).resolve().parents[3]
    database_url = f"sqlite:///{tmp_path / 'integrity.sqlite3'}"
    monkeypatch.setenv("FLEET_DATABASE_URL", database_url)
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))

    sync_engine = create_engine(database_url)
    try:
        command.upgrade(config, "head")
        with sync_engine.connect() as connection:
            inspector = inspect(connection)
            turn_fks = inspector.get_foreign_keys("fleet_turns")
            binding_fks = inspector.get_foreign_keys("fleet_sandbox_bindings")
            session_checks = inspector.get_check_constraints("fleet_sessions")
        assert any(foreign_key["referred_table"] == "fleet_runs" for foreign_key in turn_fks)
        assert any(foreign_key["referred_table"] == "fleet_workspaces" for foreign_key in binding_fks)
        assert any(check["name"] == "ck_fleet_sessions_status" for check in session_checks)

        command.downgrade(config, "019fa2e4b7c1")
        with sync_engine.connect() as connection:
            turn_fks = inspect(connection).get_foreign_keys("fleet_turns")
        assert not any(foreign_key["referred_table"] == "fleet_runs" for foreign_key in turn_fks)
    finally:
        sync_engine.dispose()
