"""Deterministic Daytona cleanup settlement regressions."""

from __future__ import annotations

import asyncio

from fleet_rlm.runtime.daytona import run_environment


def test_settled_resource_cleanup_snapshot_iteration() -> None:
    """Set removal must not iterate the live registry while callbacks mutate it."""
    resources = object()
    loop = asyncio.new_event_loop()
    tasks = [loop.create_future() for _ in range(3)]
    registry = run_environment._RESOURCE_CLEANUP_OWNERS
    previous = set(registry)
    registry.clear()
    try:
        for task in tasks:
            registry.add((task, resources, "sandbox"))
        # Simulate nested settlement callbacks while the first callback is
        # computing its exact owner membership.
        tasks[1].set_result(None)
        run_environment.DaytonaRuntimeResources._settled_resource_cleanup(resources, "sandbox", tasks[1])
        assert (tasks[1], resources, "sandbox") not in registry
        assert (tasks[0], resources, "sandbox") in registry
        assert (tasks[2], resources, "sandbox") in registry
    finally:
        registry.clear()
        registry.update(previous)
        loop.close()
