"""Alembic schema enforcement, dirty-data preflight, and reversible lineage migration.

* ``test_binding_lineage_migration.py``: Binding lineage migration rejects dirty data before changing the schema.
* ``test_turn_lineage_migration.py``: Database enforcement and reversible, fail-closed lineage migration.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from fleet_rlm.persistence.database import (
    DatabaseCompatibilityError,
    DatabaseConnectionError,
    check_database_compatibility,
    create_async_engine_from_url,
)


# --- from test_binding_lineage_migration.py ---------------------------
def _database(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'bindings.db'}"
    monkeypatch.setenv("FLEET_DATABASE_URL", url)
    config = Config("alembic.ini")
    command.upgrade(config, "019fdb010001")
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO fleet_users (id) VALUES ('u')"))
        connection.execute(text("INSERT INTO fleet_workspaces (id,name) VALUES ('w','one'), ('other','two')"))
        connection.execute(
            text(
                "INSERT INTO fleet_sessions (id,user_id,workspace_id,status,title,checkpoint_version) "
                "VALUES ('s','u','w','active','test',0)"
            )
        )
    return config, engine


def _binding(connection, workspace="w", session="s"):
    connection.execute(
        text(
            "INSERT INTO fleet_sandbox_bindings "
            "(id,session_id,workspace_id,volume_subpath,mount_path,provider_state) "
            "VALUES ('b',:session,:workspace,'scope','/home/daytona/fleet','missing')"
        ),
        {"session": session, "workspace": workspace},
    )


@pytest.mark.parametrize("dirty", ["orphan_session", "orphan_workspace", "cross_workspace", "status"])
def test_dirty_preflight_preserves_rows_and_revision(tmp_path, monkeypatch, dirty):
    config, engine = _database(tmp_path, monkeypatch)
    try:
        with engine.begin() as connection:
            _binding(
                connection,
                workspace={"orphan_workspace": "missing", "cross_workspace": "other"}.get(dirty, "w"),
                session="missing" if dirty == "orphan_session" else "s",
            )
            if dirty == "status":
                connection.execute(text("UPDATE fleet_sessions SET status='invalid'"))
        with pytest.raises(RuntimeError, match="preflight failed"):
            command.upgrade(config, "head")
        with engine.connect() as connection:
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "019fdb010001"
            assert connection.execute(text("SELECT COUNT(*) FROM fleet_sandbox_bindings")).scalar_one() == 1
            assert "uq_fleet_sessions_id_workspace" not in {
                index["name"] for index in inspect(connection).get_indexes("fleet_sessions")
            }
    finally:
        engine.dispose()


def test_upgrade_enforces_lineage_and_status_and_round_trips(tmp_path, monkeypatch):
    config, engine = _database(tmp_path, monkeypatch)
    try:
        command.upgrade(config, "head")
        with engine.connect() as connection:
            connection.execute(text("PRAGMA foreign_keys=ON"))
            connection.commit()
            for workspace, session in [("other", "s"), ("missing", "s"), ("w", "missing")]:
                with pytest.raises(IntegrityError), connection.begin():
                    _binding(connection, workspace, session)
            with pytest.raises(IntegrityError), connection.begin():
                connection.execute(text("UPDATE fleet_sessions SET status='invalid'"))
            with connection.begin():
                _binding(connection)
        command.downgrade(config, "019fdb010001")
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert connection.execute(text("SELECT COUNT(*) FROM fleet_sandbox_bindings")).scalar_one() == 1
            assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
    finally:
        engine.dispose()


# --- from test_database_compatibility.py ------------------------------
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
    await _set_revision(database_url, "01a087800002")

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
    root = Path(__file__).resolve().parents[4]
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
    assert revision == "01a087800002"


def test_existing_baseline_database_upgrades_to_memory_intents_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P23: baseline databases reach the promotion-intent head with the new table."""
    root = Path(__file__).resolve().parents[4]
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
    assert revision == "01a087800002"


# --- from test_turn_lineage_migration.py ------------------------------
def _lineage_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """
    Configure a temporary SQLite database and apply the baseline migration.

    Parameters:
        tmp_path (Path): Temporary directory in which to create the database.
        monkeypatch (pytest.MonkeyPatch): Fixture used to set the database URL environment variable.

    Returns:
        tuple: The Alembic configuration and SQLAlchemy engine for the database.
    """
    url = f"sqlite:///{tmp_path / 'lineage.db'}"
    monkeypatch.setenv("FLEET_DATABASE_URL", url)
    config = Config("alembic.ini")
    command.upgrade(config, "019fa2e4b7c1")
    return config, create_engine(url)


def _seed(connection) -> None:
    """
    Populate the database with a user, workspace, two active sessions, and a running session-associated run.
    """
    connection.execute(text("INSERT INTO fleet_users (id) VALUES ('u')"))
    connection.execute(text("INSERT INTO fleet_workspaces (id, name) VALUES ('w', 'test')"))
    for session in ("s1", "s2"):
        connection.execute(
            text(
                "INSERT INTO fleet_sessions (id,user_id,workspace_id,status,title,checkpoint_version) "
                "VALUES (:id,'u','w','active','test',0)"
            ),
            {"id": session},
        )
    connection.execute(
        text(
            "INSERT INTO fleet_runs (id,session_id,status,idempotency_key,input_fingerprint,"
            "base_checkpoint_version,claim_owner,claim_heartbeat_at) "
            "VALUES ('r','s1','running','key',:fingerprint,0,'owner',CURRENT_TIMESTAMP)"
        ),
        {"fingerprint": "a" * 64},
    )


def _turn(connection, session: str, run: str = "r") -> None:
    """
    Insert a user turn associated with a session and run.

    Parameters:
        connection: Database connection used to execute the insert.
        session: Identifier of the session associated with the turn.
        run: Identifier of the run associated with the turn.
    """
    connection.execute(
        text(
            "INSERT INTO fleet_turns (id,session_id,run_id,sequence,role,user_input_json) "
            "VALUES ('t',:session,:run,1,'user','{}')"
        ),
        {"session": session, "run": run},
    )


@pytest.mark.parametrize("session,run", [("s2", "r"), ("s1", "missing")])
def test_upgrade_rejects_dirty_lineage_before_ddl(tmp_path, monkeypatch, session, run):
    config, engine = _lineage_database(tmp_path, monkeypatch)
    try:
        with engine.begin() as connection:
            _seed(connection)
            _turn(connection, session, run)
        with pytest.raises(RuntimeError, match="lineage preflight failed"):
            command.upgrade(config, "head")
        with engine.connect() as connection:
            assert connection.execute(text("SELECT COUNT(*) FROM fleet_turns")).scalar_one() == 1
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "019fa2e4b7c1"
            assert "uq_fleet_runs_id_session" not in {i["name"] for i in inspect(connection).get_indexes("fleet_runs")}
    finally:
        engine.dispose()


def test_upgrade_enforces_immediate_lineage_and_downgrade_preserves_rows(tmp_path, monkeypatch):
    """Verify lineage enforcement after upgrade and row preservation after downgrade."""
    config, engine = _lineage_database(tmp_path, monkeypatch)
    try:
        with engine.begin() as connection:
            _seed(connection)
        command.upgrade(config, "head")
        with engine.connect() as connection:
            connection.execute(text("PRAGMA foreign_keys=ON"))
            connection.commit()
            with pytest.raises(IntegrityError), connection.begin():
                _turn(connection, "s2")
            with connection.begin():
                _turn(connection, "s1")
        command.downgrade(config, "019fa2e4b7c1")
        with engine.connect() as connection:
            assert connection.execute(text("SELECT COUNT(*) FROM fleet_turns")).scalar_one() == 1
            assert "fk_fleet_turns_run_session" not in {
                fk["name"] for fk in inspect(connection).get_foreign_keys("fleet_turns")
            }
        command.upgrade(config, "head")
    finally:
        engine.dispose()
