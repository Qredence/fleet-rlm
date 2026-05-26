"""Unit tests for remember / recall volume memory tools."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from fleet_rlm.runtime.tools.volume_memory_tools import _recall_impl, _remember_impl

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mem_dir(tmp_path: Path) -> Path:
    """Create a temporary memories directory mirroring the volume layout."""
    memories = tmp_path / "memories"
    memories.mkdir()
    db_path = memories / "core.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS memory "
        "(key TEXT PRIMARY KEY, value TEXT NOT NULL, "
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )
    conn.commit()
    conn.close()
    return tmp_path


# ---------------------------------------------------------------------------
# remember tests
# ---------------------------------------------------------------------------


class TestRememberImpl:
    def test_remember_writes_key(self, mem_dir: Path) -> None:
        result = _remember_impl("name", "Alice", volume_mount_path=str(mem_dir))
        assert result["status"] == "ok"
        assert result["key"] == "name"

    def test_remember_read_back(self, mem_dir: Path) -> None:
        _remember_impl("fact", "sky is blue", volume_mount_path=str(mem_dir))
        db_path = mem_dir / "memories" / "core.db"
        conn = sqlite3.connect(str(db_path))
        row = conn.execute("SELECT value FROM memory WHERE key = ?", ("fact",)).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "sky is blue"

    def test_remember_upserts_on_duplicate_key(self, mem_dir: Path) -> None:
        _remember_impl("k", "v1", volume_mount_path=str(mem_dir))
        _remember_impl("k", "v2", volume_mount_path=str(mem_dir))
        db_path = mem_dir / "memories" / "core.db"
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT value FROM memory WHERE key = ?", ("k",)).fetchall()
        conn.close()
        assert len(rows) == 1
        assert rows[0][0] == "v2"

    def test_remember_depth_gate_blocks_write(self, mem_dir: Path) -> None:
        result = _remember_impl("secret", "blocked", volume_mount_path=str(mem_dir), agent_depth=1)
        assert result["status"] == "skipped"
        db_path = mem_dir / "memories" / "core.db"
        conn = sqlite3.connect(str(db_path))
        row = conn.execute("SELECT value FROM memory WHERE key = ?", ("secret",)).fetchone()
        conn.close()
        assert row is None

    def test_remember_depth_zero_allows_write(self, mem_dir: Path) -> None:
        result = _remember_impl("allowed", "yes", volume_mount_path=str(mem_dir), agent_depth=0)
        assert result["status"] == "ok"

    def test_remember_missing_memories_dir_returns_error(self, tmp_path: Path) -> None:
        result = _remember_impl("k", "v", volume_mount_path=str(tmp_path))
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# recall tests
# ---------------------------------------------------------------------------


class TestRecallImpl:
    def test_recall_finds_by_key(self, mem_dir: Path) -> None:
        _remember_impl("project_name", "FleetRLM", volume_mount_path=str(mem_dir))
        result = _recall_impl("project_name", volume_mount_path=str(mem_dir))
        assert result["status"] == "ok"
        assert result["count"] == 1
        assert result["results"][0]["key"] == "project_name"

    def test_recall_finds_by_value_substring(self, mem_dir: Path) -> None:
        _remember_impl("bio", "I work on AI agents", volume_mount_path=str(mem_dir))
        result = _recall_impl("AI agents", volume_mount_path=str(mem_dir))
        assert result["status"] == "ok"
        assert result["count"] >= 1

    def test_recall_returns_empty_list_when_no_match(self, mem_dir: Path) -> None:
        result = _recall_impl("xyzzy_does_not_exist", volume_mount_path=str(mem_dir))
        assert result["status"] == "ok"
        assert result["count"] == 0
        assert result["results"] == []

    def test_recall_no_db_returns_ok_with_note(self, tmp_path: Path) -> None:
        result = _recall_impl("anything", volume_mount_path=str(tmp_path))
        assert result["status"] == "ok"
        assert "note" in result

    def test_recall_works_at_any_depth(self, mem_dir: Path) -> None:
        _remember_impl("deep_key", "deep_val", volume_mount_path=str(mem_dir), agent_depth=0)
        result = _recall_impl("deep_key", volume_mount_path=str(mem_dir))
        assert result["status"] == "ok"
        assert result["count"] == 1
