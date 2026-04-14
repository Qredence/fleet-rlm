"""Tests for optimization run list/get/phase/recovery in local_store."""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _isolated_db(monkeypatch, tmp_path):
    """Point local_store at a fresh temporary SQLite database."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setenv("FLEET_RLM_LOCAL_DB_URL", f"sqlite:///{db_path}")
    from fleet_rlm.integrations import local_store

    local_store._engines.clear()


def test_create_optimization_run_with_new_fields():
    from fleet_rlm.integrations.local_store import create_optimization_run

    run = create_optimization_run(
        program_spec="test:module",
        module_slug="reflect-and-revise",
        dataset_path="data/test.jsonl",
    )
    assert run.id is not None
    assert run.module_slug == "reflect-and-revise"
    assert run.dataset_path == "data/test.jsonl"
    assert run.phase is None
    assert run.manifest_path is None


def test_get_optimization_run():
    from fleet_rlm.integrations.local_store import (
        create_optimization_run,
        get_optimization_run,
    )

    created = create_optimization_run(program_spec="test:mod")
    fetched = get_optimization_run(created.id)
    assert fetched is not None
    assert fetched.id == created.id
    assert fetched.program_spec == "test:mod"


def test_get_optimization_run_not_found():
    from fleet_rlm.integrations.local_store import get_optimization_run

    assert get_optimization_run(99999) is None


def test_list_optimization_runs_ordered_by_recent():
    from fleet_rlm.integrations.local_store import (
        create_optimization_run,
        list_optimization_runs,
    )

    r1 = create_optimization_run(program_spec="mod:a")
    r2 = create_optimization_run(program_spec="mod:b")
    r3 = create_optimization_run(program_spec="mod:c")

    runs = list_optimization_runs()
    assert len(runs) >= 3
    ids = [r.id for r in runs]
    assert ids.index(r3.id) < ids.index(r2.id) < ids.index(r1.id)


def test_list_optimization_runs_filter_by_status():
    from fleet_rlm.integrations.local_store import (
        RunStatus,
        complete_optimization_run,
        create_optimization_run,
        list_optimization_runs,
    )

    r1 = create_optimization_run(program_spec="mod:a")
    r2 = create_optimization_run(program_spec="mod:b")
    complete_optimization_run(r1.id, train_examples=5, validation_examples=2)

    completed = list_optimization_runs(status=RunStatus.COMPLETED)
    running = list_optimization_runs(status=RunStatus.RUNNING)
    assert any(r.id == r1.id for r in completed)
    assert all(r.id != r1.id for r in running)
    assert any(r.id == r2.id for r in running)


def test_list_optimization_runs_pagination():
    from fleet_rlm.integrations.local_store import (
        create_optimization_run,
        list_optimization_runs,
    )

    for i in range(5):
        create_optimization_run(program_spec=f"mod:{i}")

    page1 = list_optimization_runs(limit=2, offset=0)
    page2 = list_optimization_runs(limit=2, offset=2)
    assert len(page1) == 2
    assert len(page2) == 2
    assert page1[0].id != page2[0].id


def test_update_optimization_run_phase():
    from fleet_rlm.integrations.local_store import (
        create_optimization_run,
        get_optimization_run,
        update_optimization_run_phase,
    )

    run = create_optimization_run(program_spec="mod:test")
    update_optimization_run_phase(run.id, phase="compiling")
    fetched = get_optimization_run(run.id)
    assert fetched is not None
    assert fetched.phase == "compiling"


def test_complete_optimization_run_with_manifest():
    from fleet_rlm.integrations.local_store import (
        complete_optimization_run,
        create_optimization_run,
        get_optimization_run,
    )

    run = create_optimization_run(program_spec="mod:test")
    complete_optimization_run(
        run.id,
        train_examples=8,
        validation_examples=2,
        validation_score=0.85,
        manifest_path="/tmp/manifest.json",
    )
    fetched = get_optimization_run(run.id)
    assert fetched is not None
    assert fetched.status.value == "completed"
    assert fetched.manifest_path == "/tmp/manifest.json"
    assert fetched.phase == "completed"


def test_recover_stale_optimization_runs():
    from fleet_rlm.integrations.local_store import (
        create_optimization_run,
        get_optimization_run,
        recover_stale_optimization_runs,
    )

    r1 = create_optimization_run(program_spec="mod:stale1")
    r2 = create_optimization_run(program_spec="mod:stale2")

    recovered = recover_stale_optimization_runs()
    assert recovered == 2

    for rid in [r1.id, r2.id]:
        fetched = get_optimization_run(rid)
        assert fetched is not None
        assert fetched.status.value == "failed"
        assert "Server restarted" in (fetched.error or "")


def test_recover_stale_runs_idempotent():
    from fleet_rlm.integrations.local_store import (
        create_optimization_run,
        recover_stale_optimization_runs,
    )

    create_optimization_run(program_spec="mod:x")
    assert recover_stale_optimization_runs() == 1
    assert recover_stale_optimization_runs() == 0


def test_migration_adds_columns_to_existing_db(tmp_path):
    """Verify _migrate_optimization_runs handles pre-existing tables."""
    import sqlite3

    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE optimization_runs (
            id INTEGER PRIMARY KEY,
            dataset_id INTEGER,
            optimizer VARCHAR(16),
            status VARCHAR(16) DEFAULT 'running',
            program_spec VARCHAR(255),
            output_path TEXT,
            auto VARCHAR(16) DEFAULT 'light',
            train_ratio REAL DEFAULT 0.8,
            train_examples INTEGER,
            validation_examples INTEGER,
            validation_score REAL,
            error TEXT,
            started_at TIMESTAMP,
            completed_at TIMESTAMP,
            created_at TIMESTAMP
        )
        """
    )
    conn.commit()
    conn.close()

    os.environ["FLEET_RLM_LOCAL_DB_URL"] = f"sqlite:///{db_path}"
    from fleet_rlm.integrations import local_store

    local_store._engines.clear()

    # This should trigger migration without error
    local_store.get_engine()

    # Verify new columns exist by inserting a row with them
    from fleet_rlm.integrations.local_store import create_optimization_run

    run = create_optimization_run(
        program_spec="migrated:mod",
        module_slug="test-slug",
        dataset_path="data/test.jsonl",
    )
    assert run.module_slug == "test-slug"
