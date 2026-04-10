"""Delayed startup-status policy isolated from websocket connection handling."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import datetime, timezone

from fleet_rlm.worker import WorkspaceEvent

EmitStartupEvent = Callable[[WorkspaceEvent], Awaitable[None]]


def build_startup_status_event() -> WorkspaceEvent:
    """Return the canonical delayed startup status event."""

    return WorkspaceEvent(
        kind="status",
        text="Preparing Daytona workspace...",
        payload={
            "phase": "startup",
            "runtime": {"runtime_mode": "daytona_pilot"},
        },
        timestamp=datetime.now(timezone.utc),
    )


async def emit_delayed_startup_status(
    *,
    delay_seconds: float,
    emit_event: EmitStartupEvent,
) -> None:
    """Emit the startup-status event after the configured first-frame delay."""

    await asyncio.sleep(delay_seconds)
    await emit_event(build_startup_status_event())


async def cancel_startup_status_task(task: asyncio.Task[None] | None) -> None:
    """Cancel the delayed startup task when startup completes first."""

    if task is None:
        return
    task.cancel()
    with suppress(asyncio.CancelledError):
        _ = await task
