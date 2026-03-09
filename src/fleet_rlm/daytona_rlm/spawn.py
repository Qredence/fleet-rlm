"""Guide-native recursive spawn helpers for the experimental Daytona-backed RLM pilot."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .runner import DaytonaRLMRunner
    from .types import ChildTaskResult, RecursiveTaskSpec


def llm_query(
    runner: "DaytonaRLMRunner",
    *,
    parent_id: str,
    depth: int,
    task_spec: "RecursiveTaskSpec",
) -> "ChildTaskResult":
    """Run one child query and return the normalized child result."""

    return runner.run_child_task(
        parent_id=parent_id,
        depth=depth,
        task_spec=task_spec,
    )


def llm_query_batched(
    runner: "DaytonaRLMRunner",
    *,
    parent_id: str,
    depth: int,
    task_specs: list["RecursiveTaskSpec"],
) -> list["ChildTaskResult"]:
    """Run child queries with bounded concurrency and stable ordering."""

    if not task_specs:
        return []

    max_workers = max(1, min(len(task_specs), runner.budget.batch_concurrency))
    results: dict[int, ChildTaskResult] = {}

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(
                runner.run_child_task,
                parent_id=parent_id,
                depth=depth,
                task_spec=task_spec,
            ): idx
            for idx, task_spec in enumerate(task_specs)
        }
        for future in as_completed(future_map):
            idx = future_map[future]
            results[idx] = future.result()

    return [results[idx] for idx in range(len(task_specs))]


def rlm_query(
    runner: "DaytonaRLMRunner",
    *,
    parent_id: str,
    depth: int,
    task_spec: "RecursiveTaskSpec",
) -> "ChildTaskResult":
    """Compatibility alias for the guide-native llm_query helper."""

    return llm_query(
        runner,
        parent_id=parent_id,
        depth=depth,
        task_spec=task_spec,
    )


def rlm_query_batched(
    runner: "DaytonaRLMRunner",
    *,
    parent_id: str,
    depth: int,
    task_specs: list["RecursiveTaskSpec"],
) -> list["ChildTaskResult"]:
    """Compatibility alias for the guide-native llm_query_batched helper."""

    return llm_query_batched(
        runner,
        parent_id=parent_id,
        depth=depth,
        task_specs=task_specs,
    )
