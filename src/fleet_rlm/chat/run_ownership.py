"""Shared claim-heartbeat and owned-cleanup helpers for Turn orchestration."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Any, TypeVar

from fleet_rlm.runtime.owned_effect import OwnedEffect

T = TypeVar("T")


@dataclass(slots=True)
class ClaimHeartbeat:
    task: asyncio.Task[None]
    lost: asyncio.Event


async def shield_cleanup(awaitable: Awaitable[T]) -> T:
    """Complete an owned awaitable even if the caller is repeatedly cancelled.

    Returns the awaitable's result so cancel/settle paths can keep a bool or
    receipt without a second cancel-resistant awaiter twin.
    """
    effect = OwnedEffect.start(awaitable)
    settled = await effect.settle()
    if settled.caller_cancelled:
        raise asyncio.CancelledError
    return settled.result()


def consume_task_exception(task: asyncio.Task[Any]) -> None:
    if not task.cancelled():
        with contextlib.suppress(BaseException):
            task.exception()


async def stop_heartbeat(heartbeat: ClaimHeartbeat | None) -> None:
    if heartbeat is None:
        return
    heartbeat.task.cancel()
    await asyncio.gather(heartbeat.task, return_exceptions=True)


__all__ = [
    "ClaimHeartbeat",
    "consume_task_exception",
    "shield_cleanup",
    "stop_heartbeat",
]
