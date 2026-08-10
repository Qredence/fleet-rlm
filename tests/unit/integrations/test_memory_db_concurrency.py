"""Cross-process concurrency tests for the volume memory DB.

These spawn real OS processes, so the ``fcntl.flock`` seam and the atomic
``os.replace`` swap in :mod:`fleet_rlm.integrations.daytona.memory_db` are
exercised.  The in-process memory tests never take the lock, so a lost-record
or half-written-file regression only shows up here.
"""

from __future__ import annotations

import multiprocessing as mp
import queue
import sqlite3
import subprocess
import sys
from pathlib import Path

from fleet_rlm.integrations.daytona import memory_db
from fleet_rlm.integrations.daytona.memory_db import MEMORY_SCHEMA_VERSION, memory_db_bootstrap_script
from fleet_rlm.runtime.tools.volume_memory_tools import _recall_impl, _remember_impl

# Linux is the deployment target; fork keeps the worker functions picklable
# without re-importing the package in every child.
_MP = mp.get_context("fork")


def _writer(volume_mount_path: str, worker_id: int, count: int) -> None:
    for i in range(count):
        _remember_impl(f"w{worker_id}_k{i}", f"value-{worker_id}-{i}", volume_mount_path=volume_mount_path)


def _reader(volume_mount_path: str, iterations: int, errors: mp.Queue) -> None:
    for _ in range(iterations):
        result = _recall_impl("k", volume_mount_path=volume_mount_path)
        if result.get("status") != "ok":
            errors.put(result)


def _run(procs: list[mp.Process]) -> None:
    for proc in procs:
        proc.start()
    for proc in procs:
        proc.join(timeout=25)
    for proc in procs:
        assert proc.exitcode == 0, f"worker exited with {proc.exitcode}"


def test_concurrent_writers_do_not_lose_records(tmp_path: Path) -> None:
    (tmp_path / "memories").mkdir()
    workers, per_worker = 8, 25

    _run([_MP.Process(target=_writer, args=(str(tmp_path), w, per_worker)) for w in range(workers)])

    conn = sqlite3.connect(str(tmp_path / "memories" / "core.db"))
    total = conn.execute("SELECT COUNT(*) FROM memory").fetchone()[0]
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    conn.close()

    assert total == workers * per_worker
    assert integrity == "ok"


def test_readers_never_observe_corruption(tmp_path: Path) -> None:
    (tmp_path / "memories").mkdir()
    errors: mp.Queue = _MP.Queue()

    writers = [_MP.Process(target=_writer, args=(str(tmp_path), w, 20)) for w in range(4)]
    readers = [_MP.Process(target=_reader, args=(str(tmp_path), 40, errors)) for _ in range(4)]
    _run(writers + readers)

    collected = []
    while True:
        try:
            collected.append(errors.get_nowait())
        except queue.Empty:
            break
    assert collected == []


def test_concurrent_bootstrap_preserves_existing_rows(tmp_path: Path) -> None:
    memories = tmp_path / "memories"
    memories.mkdir()
    db_path = memories / "core.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE memory (key TEXT PRIMARY KEY, value TEXT NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )
    conn.execute("INSERT INTO memory(key, value) VALUES ('seed', 'kept')")
    conn.commit()
    conn.close()

    script = memory_db_bootstrap_script(str(tmp_path))
    procs = [subprocess.Popen([sys.executable, "-c", script]) for _ in range(4)]
    for proc in procs:
        assert proc.wait(timeout=25) == 0

    conn = sqlite3.connect(str(db_path))
    row = conn.execute("SELECT value FROM memory WHERE key = 'seed'").fetchone()
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    conn.close()

    assert row == ("kept",)
    assert integrity == "ok"
    assert version == MEMORY_SCHEMA_VERSION


def test_ensure_memory_db_migrates_once_per_session(tmp_path: Path, monkeypatch) -> None:
    memory_db.reset_memory_db_session_cache()
    (tmp_path / "memories").mkdir()

    calls: list[Path] = []
    original = memory_db._migrate_locked

    def spy(db_path: Path) -> None:
        calls.append(db_path)
        original(db_path)

    monkeypatch.setattr(memory_db, "_migrate_locked", spy)

    memory_db.ensure_memory_db(str(tmp_path))
    memory_db.ensure_memory_db(str(tmp_path))

    assert len(calls) == 1
    memory_db.reset_memory_db_session_cache()
