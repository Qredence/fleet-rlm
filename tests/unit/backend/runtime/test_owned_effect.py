from __future__ import annotations

import asyncio

import pytest

from fleet_rlm.runtime.owned_effect import OwnedEffect


@pytest.mark.asyncio
async def test_owned_effect_preserves_success_and_repeated_settlement() -> None:
    effect = OwnedEffect.start(asyncio.sleep(0, result="ok"))

    first = await effect.settle()
    second = await effect.settle()

    assert first.done is True
    assert first.pending is False
    assert first.timed_out is False
    assert first.result() == "ok"
    assert second.result() == "ok"


@pytest.mark.asyncio
async def test_owned_effect_preserves_failure_on_repeated_settlement() -> None:
    async def fail() -> str:
        raise ValueError("owned effect failed")

    effect = OwnedEffect.start(fail())

    with pytest.raises(ValueError, match="owned effect failed"):
        await effect.settle()
    with pytest.raises(ValueError, match="owned effect failed"):
        await effect.settle()


@pytest.mark.asyncio
async def test_waiter_cancellation_does_not_cancel_owned_effect() -> None:
    release = asyncio.Event()

    async def work() -> str:
        """Wait for the release event, then return the settled result.
        
        Returns:
        	str: The string "settled".
        """
        await release.wait()
        return "settled"

    effect = OwnedEffect.start(work())
    waiter = asyncio.create_task(effect.settle())
    await asyncio.sleep(0)
    waiter.cancel()
    await asyncio.sleep(0)

    assert waiter.done() is False
    assert effect.done() is False

    release.set()
    settled = await waiter
    assert settled.caller_cancelled is True
    assert settled.result() == "settled"


@pytest.mark.asyncio
async def test_waiter_cancellation_does_not_hide_later_effect_failure() -> None:
    release = asyncio.Event()

    async def fail_later() -> str:
        """
        Wait for the release signal, then raise a late failure.
        
        Returns:
        	str: This coroutine does not return normally.
        """
        await release.wait()
        raise RuntimeError("late failure")

    effect = OwnedEffect.start(fail_later())
    waiter = asyncio.create_task(effect.settle())
    await asyncio.sleep(0)
    waiter.cancel()
    await asyncio.sleep(0)
    release.set()

    with pytest.raises(RuntimeError, match="late failure") as caught:
        await waiter
    assert str(caught.value) == "late failure"
    assert effect.caller_cancelled is True


@pytest.mark.asyncio
async def test_bounded_wait_leaves_pending_effect_owned() -> None:
    release = asyncio.Event()

    async def work() -> str:
        await release.wait()
        return "later"

    effect = OwnedEffect.start(work())
    pending = await effect.settle(timeout=0.001)

    assert pending.timed_out is True
    assert pending.pending is True
    assert effect.done() is False

    release.set()
    settled = await effect.settle()
    assert settled.result() == "later"
