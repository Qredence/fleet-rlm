from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from scripts.migrate_sqlite_to_postgres import (
    MigrationError,
    _environment_url,
    _target_has_rows,
    create_verified_backup,
)


def test_environment_url_requires_an_explicit_uppercase_name(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(MigrationError):
        _environment_url("database_url")
    monkeypatch.setenv("FLEET_SOURCE_DB", "sqlite:///source.sqlite3")
    assert _environment_url("FLEET_SOURCE_DB") == "sqlite:///source.sqlite3"


def test_verified_backup_is_exclusive_and_integrity_checked(tmp_path: Path) -> None:
    source = tmp_path / "source.sqlite3"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE proof (value TEXT NOT NULL)")
        connection.execute("INSERT INTO proof VALUES ('ok')")
    backup = tmp_path / "source.backup.sqlite3"
    create_verified_backup(source, backup)
    with sqlite3.connect(backup) as connection:
        assert connection.execute("SELECT value FROM proof").fetchone() == ("ok",)
    with pytest.raises(MigrationError, match="already exists"):
        create_verified_backup(source, backup)


def test_target_nonempty_check_runs_before_upgrade(tmp_path: Path) -> None:
    target = tmp_path / "target.sqlite3"
    with sqlite3.connect(target) as connection:
        connection.execute("CREATE TABLE fleet_users (id TEXT NOT NULL)")
        connection.execute("INSERT INTO fleet_users VALUES ('occupied')")
    assert _target_has_rows(f"sqlite:///{target}") is True

    empty = tmp_path / "empty.sqlite3"
    with sqlite3.connect(empty) as connection:
        connection.execute("CREATE TABLE fleet_users (id TEXT NOT NULL)")
    assert _target_has_rows(f"sqlite:///{empty}") is False
