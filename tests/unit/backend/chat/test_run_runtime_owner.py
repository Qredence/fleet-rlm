"""QRE-158 contracts for the claimed-Run ownership state machine."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import pytest

from fleet_rlm.chat.run_runtime_owner import (
    OwnershipComponentReceipt,
    RunLifetimeReceipt,
    RunOwnership,
    RunOwnershipState,
    RunOwnershipTransitionError,
)


@dataclass
class _Stream:
    values: list[str]
    close_count: int = 0
    next_count: int = 0

    def __aiter__(self) -> _Stream:
        return self

    async def __anext__(self) -> str:
        self.next_count += 1
        if not self.values:
            raise StopAsyncIteration
        return self.values.pop(0)

    async def aclose(self) -> None:
        self.close_count += 1


@pytest.mark.asyncio
async def test_state_machine_rejects_invalid_and_duplicate_transitions() -> None:
    owner = RunOwnership(lambda _callback, _cleanup: asyncio.sleep(0, result=_Stream([])))
    assert owner.state is RunOwnershipState.CLAIMED
    owner.transition(RunOwnershipState.PREPARING)
    with pytest.raises(RunOwnershipTransitionError):
        owner.transition(RunOwnershipState.PREPARING)
    with pytest.raises(RunOwnershipTransitionError):
        owner.transition(RunOwnershipState.RELEASED)


@pytest.mark.asyncio
async def test_wait_open_timeout_does_not_cancel_owned_open_task() -> None:
    gate = asyncio.Event()
    stream = _Stream(["event"])

    async def open_factory(_callback: Any, _cleanup: Any) -> _Stream:
        await gate.wait()
        return stream

    owner = RunOwnership(open_factory).start()
    assert await owner.wait_open(timeout=0.001) is None
    assert owner.state is RunOwnershipState.PREPARING
    assert not owner._open_task.done()  # the owner, not the waiter, owns it
    gate.set()
    opened = await owner.wait_open()
    assert opened is stream
    assert owner.state is RunOwnershipState.PREPARED
    receipt = await owner.aclose()
    assert receipt.state is RunOwnershipState.RELEASED
    assert stream.close_count == 1


@pytest.mark.asyncio
async def test_close_before_open_settles_once_and_primes_stream() -> None:
    gate = asyncio.Event()
    stream = _Stream(["first"])
    durable = {"status": "cancelled"}

    async def open_factory(on_settlement: Any, _cleanup: Any) -> _Stream:
        await gate.wait()
        on_settlement(durable)
        return stream

    owner = RunOwnership(open_factory).start()
    close_task = asyncio.create_task(owner.aclose())
    await asyncio.sleep(0)
    assert not close_task.done()
    gate.set()
    receipt = await close_task
    assert receipt.state is RunOwnershipState.RELEASED
    assert receipt.durable is durable
    assert stream.next_count == 1
    assert stream.close_count == 1
    assert await owner.aclose() is receipt


@pytest.mark.asyncio
async def test_cleanup_error_is_quarantined_in_aggregate_receipt() -> None:
    class BrokenStream(_Stream):
        async def aclose(self) -> None:
            self.close_count += 1
            raise RuntimeError("worker cleanup failed")

    stream = BrokenStream([])
    owner = RunOwnership(lambda _callback, _cleanup: asyncio.sleep(0, result=stream)).start()
    await owner.wait_open()
    # Prime happens during close; the stream is already exhausted, then close fails.
    receipt = await owner.aclose()
    assert receipt.state is RunOwnershipState.QUARANTINED
    assert receipt.quarantine.status == "quarantined"
    assert receipt.cleanup_error is not None
    assert receipt.clean is False


def test_receipt_component_shape_is_internal_and_typed() -> None:
    receipt = RunLifetimeReceipt(
        state=RunOwnershipState.RELEASED,
        durable=object(),
        root_worker=OwnershipComponentReceipt("clean"),
        recursive_workers=OwnershipComponentReceipt("not_applicable"),
        provider=OwnershipComponentReceipt("clean"),
        post_commit=OwnershipComponentReceipt("clean"),
        quarantine=OwnershipComponentReceipt("not_quarantined"),
    )
    assert receipt.clean is True


@pytest.mark.asyncio
async def test_owner_waits_for_detached_cleanup_before_released() -> None:
    cleanup_gate = asyncio.Event()
    stream = _Stream([])

    async def cleanup() -> None:
        await cleanup_gate.wait()

    async def open_factory(_on_settlement: Any, on_cleanup: Any) -> _Stream:
        on_cleanup(asyncio.create_task(cleanup(), name="test-owned-cleanup"))
        return stream

    owner = RunOwnership(open_factory).start()
    await owner.wait_open()
    close_task = asyncio.create_task(owner.aclose())
    await asyncio.sleep(0)
    assert not close_task.done()
    cleanup_gate.set()
    receipt = await close_task
    assert receipt.state is RunOwnershipState.RELEASED
    assert receipt.recursive_workers.status == "clean"
