"""Recursive spawn helpers for the experimental Daytona-backed RLM pilot."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .runner import DaytonaRLMRunner


def rlm_query(
    runner: "DaytonaRLMRunner", *, parent_id: str, depth: int, task: str
) -> str:
    """Run one child query and return the truncated string result."""

    return runner.run_child_task(parent_id=parent_id, depth=depth, task=task)


def rlm_query_batched(
    runner: "DaytonaRLMRunner",
    *,
    parent_id: str,
    depth: int,
    tasks: list[str],
) -> list[str]:
    """Run child queries with bounded concurrency and stable ordering."""

    if not tasks:
        return []

    max_workers = max(1, min(len(tasks), runner.budget.batch_concurrency))
    results: dict[int, str] = {}

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(
                runner.run_child_task,
                parent_id=parent_id,
                depth=depth,
                task=task,
            ): idx
            for idx, task in enumerate(tasks)
        }
        for future in as_completed(future_map):
            idx = future_map[future]
            results[idx] = future.result()

    return [results[idx] for idx in range(len(tasks))]
