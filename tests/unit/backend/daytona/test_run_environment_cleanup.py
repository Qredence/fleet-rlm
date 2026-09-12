"""Deterministic Daytona cleanup settlement regressions."""

from __future__ import annotations

import asyncio

from fleet_rlm.composition import daytona_run_preparation as run_environment


def test_settled_resource_cleanup_snapshot_iteration() -> None:
    """Set removal must not iterate the live registry while callbacks mutate it."""
    resources = object.__new__(run_environment.DaytonaRuntimeResources)
    resources._resource_cleanup_owners = set()
    loop = asyncio.new_event_loop()
    tasks = [loop.create_future() for _ in range(3)]
    registry = resources._resource_cleanup_owners
    try:
        for task in tasks:
            registry.add((task, "sandbox"))
        # Simulate nested settlement callbacks while the first callback is
        # computing its exact owner membership.
        tasks[1].set_result(None)
        resources._settled_resource_cleanup("sandbox", tasks[1])
        assert (tasks[1], "sandbox") not in registry
        assert (tasks[0], "sandbox") in registry
        assert (tasks[2], "sandbox") in registry
    finally:
        loop.close()
