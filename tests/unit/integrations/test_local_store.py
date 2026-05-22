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


# ---------------------------------------------------------------------------
# VAL-PERSIST-013: SQLite initializes and persists across connections
# ---------------------------------------------------------------------------


def test_sqlite_initializes_and_persists_across_connections(tmp_path):
    """VAL-PERSIST-013: Data written via one engine handle is readable via a second handle."""
    import os

    db_path = str(tmp_path / "cross_conn.db")
    os.environ["FLEET_RLM_LOCAL_DB_URL"] = f"sqlite:///{db_path}"
    from fleet_rlm.integrations import local_store

    local_store._engines.clear()

    # Write via first handle
    sess1 = local_store.create_session(title="cross-conn-test", owner_tenant="t", owner_user="u")
    assert sess1.id is not None

    # Clear engine cache to force new connection (simulates new process/engine open)
    local_store._engines.clear()

    # Read via fresh second handle
    second_engine = local_store.get_engine()
    assert second_engine is not None

    result = local_store.get_chat_session(sess1.id, owner_tenant="t", owner_user="u")
    assert result is not None
    assert result.title == "cross-conn-test"


# ---------------------------------------------------------------------------
# VAL-PERSIST-014: SQLite local chat lifecycle matches supported subset
# ---------------------------------------------------------------------------


def test_local_chat_archive_restore_cycle():
    """VAL-PERSIST-014: Local store supports archive+restore with active/archived semantics."""
    from fleet_rlm.integrations.local_store import (
        SessionStatus,
        archive_session,
        create_session,
        get_chat_session,
        list_sessions,
        restore_session,
    )

    s = create_session(title="lifecycle-test", owner_tenant="t", owner_user="u")
    assert s.id is not None

    # Archive
    assert archive_session(s.id, owner_tenant="t", owner_user="u") is True
    row = get_chat_session(s.id, owner_tenant="t", owner_user="u")
    assert row is not None
    assert row.status == SessionStatus.ARCHIVED

    # Archived not in active listings
    active, total = list_sessions(owner_tenant="t", owner_user="u")
    assert all(item.id != s.id for item in active)

    # Restore
    assert restore_session(s.id, owner_tenant="t", owner_user="u") is True
    restored = get_chat_session(s.id, owner_tenant="t", owner_user="u")
    assert restored is not None
    assert restored.status == SessionStatus.ACTIVE

    # Active again in listings
    active_after, _ = list_sessions(owner_tenant="t", owner_user="u")
    assert any(item.id == s.id for item in active_after)

    # Ownership filter: wrong tenant returns None
    assert get_chat_session(s.id, owner_tenant="wrong", owner_user="u") is None


# ---------------------------------------------------------------------------
# VAL-PERSIST-015: SQLite local turns, stats, and transcript export
# ---------------------------------------------------------------------------


def test_local_session_stats_and_transcript_export(tmp_path, monkeypatch):
    """VAL-PERSIST-015: local stats match persisted turns; transcript export produces JSONL."""
    monkeypatch.setenv("FLEET_RLM_DATASET_ROOT", str(tmp_path / "datasets"))

    from fleet_rlm.integrations.local_store import (
        add_turn,
        create_session,
        export_session_as_dataset,
        get_local_session_stats,
        get_turns_paginated,
    )

    sess = create_session(title="stats-export-test", model_name="test-model")
    turns_data = [
        ("q0", "a0", 5, 10, 100),
        ("q1", "a1", 7, 14, 200),
        ("q2", "a2", 3, 6, 50),
    ]
    for i, (um, am, ti, to, lat) in enumerate(turns_data):
        add_turn(sess.id, i, um, am, tokens_in=ti, tokens_out=to, latency_ms=lat)

    # Check turn ordering
    turns, total = get_turns_paginated(sess.id, limit=100, offset=0)
    assert total == 3
    assert [t.turn_index for t in turns] == [0, 1, 2]

    # Stats should match aggregated turn data
    stats = get_local_session_stats(sess.id)
    assert stats is not None
    assert stats["total_tokens_in"] == 15
    assert stats["total_tokens_out"] == 30
    assert stats["total_latency_ms"] == 350
    assert "test-model" in stats["model_breakdown"]

    # Transcript export writes JSONL
    dataset = export_session_as_dataset(sess.id, "reflect-and-revise")
    assert dataset.row_count == 3
    assert dataset.format == "jsonl"
    assert dataset.module_slug == "reflect-and-revise"

    import json
    from pathlib import Path

    lines = Path(dataset.uri).read_text().strip().splitlines()
    assert len(lines) == 3
    row0 = json.loads(lines[0])
    assert row0["user_request"] == "q0"


# ---------------------------------------------------------------------------
# VAL-PERSIST-016: SQLite local optimization state survives engine reset
# ---------------------------------------------------------------------------


def test_local_optimization_state_survives_engine_reset(tmp_path, monkeypatch):
    """VAL-PERSIST-016: optimization run/phase/completion state is readable after engine clear."""
    db_path = str(tmp_path / "optim_reset.db")
    ds_root = str(tmp_path / "datasets")
    monkeypatch.setenv("FLEET_RLM_LOCAL_DB_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("FLEET_RLM_DATASET_ROOT", ds_root)

    from fleet_rlm.integrations import local_store

    local_store._engines.clear()

    # Write state
    run = local_store.create_optimization_run(program_spec="test:module", module_slug="reflect-and-revise")
    local_store.update_optimization_run_phase(run.id, phase="compiling")
    local_store.save_evaluation_results(
        run.id,
        [{"example_index": 0, "input_data": '{"q":"hi"}', "score": 0.8}],
    )
    local_store.save_prompt_snapshots(
        run.id,
        [{"predictor_name": "pred", "prompt_type": "before", "prompt_text": "original-prompt"}],
    )
    local_store.complete_optimization_run(
        run.id,
        train_examples=5,
        validation_examples=2,
        validation_score=0.88,
        manifest_path="/tmp/manifest.json",
    )

    # Clear engine (simulate process restart)
    local_store._engines.clear()

    # Read back
    fetched = local_store.get_optimization_run(run.id)
    assert fetched is not None
    assert fetched.status.value == "completed"
    assert fetched.phase == "completed"
    assert fetched.validation_score == pytest.approx(0.88)
    assert fetched.train_examples == 5

    eval_results, total = local_store.get_evaluation_results(run.id)
    assert total == 1
    assert eval_results[0].score == pytest.approx(0.8)

    snapshots = local_store.get_prompt_snapshots(run.id)
    assert len(snapshots) == 1
    assert snapshots[0].prompt_text == "original-prompt"

    # Stale recovery is idempotent after process reset
    local_store.create_optimization_run(program_spec="test:running")
    recovered_first = local_store.recover_stale_optimization_runs()
    assert recovered_first >= 1

    local_store._engines.clear()
    recovered_second = local_store.recover_stale_optimization_runs()
    assert recovered_second == 0


# ---------------------------------------------------------------------------
# VAL-PERSIST-017: Local temporary files bounded to caller-provided test roots
# ---------------------------------------------------------------------------


def test_local_temp_files_bounded_to_test_roots(tmp_path, monkeypatch):
    """VAL-PERSIST-017: SQLite file and JSONL exports stay inside the tmp_path root."""
    from pathlib import Path  # noqa: PLC0415 - local import is fine in test helpers

    db_path = str(tmp_path / "bounded.db")
    ds_root = str(tmp_path / "datasets")
    monkeypatch.setenv("FLEET_RLM_LOCAL_DB_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("FLEET_RLM_DATASET_ROOT", ds_root)

    from fleet_rlm.integrations import local_store

    local_store._engines.clear()

    # SQLite file must be under tmp_path
    local_store.get_engine()
    db_url = local_store._resolve_db_url()
    resolved_db = Path(db_url.replace("sqlite:///", "")).resolve()
    assert str(resolved_db).startswith(str(tmp_path.resolve()))

    # JSONL export must be under the caller-provided dataset root (which is also under tmp_path)
    sess = local_store.create_session(title="bounded-test")
    local_store.add_turn(sess.id, 0, "q", "a")
    dataset = local_store.export_session_as_dataset(sess.id, "reflect-and-revise")
    export_path = Path(dataset.uri).resolve()
    assert str(export_path).startswith(str(tmp_path.resolve()))

    # Dataset root itself must also be under tmp_path
    dataset_root = local_store.get_dataset_root()
    assert str(dataset_root).startswith(str(tmp_path.resolve()))

    # The SQLite db file must exist exactly at the monkeypatched path
    assert Path(db_path).exists()


# ---------------------------------------------------------------------------
# VAL-PERSIST-019: Multi-row SQLite writes are atomic
# ---------------------------------------------------------------------------


def test_local_evaluation_results_replacement_is_atomic():
    """VAL-PERSIST-019: save_evaluation_results replaces prior rows without partial state."""
    from fleet_rlm.integrations.local_store import (
        create_optimization_run,
        get_evaluation_results,
        get_prompt_snapshots,
        save_evaluation_results,
        save_prompt_snapshots,
    )

    run = create_optimization_run(program_spec="atomic:test")

    # First save: 2 results
    save_evaluation_results(
        run.id,
        [
            {"example_index": 0, "input_data": '{"q":"old"}', "score": 0.1},
            {"example_index": 1, "input_data": '{"q":"old"}', "score": 0.2},
        ],
    )
    first_results, first_total = get_evaluation_results(run.id)
    assert first_total == 2

    # Replace with 3 results — old rows must be completely gone
    save_evaluation_results(
        run.id,
        [
            {"example_index": 0, "input_data": '{"q":"new"}', "score": 0.9},
            {"example_index": 1, "input_data": '{"q":"new"}', "score": 0.8},
            {"example_index": 2, "input_data": '{"q":"new"}', "score": 0.7},
        ],
    )
    replaced_results, replaced_total = get_evaluation_results(run.id)
    assert replaced_total == 3
    assert [r.example_index for r in replaced_results] == [0, 1, 2]
    assert all(r.score >= 0.7 for r in replaced_results)

    # Prompt snapshot replacement
    save_prompt_snapshots(
        run.id,
        [{"predictor_name": "pred", "prompt_type": "before", "prompt_text": "old-prompt"}],
    )
    save_prompt_snapshots(
        run.id,
        [
            {"predictor_name": "pred", "prompt_type": "before", "prompt_text": "new-before"},
            {"predictor_name": "pred", "prompt_type": "after", "prompt_text": "new-after"},
        ],
    )
    snapshots = get_prompt_snapshots(run.id)
    assert len(snapshots) == 2
    texts = {s.prompt_text for s in snapshots}
    assert "new-before" in texts
    assert "new-after" in texts
    assert "old-prompt" not in texts


# ---------------------------------------------------------------------------
# VAL-PERSIST-020: Unsupported local persistence capabilities are explicit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unsupported_trace_operations_raise_explicit_errors() -> None:
    """VAL-PERSIST-020: store_trace_feedback and store_rlm_trace raise UnsupportedLocalCapabilityError."""
    import uuid as _uuid

    from fleet_rlm.integrations.local_store import LocalStore
    from fleet_rlm.integrations.persistence_protocol import UnsupportedLocalCapabilityError

    store = LocalStore()
    tenant = _uuid.uuid4()

    # store_trace_feedback must raise, not return a sentinel UUID
    with pytest.raises(UnsupportedLocalCapabilityError) as exc_info:
        await store.store_trace_feedback(
            tenant_id=tenant,
            trace_id="mlflow-trace-abc123",
            is_correct=True,
        )
    assert exc_info.value.capability == "store_trace_feedback"

    # store_rlm_trace must raise, not return uuid.UUID(int=0)
    with pytest.raises(UnsupportedLocalCapabilityError) as exc_info2:
        await store.store_rlm_trace(
            tenant_id=tenant,
            run_id=_uuid.uuid4(),
            trace_id="rlm-child-test123",
        )
    assert exc_info2.value.capability == "store_rlm_trace"


@pytest.mark.asyncio
async def test_unsupported_run_artifact_write_fails_explicitly() -> None:
    """VAL-PERSIST-020: create_run, append_step, and store_artifact raise in local mode."""
    import uuid as _uuid

    from fleet_rlm.integrations.database.models_enums import ArtifactKind
    from fleet_rlm.integrations.database.repository_chat import (
        ArtifactCreateRequest,
        RunCreateRequest,
        RunStepCreateRequest,
        RunStepType,
    )
    from fleet_rlm.integrations.local_store import LocalStore

    store = LocalStore()
    tenant = _uuid.uuid4()

    with pytest.raises(NotImplementedError):
        await store.create_run(
            RunCreateRequest(
                tenant_id=tenant,
                created_by_user_id=None,
                external_run_id="run-local-test",
            )
        )

    with pytest.raises(NotImplementedError):
        await store.append_step(
            RunStepCreateRequest(
                tenant_id=tenant,
                run_id=_uuid.uuid4(),
                step_index=0,
                step_type=RunStepType.LLM_CALL,
            )
        )

    with pytest.raises(NotImplementedError):
        await store.store_artifact(
            ArtifactCreateRequest(
                tenant_id=tenant,
                kind=ArtifactKind.TRACE,
                uri="memory://test/trace.json",
            )
        )


@pytest.mark.asyncio
async def test_unsupported_memory_write_fails_explicitly() -> None:
    """VAL-PERSIST-020: store_memory_item raises in local mode."""
    import uuid as _uuid

    from fleet_rlm.integrations.database.models_enums import (
        MemoryKind,
        MemoryScope,
        MemorySource,
    )
    from fleet_rlm.integrations.database.repository_memory import MemoryItemCreateRequest
    from fleet_rlm.integrations.local_store import LocalStore

    store = LocalStore()

    with pytest.raises(NotImplementedError):
        await store.store_memory_item(
            MemoryItemCreateRequest(
                tenant_id=_uuid.uuid4(),
                scope=MemoryScope.RUN,
                scope_id="run-id",
                kind=MemoryKind.SUMMARY,
                source=MemorySource.SYSTEM,
                content_text="test memory",
            )
        )
