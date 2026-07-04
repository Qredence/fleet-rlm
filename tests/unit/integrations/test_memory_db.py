from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from fleet_rlm.integrations.daytona.volumes import MEMORY_SCHEMA_VERSION, init_memory_db, memory_db_bootstrap_script


def test_creates_core_db(tmp_path: Path) -> None:
    memories = tmp_path / "memories"
    memories.mkdir()
    init_memory_db(str(tmp_path))
    db_path = memories / "core.db"
    assert db_path.exists()
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='memory'").fetchall()
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    conn.close()
    assert len(rows) == 1
    assert version == MEMORY_SCHEMA_VERSION


def test_idempotent(tmp_path: Path) -> None:
    memories = tmp_path / "memories"
    memories.mkdir()
    init_memory_db(str(tmp_path))
    init_memory_db(str(tmp_path))
    db_path = memories / "core.db"
    assert db_path.exists()
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='memory'").fetchall()
    migration_rows = conn.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
    conn.close()
    assert len(rows) == 1
    assert [row[0] for row in migration_rows] == [1, MEMORY_SCHEMA_VERSION]


def test_missing_memories_dir_logs_warning(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    import logging

    with caplog.at_level(logging.WARNING, logger="fleet_rlm.integrations.daytona.memory_db"):
        init_memory_db(str(tmp_path))
    assert not (tmp_path / "memories" / "core.db").exists()


def test_db_has_correct_schema(tmp_path: Path) -> None:
    memories = tmp_path / "memories"
    memories.mkdir()
    init_memory_db(str(tmp_path))
    db_path = memories / "core.db"
    conn = sqlite3.connect(str(db_path))
    columns = conn.execute("PRAGMA table_info(memory)").fetchall()
    conn.close()
    col_names = [row[1] for row in columns]
    assert "key" in col_names
    assert "value" in col_names
    assert "scope" in col_names
    assert "writer_agent_depth" in col_names
    assert "created_at" in col_names
    assert "updated_at" in col_names


def test_migrates_existing_db_with_rows_idempotently(tmp_path: Path) -> None:
    memories = tmp_path / "memories"
    memories.mkdir()
    db_path = memories / "core.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE memory "
        "(key TEXT PRIMARY KEY, value TEXT NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )
    conn.execute("INSERT INTO memory(key, value) VALUES ('existing', 'kept')")
    conn.commit()
    conn.close()

    init_memory_db(str(tmp_path))
    init_memory_db(str(tmp_path))

    conn = sqlite3.connect(str(db_path))
    columns = {row[1] for row in conn.execute("PRAGMA table_info(memory)").fetchall()}
    row = conn.execute(
        "SELECT key, value, scope, writer_agent_depth, updated_at FROM memory WHERE key = 'existing'"
    ).fetchone()
    migration_rows = conn.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    conn.close()

    assert {"scope", "writer_agent_depth", "updated_at"} <= columns
    assert row[0] == "existing"
    assert row[1] == "kept"
    assert row[2] == "core"
    assert row[3] == 0
    assert row[4]
    assert [migration[0] for migration in migration_rows] == [1, MEMORY_SCHEMA_VERSION]
    assert version == MEMORY_SCHEMA_VERSION


def test_host_init_migrates_staged_temp_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import fleet_rlm.integrations.daytona.volumes as memory_db

    memories = tmp_path / "memories"
    memories.mkdir()
    db_path = memories / "core.db"
    original_connect = memory_db.sqlite3.connect
    connected_paths: list[Path] = []

    def spy_connect(path: str, *args: object, **kwargs: object) -> sqlite3.Connection:
        connected_paths.append(Path(path))
        return original_connect(path, *args, **kwargs)

    monkeypatch.setattr(memory_db.sqlite3, "connect", spy_connect)

    init_memory_db(str(tmp_path))

    assert db_path.exists()
    assert connected_paths
    assert db_path not in connected_paths


def test_remote_bootstrap_script_uses_current_schema(tmp_path: Path) -> None:
    script = memory_db_bootstrap_script(str(tmp_path))
    namespace: dict[str, object] = {}

    exec(script, namespace)

    db_path = tmp_path / "memories" / "core.db"
    conn = sqlite3.connect(str(db_path))
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    columns = {row[1] for row in conn.execute("PRAGMA table_info(memory)").fetchall()}
    conn.close()

    assert version == MEMORY_SCHEMA_VERSION
    assert {"scope", "writer_agent_depth", "updated_at"} <= columns
