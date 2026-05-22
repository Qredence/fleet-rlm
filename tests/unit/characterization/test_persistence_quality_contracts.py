"""Characterization tests for local persistence and offline optimization behavior."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import dspy
import pytest

from fleet_rlm.quality.module_registry import get_module_spec, list_module_metadata
from fleet_rlm.quality.optimization_runner import run_module_optimization


@pytest.fixture(autouse=True)
def _isolated_local_store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Use a fresh SQLite local store and dataset root for each characterization case."""
    monkeypatch.setenv("FLEET_RLM_LOCAL_DB_URL", f"sqlite:///{tmp_path / 'fleet-local.db'}")
    monkeypatch.setenv("FLEET_RLM_DATASET_ROOT", str(tmp_path / "datasets"))
    from fleet_rlm.integrations import local_store

    local_store._engines.clear()


def test_local_store_session_lifecycle_is_owner_scoped_and_archive_restore_is_soft() -> None:
    """SQLite local session behavior mirrors the currently supported durable lifecycle."""
    from fleet_rlm.integrations.local_store import (
        SessionStatus,
        archive_session,
        create_session,
        get_chat_session,
        list_sessions,
        restore_session,
        update_chat_session,
    )

    session = create_session(
        title="Characterize local sessions",
        external_session_id="external-characterization",
        owner_tenant="tenant-a",
        owner_user="user-a",
        workspace_id="workspace-a",
    )
    assert session.id is not None
    assert get_chat_session(session.id, owner_tenant="tenant-a", owner_user="user-a") is not None
    assert get_chat_session(session.id, owner_tenant="tenant-b", owner_user="user-a") is None

    updated = update_chat_session(
        session.id,
        owner_tenant="tenant-a",
        owner_user="user-a",
        title="Updated title",
        metadata_json={"ignored": True},
    )
    assert updated is not None
    assert updated.title == "Updated title"

    active_items, active_total = list_sessions(owner_tenant="tenant-a", owner_user="user-a")
    assert active_total == 1
    assert active_items[0].status == SessionStatus.ACTIVE

    assert archive_session(session.id, owner_tenant="tenant-a", owner_user="user-a") is True
    active_after_archive, active_total_after_archive = list_sessions(owner_tenant="tenant-a", owner_user="user-a")
    assert active_after_archive == []
    assert active_total_after_archive == 0

    archived_items, archived_total = list_sessions(
        owner_tenant="tenant-a",
        owner_user="user-a",
        status=SessionStatus.ARCHIVED,
    )
    assert archived_total == 1
    assert archived_items[0].id == session.id

    assert restore_session(session.id, owner_tenant="tenant-a", owner_user="user-a") is True
    restored_items, restored_total = list_sessions(owner_tenant="tenant-a", owner_user="user-a")
    assert restored_total == 1
    assert restored_items[0].status == SessionStatus.ACTIVE


def test_local_store_turns_are_append_only_and_paginated_in_monotonic_order() -> None:
    """Supplied turn_index is ignored in favor of the session monotonic counter."""
    from fleet_rlm.integrations.local_store import add_turn, create_session, get_turns, get_turns_paginated

    session = create_session(title="turn order")
    assert session.id is not None
    first = add_turn(session.id, 99, "first user", "first assistant", tokens_in=3, tokens_out=5, latency_ms=10)
    second = add_turn(session.id, 0, "second user", "second assistant", tokens_in=7, tokens_out=11, latency_ms=20)

    assert first.turn_index == 0
    assert second.turn_index == 1
    assert [turn.turn_index for turn in get_turns(session.id)] == [0, 1]

    page, total = get_turns_paginated(session.id, limit=1, offset=1)
    assert total == 2
    assert len(page) == 1
    assert page[0].turn_index == 1
    assert page[0].user_message == "second user"


def test_longcot_module_registry_is_offline_optimization_source_of_truth() -> None:
    """Current module registry exposes longcot-reasoner for CLI/API without live chat coupling."""
    modules = {module["slug"]: module for module in list_module_metadata()}
    spec = get_module_spec("longcot-reasoner")

    assert "longcot-reasoner" in modules
    assert spec.module_slug == "longcot-reasoner"
    assert spec.program_spec == "fleet_rlm.runtime.agent.signatures:LongCoTQASignature"
    assert spec.input_keys == ["question"]
    assert set(spec.required_dataset_keys) == {"question", "answer"}
    assert spec.metric_name == "longcot_qa_metric"


def test_run_module_optimization_writes_reviewable_artifacts_without_mlflow_requirement(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Shared GEPA runner stays an offline path and persists artifact/manifest evidence."""
    dataset = tmp_path / "longcot.jsonl"
    rows = [
        {
            "question_id": f"q-{index}",
            "domain": "math",
            "difficulty": "easy",
            "question": f"What is {index}+{index}?",
            "answer": str(index + index),
        }
        for index in range(6)
    ]
    dataset.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    output_path = tmp_path / "optimized" / "longcot.json"

    class _FakeGEPA:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

        def compile(self, program: object, trainset: object = None, valset: object = None) -> object:
            class _Optimized:
                def save(self, path: str) -> None:
                    Path(path).parent.mkdir(parents=True, exist_ok=True)
                    Path(path).write_text('{"optimized": true}')

                def __call__(self, **kwargs: object) -> object:
                    return dspy.Prediction(answer=str(kwargs.get("question", "")))

                def named_predictors(self) -> list[tuple[str, object]]:
                    return []

            assert trainset
            assert valset
            return _Optimized()

    class _FakeEvaluate:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

        def __call__(self, program: object) -> float:
            return 0.75

    monkeypatch.setattr("dspy.teleprompt.GEPA", _FakeGEPA, raising=False)
    monkeypatch.setattr("dspy.Evaluate", _FakeEvaluate, raising=False)
    monkeypatch.setattr("fleet_rlm.quality.optimization_runner._resolve_reflection_lm", lambda: MagicMock())
    monkeypatch.setattr("fleet_rlm.quality.optimization_runner._ensure_dspy_configured", lambda: None)
    monkeypatch.setattr(
        "fleet_rlm.quality.optimization_runner._evaluate_per_example",
        lambda program, examples, metric: [
            {
                "example_index": index,
                "input_data": "{}",
                "expected_output": "",
                "predicted_output": "",
                "score": 0.75,
            }
            for index, _example in enumerate(examples)
        ],
    )

    with patch("mlflow.start_run") as start_run:
        spec = get_module_spec("longcot-reasoner")
        assert spec is not None
        result = run_module_optimization(
            spec,
            dataset_path=dataset,
            auto="light",
            train_ratio=0.5,
            output_path=output_path,
        )

    assert start_run.call_count == 0
    assert result["optimizer"] == "GEPA"
    assert result["module_slug"] == "longcot-reasoner"
    assert result["train_examples"] == 3
    assert result["validation_examples"] == 3
    assert result["validation_score"] == pytest.approx(0.75)
    assert Path(result["output_path"]).read_text() == '{"optimized": true}'
    manifest = json.loads(Path(result["manifest_path"]).read_text())
    assert manifest["optimizer"] == "GEPA"
    assert manifest["module_slug"] == "longcot-reasoner"
