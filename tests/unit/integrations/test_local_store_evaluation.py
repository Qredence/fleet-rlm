"""Tests for evaluation result and prompt snapshot persistence in local_store."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_db(monkeypatch, tmp_path):
    """Point local_store at a fresh temporary SQLite database."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setenv("FLEET_RLM_LOCAL_DB_URL", f"sqlite:///{db_path}")
    from fleet_rlm.integrations import local_store

    local_store._engines.clear()


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


def test_evaluation_results_pagination():
    from fleet_rlm.integrations.local_store import (
        get_evaluation_results,
        save_evaluation_results,
    )

    run_id = _create_run()
    results = [
        {
            "example_index": i,
            "input_data": f'{{"q": "{i}"}}',
            "score": 0.5,
        }
        for i in range(5)
    ]
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

    # Ordered by predictor_name, prompt_type
    assert fetched[0].predictor_name == "generate_answer"
    assert fetched[0].prompt_type == "after"
    assert fetched[1].predictor_name == "generate_answer"
    assert fetched[1].prompt_type == "before"
    assert fetched[2].predictor_name == "refine_answer"
    assert fetched[3].predictor_name == "refine_answer"


def test_evaluation_results_empty_run():
    from fleet_rlm.integrations.local_store import get_evaluation_results

    items, total = get_evaluation_results(99999)
    assert items == []
    assert total == 0


def test_prompt_snapshots_empty_run():
    from fleet_rlm.integrations.local_store import get_prompt_snapshots

    snapshots = get_prompt_snapshots(99999)
    assert snapshots == []
