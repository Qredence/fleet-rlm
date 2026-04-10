from __future__ import annotations

import asyncio

from fleet_rlm.agent_host.startup_status import (
    build_startup_status_event,
    cancel_startup_status_task,
    emit_delayed_startup_status,
)


def test_build_startup_status_event_uses_canonical_payload() -> None:
    event = build_startup_status_event()

    assert event.kind == "status"
    assert event.text == "Preparing Daytona workspace..."
    assert event.payload == {
        "phase": "startup",
        "runtime": {"runtime_mode": "daytona_pilot"},
    }


def test_emit_delayed_startup_status_emits_after_delay() -> None:
    async def scenario() -> None:
        emitted = []

        async def emit_event(event) -> None:
            emitted.append(event)

        await emit_delayed_startup_status(delay_seconds=0.0, emit_event=emit_event)

        assert len(emitted) == 1
        assert emitted[0].kind == "status"
        assert emitted[0].text == "Preparing Daytona workspace..."

    asyncio.run(scenario())


def test_cancel_startup_status_task_stops_pending_emit() -> None:
    async def scenario() -> None:
        emitted = []

        async def emit_event(event) -> None:
            emitted.append(event)

        task = asyncio.create_task(
            emit_delayed_startup_status(delay_seconds=60.0, emit_event=emit_event)
        )

        await asyncio.sleep(0)
        await cancel_startup_status_task(task)

        assert emitted == []
        assert task.cancelled() is True

    asyncio.run(scenario())
