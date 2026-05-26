from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from fleet_rlm.integrations.daytona.memory_db import init_memory_db


def test_creates_core_db(tmp_path: Path) -> None:
    memories = tmp_path / "memories"
    memories.mkdir()
    init_memory_db(str(tmp_path))
    db_path = memories / "core.db"
    assert db_path.exists()
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='memory'").fetchall()
    conn.close()
    assert len(rows) == 1


def test_idempotent(tmp_path: Path) -> None:
    memories = tmp_path / "memories"
    memories.mkdir()
    init_memory_db(str(tmp_path))
    init_memory_db(str(tmp_path))
    db_path = memories / "core.db"
    assert db_path.exists()
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='memory'").fetchall()
    conn.close()
    assert len(rows) == 1


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
    assert "created_at" in col_names
