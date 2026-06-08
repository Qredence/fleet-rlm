"""Tests for ReplHookBridge progress relay fan-out."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest

from fleet_rlm.api.routers.ws.repl_bridge import ReplHookBridge
from fleet_rlm.runtime.agent.turn_progress_relay import TurnProgressRelay


def _enqueue_nonblocking(queue: asyncio.Queue[Any | None], item: Any) -> bool:
    try:
        queue.put_nowait(item)
        return True
    except asyncio.QueueFull:
        return False


@pytest.mark.asyncio
async def test_repl_bridge_forwards_interpreter_progress_to_relay() -> None:
    loop = asyncio.get_running_loop()
    relay = TurnProgressRelay(loop=loop)
    lifecycle = MagicMock()
    lifecycle.run_completed = False
    step_builder = MagicMock()
    step_builder.from_interpreter_hook.return_value = {"type": "repl", "label": "repl_result"}
    interpreter = MagicMock()

    bridge = ReplHookBridge(
        ws_loop=loop,
        lifecycle=lifecycle,
        step_builder=step_builder,
        interpreter=interpreter,
        enqueue_nonblocking=_enqueue_nonblocking,
        progress_relay=relay,
    )
    bridge.start()

    bridge._interpreter_hook(
        {
            "phase": "progress",
            "path": "/workspace/repo/README.md",
            "event_kind": "write",
        }
    )
    await asyncio.sleep(0)

    drained = relay.drain_nonblocking()
    assert any(event.text and "write" in event.text for event in drained)

    await bridge.stop()
