"""Tests for TurnProgressRelay."""

from __future__ import annotations

import asyncio

import pytest

from fleet_rlm.runtime.agent.turn_progress_relay import TurnProgressRelay
from fleet_rlm.runtime.events import RuntimeEvent


@pytest.mark.asyncio
async def test_relay_emits_and_drains_events() -> None:
    loop = asyncio.get_running_loop()
    relay = TurnProgressRelay(loop=loop)
    event = RuntimeEvent.status("hello", payload={"phase": "rlm_progress"})
    await relay.emit(event)
    drained = relay.drain_nonblocking()
    assert len(drained) == 1
    assert drained[0].text == "hello"
    assert relay.was_seen(event)


@pytest.mark.asyncio
async def test_relay_emit_threadsafe_from_worker_thread() -> None:
    loop = asyncio.get_running_loop()
    relay = TurnProgressRelay(loop=loop)
    event = RuntimeEvent.reasoning("thinking")

    def _worker() -> None:
        relay.emit_threadsafe(event)

    await asyncio.to_thread(_worker)
    await asyncio.sleep(0.05)
    drained = relay.drain_nonblocking()
    assert any(item.text == "thinking" for item in drained)
