from __future__ import annotations

import asyncio
import threading

import pytest

from fleet_rlm.daytona.turn_environment import _cleanup_scratch_before_releasing_invocation


@pytest.mark.asyncio
async def test_invocation_gate_stays_held_until_cancelled_scratch_cleanup_finishes() -> None:
    cleanup_started = threading.Event()
    finish_cleanup = threading.Event()
    released = asyncio.Event()

    def cleanup() -> None:
        cleanup_started.set()
        if not finish_cleanup.wait(timeout=5):
            raise TimeoutError("scratch cleanup test was not released")

    task = asyncio.create_task(_cleanup_scratch_before_releasing_invocation(cleanup, released.set))
    assert await asyncio.to_thread(cleanup_started.wait, 1)

    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert not released.is_set()

    finish_cleanup.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert released.is_set()


@pytest.mark.asyncio
async def test_invocation_gate_releases_after_scratch_cleanup_finishes() -> None:
    cleanup_finished = threading.Event()
    released = asyncio.Event()

    def cleanup() -> None:
        cleanup_finished.set()

    await _cleanup_scratch_before_releasing_invocation(cleanup, released.set)

    assert cleanup_finished.is_set()
    assert released.is_set()


@pytest.mark.asyncio
async def test_invocation_gate_releases_when_scratch_cleanup_fails() -> None:
    released = asyncio.Event()

    def cleanup() -> None:
        raise RuntimeError("scratch cleanup failed")

    with pytest.raises(RuntimeError, match="scratch cleanup failed"):
        await _cleanup_scratch_before_releasing_invocation(cleanup, released.set)

    assert released.is_set()


@pytest.mark.asyncio
async def test_invocation_gate_releases_when_no_scratch_cleanup_is_needed() -> None:
    released = asyncio.Event()

    await _cleanup_scratch_before_releasing_invocation(None, released.set)

    assert released.is_set()
