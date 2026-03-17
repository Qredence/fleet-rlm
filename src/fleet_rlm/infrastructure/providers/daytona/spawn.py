"""Guide-native semantic and recursive helper adapters for Daytona RLM."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fleet_rlm.infrastructure.providers.daytona.runner import DaytonaRLMRunner
    from .types import ChildTaskResult, RecursiveTaskSpec


def llm_query(
    runner: DaytonaRLMRunner,
    *,
    task_spec: RecursiveTaskSpec,
) -> str:
    """Run one semantic host-LM query and return plain text."""

    return runner.run_semantic_task(task_spec=task_spec)


def llm_query_batched(
    runner: DaytonaRLMRunner,
    *,
    task_specs: list[RecursiveTaskSpec],
) -> list[str]:
    """Run semantic host-LM queries with bounded concurrency and stable ordering."""

    return runner.run_semantic_tasks_batched(task_specs=task_specs)


def rlm_query(
    runner: DaytonaRLMRunner,
    *,
    parent_id: str,
    depth: int,
    task_spec: RecursiveTaskSpec,
) -> ChildTaskResult:
    """Run one true recursive child Daytona query."""

    return runner.run_child_task(
        parent_id=parent_id,
        depth=depth,
        task_spec=task_spec,
    )


def rlm_query_batched(
    runner: DaytonaRLMRunner,
    *,
    parent_id: str,
    depth: int,
    task_specs: list[RecursiveTaskSpec],
) -> list[ChildTaskResult]:
    """Run recursive child Daytona queries with bounded concurrency."""

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
