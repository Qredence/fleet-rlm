"""Database enforcement and reversible, fail-closed lineage migration."""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError


def _database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
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
    config, engine = _database(tmp_path, monkeypatch)
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
    config, engine = _database(tmp_path, monkeypatch)
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
