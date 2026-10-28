from __future__ import annotations

from fleet_rlm.session import SessionRuntime


def test_history_is_bounded_and_keeps_the_newest_turns_in_order() -> None:
    turns = [{"task": f"task-{index}", "answer": f"answer-{index}"} for index in range(10)]

    history = SessionRuntime._render_history(turns)

    assert "task-0" not in history
    assert history.index("task-2") < history.index("task-9")
    assert len(history) <= 16_000

    oversized = SessionRuntime._render_history([{"task": "task-" + "x" * 50_000, "answer": "tail" * 20_000}])
    assert len(oversized) <= 16_000
    assert oversized.endswith("tail")
