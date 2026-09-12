from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, create_engine

from scripts.migrate_sqlite_to_postgres import (
    MigrationError,
    _canonical_value,
    _digest_rows,
    _environment_url,
    _manifest,
    _rows_for_target,
    _target_has_fleet_schema,
    _target_has_rows,
    _validate_statuses,
    _verify_sample_reconstruction,
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


def test_verified_backup_rejects_foreign_key_violations(tmp_path: Path) -> None:
    source = tmp_path / "source.sqlite3"
    with sqlite3.connect(source) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("CREATE TABLE parent (id INTEGER PRIMARY KEY)")
        connection.execute("CREATE TABLE child (parent_id INTEGER REFERENCES parent(id))")
        connection.execute("INSERT INTO child VALUES (99)")
    with pytest.raises(MigrationError, match="foreign-key"):
        create_verified_backup(source, tmp_path / "source.backup.sqlite3")


def test_import_rejects_invalid_canonical_status_without_repair(tmp_path: Path) -> None:
    source = tmp_path / "source.sqlite3"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE fleet_sessions (status TEXT NOT NULL)")
        connection.execute("INSERT INTO fleet_sessions VALUES ('corrupt')")
    engine = create_engine(f"sqlite:///{source}")
    metadata = MetaData()
    try:
        metadata.reflect(bind=engine)
        with engine.connect() as connection, pytest.raises(MigrationError, match="invalid fleet_sessions status"):
            _validate_statuses(connection, metadata)
    finally:
        engine.dispose()


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
    assert _target_has_fleet_schema(f"sqlite:///{empty}") is True


def test_empty_target_has_no_fleet_schema(tmp_path: Path) -> None:
    target = tmp_path / "empty.sqlite3"
    with sqlite3.connect(target):
        pass
    assert _target_has_fleet_schema(f"sqlite:///{target}") is False


def test_manifest_digest_normalizes_cross_backend_identity_and_timestamps() -> None:
    metadata = MetaData()
    table = Table(
        "sample",
        metadata,
        Column("id", String, primary_key=True),
        Column("created_at", DateTime(timezone=True)),
    )
    identifier = UUID("12345678-1234-5678-1234-567812345678")
    naive = datetime(2026, 9, 10, 12, 30, 45, 123456)
    aware = naive.replace(tzinfo=UTC)

    assert _canonical_value(identifier) == _canonical_value(str(identifier))
    assert _canonical_value(naive) == _canonical_value(aware)
    assert _digest_rows([{"id": identifier, "created_at": naive}], table) == _digest_rows(
        [{"id": str(identifier), "created_at": aware}], table
    )


def test_legacy_binding_rows_get_generation_one_before_manifest_and_insert() -> None:
    source_metadata = MetaData()
    source_table = Table(
        "fleet_sandbox_bindings",
        source_metadata,
        Column("session_id", String, primary_key=True),
        Column("workspace_id", String, nullable=False),
        Column("sandbox_id", String),
    )
    target_metadata = MetaData()
    target_table = Table(
        "fleet_sandbox_bindings",
        target_metadata,
        Column("session_id", String, primary_key=True),
        Column("workspace_id", String, nullable=False),
        Column("sandbox_id", String),
        Column("generation", Integer, nullable=False),
    )
    engine = create_engine("sqlite:///:memory:")
    try:
        source_metadata.create_all(engine)
        with engine.begin() as connection:
            connection.execute(
                source_table.insert(),
                {"session_id": "s-1", "workspace_id": "w-1", "sandbox_id": "sb-1"},
            )
        with engine.connect() as connection:
            rows = _rows_for_target(connection, source_table, target_table)
            assert rows == [{"session_id": "s-1", "workspace_id": "w-1", "sandbox_id": "sb-1", "generation": 1}]
            source_manifest = _manifest(connection, source_metadata, digest_metadata=target_metadata)
            assert source_manifest["fleet_sandbox_bindings"]["count"] == 1
            assert source_manifest["fleet_sandbox_bindings"]["rows_sha256"] == _digest_rows(rows, target_table)
    finally:
        engine.dispose()


def test_empty_source_table_still_rejects_missing_target_columns() -> None:
    source_metadata = MetaData()
    source_table = Table(
        "fleet_sandbox_bindings",
        source_metadata,
        Column("session_id", String, primary_key=True),
    )
    target_metadata = MetaData()
    target_table = Table(
        "fleet_sandbox_bindings",
        target_metadata,
        Column("session_id", String, primary_key=True),
        Column("generation", Integer, nullable=False),
        Column("sandbox_id", String, nullable=False),
    )
    engine = create_engine("sqlite:///:memory:")
    try:
        source_metadata.create_all(engine)
        with engine.connect() as connection, pytest.raises(MigrationError, match="missing required Fleet columns"):
            _rows_for_target(connection, source_table, target_table)
    finally:
        engine.dispose()


def test_generation_gap_is_only_normalized_for_legacy_binding_table() -> None:
    source_metadata = MetaData()
    source_table = Table("other_generation_table", source_metadata, Column("id", String, primary_key=True))
    target_metadata = MetaData()
    target_table = Table(
        "other_generation_table",
        target_metadata,
        Column("id", String, primary_key=True),
        Column("generation", Integer, nullable=False),
    )
    engine = create_engine("sqlite:///:memory:")
    try:
        source_metadata.create_all(engine)
        with engine.connect() as connection, pytest.raises(MigrationError, match="missing required Fleet columns"):
            _rows_for_target(connection, source_table, target_table)
    finally:
        engine.dispose()


def test_sample_reconstruction_checks_session_run_and_turn_history() -> None:
    def schema() -> MetaData:
        metadata = MetaData()
        Table("fleet_sessions", metadata, Column("id", String, primary_key=True))
        Table(
            "fleet_runs",
            metadata,
            Column("id", String, primary_key=True),
            Column("session_id", String, nullable=False),
            Column("created_at", DateTime(timezone=True), nullable=False),
        )
        Table(
            "fleet_turns",
            metadata,
            Column("id", String, primary_key=True),
            Column("run_id", String, nullable=False),
            Column("sequence", Integer, nullable=False),
            Column("role", String, nullable=False),
        )
        return metadata

    source_metadata = schema()
    target_metadata = schema()
    source_engine = create_engine("sqlite:///:memory:")
    target_engine = create_engine("sqlite:///:memory:")
    try:
        source_metadata.create_all(source_engine)
        target_metadata.create_all(target_engine)
        created_at = datetime(2026, 9, 10, tzinfo=UTC)
        source_values = {
            "fleet_sessions": {"id": "session-1"},
            "fleet_runs": {"id": "run-1", "session_id": "session-1", "created_at": created_at},
            "fleet_turns": {"id": "turn-1", "run_id": "run-1", "sequence": 1, "role": "user"},
        }
        with source_engine.begin() as source_connection, target_engine.begin() as target_connection:
            for name, values in source_values.items():
                source_connection.execute(source_metadata.tables[name].insert(), values)
                target_connection.execute(target_metadata.tables[name].insert(), values)

        with source_engine.connect() as source, target_engine.begin() as target:
            result = _verify_sample_reconstruction(source, target, source_metadata, target_metadata)

        assert result == {"status": "passed", "turn_count": 1}
    finally:
        source_engine.dispose()
        target_engine.dispose()
