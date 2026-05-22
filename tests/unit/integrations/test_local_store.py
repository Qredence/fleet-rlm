"""Tests for local_store dataset, evaluation, run, and session CRUD."""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import dspy
import pytest

from fleet_rlm.quality.module_registry import (
    _REGISTRY,
    ModuleOptimizationSpec,
    register_module,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_db(monkeypatch, tmp_path):
    """Point local_store at a fresh temporary SQLite database."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setenv("FLEET_RLM_LOCAL_DB_URL", f"sqlite:///{db_path}")
    ds_root = str(tmp_path / "datasets")
    monkeypatch.setenv("FLEET_RLM_DATASET_ROOT", ds_root)
    from fleet_rlm.integrations import local_store

    local_store._engines.clear()


@pytest.fixture(autouse=True)
def _register_test_module_spec():
    """Register a stub module spec for tests that use 'reflect-and-revise' slug."""

    def _stub_row_converter(rows):
        return [dspy.Example(**{k: str(v) for k, v in row.items()}).with_inputs("user_request") for row in rows]

    spec = ModuleOptimizationSpec(
        module_slug="reflect-and-revise",
        label="Reflect & Revise",
        program_spec="stub",
        artifact_filename="stub.json",
        input_keys=["user_request"],
        required_dataset_keys=["user_request"],
        module_factory=lambda: None,
        row_converter=_stub_row_converter,
        metric_builder=lambda: None,
        metric_name="stub",
        description="stub for testing",
    )
    register_module(spec)
    yield
    _REGISTRY.pop("reflect-and-revise", None)


# ---------------------------------------------------------------------------
# Datasets
# ---------------------------------------------------------------------------


def test_create_dataset():
    from fleet_rlm.integrations.local_store import create_dataset

    ds = create_dataset(
        name="test-ds",
        row_count=42,
        format="jsonl",
        uri="/fake/path/test-ds.jsonl",
        module_slug="qa",
    )
    assert ds.id is not None
    assert ds.name == "test-ds"
    assert ds.row_count == 42
    assert ds.format == "jsonl"
    assert ds.module_slug == "qa"
    assert ds.uri == "/fake/path/test-ds.jsonl"

    ds2 = create_dataset(
        name="plain",
        row_count=10,
        format="json",
        uri="/fake/path/plain.json",
    )
    assert ds2.id is not None
    assert ds2.module_slug is None


def test_list_datasets_empty():
    from fleet_rlm.integrations.local_store import list_datasets

    items, total = list_datasets()
    assert items == []
    assert total == 0


def test_list_datasets_returns_items():
    from fleet_rlm.integrations.local_store import create_dataset, list_datasets

    create_dataset(name="a", row_count=1, format="json", uri="/a.json")
    create_dataset(name="b", row_count=2, format="jsonl", uri="/b.jsonl")
    create_dataset(name="c", row_count=3, format="json", uri="/c.json", module_slug="qa")

    items, total = list_datasets()
    assert total == 3
    assert len(items) == 3
    assert items[0].name == "c"


def test_list_datasets_filter_by_module():
    from fleet_rlm.integrations.local_store import create_dataset, list_datasets

    create_dataset(name="a", row_count=1, format="json", uri="/a.json")
    create_dataset(name="b", row_count=2, format="jsonl", uri="/b.jsonl", module_slug="qa")

    items, total = list_datasets(module_slug="qa")
    assert total == 1
    assert items[0].name == "b"


def test_list_datasets_pagination():
    from fleet_rlm.integrations.local_store import create_dataset, list_datasets

    for i in range(5):
        create_dataset(name=f"ds-{i}", row_count=i, format="json", uri=f"/{i}.json")

    page1, total1 = list_datasets(limit=2, offset=0)
    assert total1 == 5
    assert len(page1) == 2

    page2, total2 = list_datasets(limit=2, offset=2)
    assert total2 == 5
    assert len(page2) == 2

    page3, total3 = list_datasets(limit=2, offset=4)
    assert total3 == 5
    assert len(page3) == 1


@pytest.mark.asyncio
async def test_local_store_session_stats_are_supported_for_canonical_http_contract() -> None:
    from fleet_rlm.integrations.database.repository_identity import IdentityUpsertResult
    from fleet_rlm.integrations.local_store import LocalStore, add_turn, create_session

    store = LocalStore()
    identity = await store.upsert_identity(
        entra_tenant_id="tenant-local-stats",
        entra_user_id="user-local-stats",
        email="local@example.com",
    )
    assert isinstance(identity, IdentityUpsertResult)
    session = create_session(
        title="stats",
        model_name="local-model",
        owner_tenant=str(identity.tenant_id),
        owner_user=str(identity.user_id),
        workspace_id=str(identity.workspace_id),
    )
    assert session.id is not None
    add_turn(session.id, 99, "first", "one", tokens_in=2, tokens_out=3, latency_ms=5)
    add_turn(session.id, 99, "second", "two", tokens_in=7, tokens_out=11, latency_ms=13)

    stats = await store.get_session_stats(
        tenant_id=identity.tenant_id,
        session_id=uuid.UUID(int=session.id),
        user_id=identity.user_id,
        workspace_id=identity.workspace_id,
    )

    assert stats == {
        "total_tokens_in": 9,
        "total_tokens_out": 14,
        "total_latency_ms": 18,
        "model_breakdown": {"local-model": 2},
    }


def test_get_dataset_found_and_not_found():
    from fleet_rlm.integrations.local_store import create_dataset, get_dataset

    ds = create_dataset(name="x", row_count=7, format="jsonl", uri="/x.jsonl")
    assert ds.id is not None
    fetched = get_dataset(ds.id)
    assert fetched is not None
    assert fetched.name == "x"
    assert fetched.row_count == 7

    assert get_dataset(99999) is None


def test_get_dataset_root(tmp_path, monkeypatch):
    ds_root = str(tmp_path / "custom_root" / "datasets")
    monkeypatch.setenv("FLEET_RLM_DATASET_ROOT", ds_root)

    from fleet_rlm.integrations.local_store import get_dataset_root

    root = get_dataset_root()
    assert root == Path(ds_root).resolve()
    assert root.is_dir()

    monkeypatch.delenv("FLEET_RLM_DATASET_ROOT", raising=False)
    root2 = get_dataset_root()
    assert root2.name == "datasets"
    assert root2.parent.name == ".data"


# ---------------------------------------------------------------------------
# Evaluation results & prompt snapshots
# ---------------------------------------------------------------------------


def _create_run() -> int:
    """Helper: create an optimization run and return its id."""
    from fleet_rlm.integrations.local_store import create_optimization_run

    run = create_optimization_run(program_spec="test:eval")
    assert run.id is not None
    return run.id


def test_save_and_get_evaluation_results():
    from fleet_rlm.integrations.local_store import (
        get_evaluation_results,
        save_evaluation_results,
    )

    run_id = _create_run()
    results = [
        {
            "example_index": i,
            "input_data": f'{{"q": "question {i}"}}',
            "expected_output": f"answer {i}",
            "predicted_output": f"predicted {i}",
            "score": round(i * 0.2, 1),
        }
        for i in range(5)
    ]

    saved = save_evaluation_results(run_id, results)
    assert len(saved) == 5
    assert all(r.id is not None for r in saved)

    items, total = get_evaluation_results(run_id)
    assert total == 5
    assert len(items) == 5
    assert items[0].example_index == 0
    assert items[4].example_index == 4
    assert items[2].score == pytest.approx(0.4)


def test_save_evaluation_results_replaces_existing_rows():
    from fleet_rlm.integrations.local_store import (
        get_evaluation_results,
        save_evaluation_results,
    )

    run_id = _create_run()
    save_evaluation_results(
        run_id,
        [{"example_index": 0, "input_data": "{}", "score": 0.1}],
    )
    save_evaluation_results(
        run_id,
        [
            {"example_index": 0, "input_data": "{}", "score": 0.9},
            {"example_index": 1, "input_data": "{}", "score": 0.8},
        ],
    )

    items, total = get_evaluation_results(run_id)
    assert total == 2
    assert [item.score for item in items] == [pytest.approx(0.9), pytest.approx(0.8)]


def test_evaluation_results_pagination():
    from fleet_rlm.integrations.local_store import (
        get_evaluation_results,
        save_evaluation_results,
    )

    run_id = _create_run()
    results = [{"example_index": i, "input_data": f'{{"q": "{i}"}}', "score": 0.5} for i in range(5)]
    save_evaluation_results(run_id, results)

    page1, total1 = get_evaluation_results(run_id, limit=2, offset=0)
    assert total1 == 5
    assert len(page1) == 2
    assert page1[0].example_index == 0
    assert page1[1].example_index == 1

    page2, total2 = get_evaluation_results(run_id, limit=2, offset=2)
    assert total2 == 5
    assert len(page2) == 2
    assert page2[0].example_index == 2

    page3, total3 = get_evaluation_results(run_id, limit=2, offset=4)
    assert total3 == 5
    assert len(page3) == 1


def test_save_and_get_prompt_snapshots():
    from fleet_rlm.integrations.local_store import (
        get_prompt_snapshots,
        save_prompt_snapshots,
    )

    run_id = _create_run()
    snapshots = [
        {
            "predictor_name": "generate_answer",
            "prompt_type": "before",
            "prompt_text": "You are a helpful assistant.",
        },
        {
            "predictor_name": "generate_answer",
            "prompt_type": "after",
            "prompt_text": "You are an expert Q&A assistant. Be concise.",
        },
        {
            "predictor_name": "refine_answer",
            "prompt_type": "before",
            "prompt_text": "Refine the answer.",
        },
        {
            "predictor_name": "refine_answer",
            "prompt_type": "after",
            "prompt_text": "Refine the answer for clarity and accuracy.",
        },
    ]

    saved = save_prompt_snapshots(run_id, snapshots)
    assert len(saved) == 4
    assert all(s.id is not None for s in saved)

    fetched = get_prompt_snapshots(run_id)
    assert len(fetched) == 4
    assert fetched[0].predictor_name == "generate_answer"
    assert fetched[0].prompt_type == "after"
    assert fetched[1].predictor_name == "generate_answer"
    assert fetched[1].prompt_type == "before"
    assert fetched[2].predictor_name == "refine_answer"
    assert fetched[3].predictor_name == "refine_answer"


def test_save_prompt_snapshots_replaces_existing_rows():
    from fleet_rlm.integrations.local_store import (
        get_prompt_snapshots,
        save_prompt_snapshots,
    )

    run_id = _create_run()
    save_prompt_snapshots(
        run_id,
        [{"predictor_name": "predict", "prompt_type": "before", "prompt_text": "old"}],
    )
    save_prompt_snapshots(
        run_id,
        [
            {"predictor_name": "predict", "prompt_type": "before", "prompt_text": "new-before"},
            {"predictor_name": "predict", "prompt_type": "after", "prompt_text": "new-after"},
        ],
    )

    snapshots = get_prompt_snapshots(run_id)
    assert len(snapshots) == 2
    assert {snapshot.prompt_text for snapshot in snapshots} == {"new-before", "new-after"}


def test_evaluation_results_empty_run():
    from fleet_rlm.integrations.local_store import get_evaluation_results

    items, total = get_evaluation_results(99999)
    assert items == []
    assert total == 0


def test_prompt_snapshots_empty_run():
    from fleet_rlm.integrations.local_store import get_prompt_snapshots

    snapshots = get_prompt_snapshots(99999)
    assert snapshots == []


# ---------------------------------------------------------------------------
# Optimization runs
# ---------------------------------------------------------------------------


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


def test_complete_optimization_run_merges_review_metadata():
    from fleet_rlm.integrations.local_store import (
        complete_optimization_run,
        create_optimization_run,
        get_optimization_run,
    )

    run = create_optimization_run(
        program_spec="mod:test",
        metadata_json={"dataset_path": "datasets/longcot.jsonl"},
    )
    complete_optimization_run(
        run.id,
        train_examples=8,
        validation_examples=2,
        validation_score=0.85,
        metadata_json={"review_bundle": {"reflection_model": {"source": "delegate"}}},
    )
    fetched = get_optimization_run(run.id)
    assert fetched is not None
    metadata = json.loads(fetched.metadata_json or "{}")
    assert metadata["dataset_path"] == "datasets/longcot.jsonl"
    assert metadata["review_bundle"]["reflection_model"]["source"] == "delegate"


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


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


def test_create_session():
    from fleet_rlm.integrations.local_store import create_session

    sess = create_session(
        title="my-session",
        external_session_id="ext-123",
        owner_tenant="tenant-a",
        owner_user="user-1",
        workspace_id="ws-001",
    )
    assert sess.id is not None
    assert sess.external_session_id == "ext-123"
    assert sess.owner_tenant == "tenant-a"
    assert sess.owner_user == "user-1"
    assert sess.workspace_id == "ws-001"

    sess2 = create_session(title="legacy")
    assert sess2.owner_tenant is None
    assert sess2.owner_user is None
    assert sess2.external_session_id is None


def test_list_sessions_returns_active_only_by_default():
    from fleet_rlm.integrations.local_store import (
        archive_session,
        create_session,
        list_sessions,
    )

    s1 = create_session(title="active-one", owner_tenant="t", owner_user="u")
    s2 = create_session(title="active-two", owner_tenant="t", owner_user="u")
    s3 = create_session(title="archived", owner_tenant="t", owner_user="u")
    archive_session(s3.id, owner_tenant="t", owner_user="u")

    items, total = list_sessions(owner_tenant="t", owner_user="u")
    assert total == 2
    assert len(items) == 2
    ids = {s.id for s in items}
    assert s1.id in ids
    assert s2.id in ids


def test_list_sessions_filters_by_owner():
    from fleet_rlm.integrations.local_store import create_session, list_sessions

    create_session(title="t1-session", owner_tenant="t1", owner_user="u1")
    create_session(title="t2-session", owner_tenant="t2", owner_user="u2")

    items_t1, total_t1 = list_sessions(owner_tenant="t1", owner_user="u1")
    assert total_t1 == 1
    assert items_t1[0].owner_tenant == "t1"

    items_t2, total_t2 = list_sessions(owner_tenant="t2", owner_user="u2")
    assert total_t2 == 1
    assert items_t2[0].owner_tenant == "t2"


def test_list_sessions_search():
    from fleet_rlm.integrations.local_store import create_session, list_sessions

    create_session(title="alpha-task", owner_tenant="t", owner_user="u")
    create_session(title="beta-task", owner_tenant="t", owner_user="u")

    items, total = list_sessions(owner_tenant="t", owner_user="u", search="alpha")
    assert total == 1
    assert items[0].title == "alpha-task"


def test_list_sessions_pagination():
    from fleet_rlm.integrations.local_store import create_session, list_sessions

    for i in range(5):
        create_session(title=f"session-{i}", owner_tenant="t", owner_user="u")

    items, total = list_sessions(owner_tenant="t", owner_user="u", limit=2, offset=0)
    assert total == 5
    assert len(items) == 2

    items2, total2 = list_sessions(owner_tenant="t", owner_user="u", limit=2, offset=2)
    assert total2 == 5
    assert len(items2) == 2
    assert {s.id for s in items} & {s.id for s in items2} == set()


def test_get_chat_session():
    from fleet_rlm.integrations.local_store import create_session, get_chat_session

    sess = create_session(title="mine", owner_tenant="t", owner_user="u")
    result = get_chat_session(sess.id, owner_tenant="t", owner_user="u")
    assert result is not None
    assert result.id == sess.id

    sess2 = create_session(title="mine", owner_tenant="t1", owner_user="u1")
    assert get_chat_session(sess2.id, owner_tenant="t2", owner_user="u2") is None
    assert get_chat_session(99999) is None


def test_archive_session():
    from fleet_rlm.integrations.local_store import (
        SessionStatus,
        archive_session,
        create_session,
        get_chat_session,
    )

    sess = create_session(title="to-archive", owner_tenant="t", owner_user="u")
    assert archive_session(sess.id, owner_tenant="t", owner_user="u") is True

    result = get_chat_session(sess.id, owner_tenant="t", owner_user="u")
    assert result is not None
    assert result.status == SessionStatus.ARCHIVED

    sess2 = create_session(title="owned", owner_tenant="t1", owner_user="u1")
    assert archive_session(sess2.id, owner_tenant="t2", owner_user="u2") is False
    assert archive_session(99999) is False


def test_get_turns_paginated():
    from fleet_rlm.integrations.local_store import (
        add_turn,
        create_session,
        get_turns_paginated,
    )

    sess = create_session(title="chat")
    for i in range(5):
        add_turn(
            session_id=sess.id,
            turn_index=i,
            user_message=f"user-{i}",
            assistant_message=f"bot-{i}",
        )

    items, total = get_turns_paginated(sess.id, limit=2, offset=0)
    assert total == 5
    assert len(items) == 2
    assert items[0].turn_index < items[1].turn_index

    items2, total2 = get_turns_paginated(sess.id, limit=10, offset=3)
    assert total2 == 5
    assert len(items2) == 2


def test_get_turns_paginated_empty():
    from fleet_rlm.integrations.local_store import create_session, get_turns_paginated

    sess = create_session(title="empty")
    items, total = get_turns_paginated(sess.id)
    assert total == 0
    assert items == []


def test_export_session_as_dataset_basic(tmp_path, monkeypatch):
    """Export a session with valid turns produces a JSONL dataset."""
    monkeypatch.setenv("FLEET_RLM_DATASET_ROOT", str(tmp_path / "datasets"))

    from fleet_rlm.integrations.local_store import (
        add_turn,
        create_session,
        export_session_as_dataset,
    )

    sess = create_session(title="test-chat")
    add_turn(sess.id, 0, "What is 2+2?", "4")
    add_turn(sess.id, 1, "And 3+3?", "6")

    dataset = export_session_as_dataset(sess.id, "reflect-and-revise")

    assert dataset.id is not None
    assert dataset.row_count == 2
    assert dataset.format == "jsonl"
    assert dataset.module_slug == "reflect-and-revise"
    assert "session-" in dataset.name.lower() or "Session" in dataset.name

    lines = Path(dataset.uri).read_text().strip().splitlines()
    assert len(lines) == 2
    row0 = json.loads(lines[0])
    assert row0["user_request"] == "What is 2+2?"
    assert row0["next_action"] == "finalize"
    assert row0["rationale"] == "4"


def test_export_session_unknown_module():
    """Export with invalid module_slug raises ValueError."""
    from fleet_rlm.integrations.local_store import (
        create_session,
        export_session_as_dataset,
    )

    sess = create_session(title="chat")
    with pytest.raises(ValueError, match="Unknown module slug"):
        export_session_as_dataset(sess.id, "nonexistent-module")


def test_export_session_no_turns():
    """Export a session with no turns raises ValueError."""
    from fleet_rlm.integrations.local_store import (
        create_session,
        export_session_as_dataset,
    )

    sess = create_session(title="empty-chat")
    with pytest.raises(ValueError, match="no usable turns"):
        export_session_as_dataset(sess.id, "reflect-and-revise")


def test_export_session_skips_partial_turns(tmp_path, monkeypatch):
    """Turns without assistant_message are skipped."""
    monkeypatch.setenv("FLEET_RLM_DATASET_ROOT", str(tmp_path / "datasets"))

    from fleet_rlm.integrations.local_store import (
        add_turn,
        create_session,
        export_session_as_dataset,
    )

    sess = create_session(title="partial")
    add_turn(sess.id, 0, "hello", None)  # no assistant message
    add_turn(sess.id, 1, "real q", "real a")

    dataset = export_session_as_dataset(sess.id, "reflect-and-revise")
    assert dataset.row_count == 1


def test_create_transcript_dataset_basic(tmp_path, monkeypatch):
    monkeypatch.setenv("FLEET_RLM_DATASET_ROOT", str(tmp_path / "datasets"))

    from fleet_rlm.integrations.local_store import (
        create_transcript_dataset,
        get_dataset_root,
    )

    dataset = create_transcript_dataset(
        module_slug="reflect-and-revise",
        title="Recovered history",
        turns=[
            ("What is 2+2?", "4"),
            ("And 3+3?", "6"),
        ],
    )

    assert dataset.id is not None
    assert dataset.row_count == 2
    assert dataset.format == "jsonl"
    assert dataset.module_slug == "reflect-and-revise"
    assert dataset.name.startswith("Recovered history")
    dataset_path = Path(dataset.uri)
    assert dataset_path.parent == get_dataset_root()
    assert dataset_path.name.startswith("transcript-")
    assert dataset_path.suffix == ".jsonl"


def test_create_transcript_dataset_requires_usable_turns():
    from fleet_rlm.integrations.local_store import create_transcript_dataset

    with pytest.raises(ValueError, match="no usable turns"):
        create_transcript_dataset(
            module_slug="reflect-and-revise",
            title="Broken transcript",
            turns=[("Only user", None)],
        )
