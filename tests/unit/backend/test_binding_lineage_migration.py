"""Binding lineage migration rejects dirty data before changing the schema."""

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError


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
