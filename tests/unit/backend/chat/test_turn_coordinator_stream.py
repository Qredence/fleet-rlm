"""Coordinator-owned stream lifetime contracts."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from uuid import uuid4

import pytest

from fleet_rlm.chat.turn_coordinator import OpenedTurnStream


@dataclass
class _Stream:
    values: list[str]
    outcome: object | None = None
    run_id: object | None = None
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
async def test_wait_open_timeout_does_not_cancel_coordinator_open_task() -> None:
    gate = asyncio.Event()
    stream = _Stream(["event"])

    async def open_stream() -> _Stream:
        await gate.wait()
        return stream

    owner = OpenedTurnStream(None, open_task=asyncio.create_task(open_stream()))
    assert await owner.wait_open(timeout=0.001) is None
    assert owner._open_task is not None
    assert not owner._open_task.done()
    gate.set()
    assert await owner.wait_open() is owner
    await owner.aclose()
    assert stream.next_count == 1
    assert stream.close_count == 1


@pytest.mark.asyncio
async def test_close_before_first_iteration_primes_and_closes_async_generator() -> None:
    closed = asyncio.Event()

    async def events():
        try:
            yield "event"
        finally:
            closed.set()

    owner = OpenedTurnStream(None, events())
    await owner.aclose()
    await owner.aclose()
    assert closed.is_set()


@pytest.mark.asyncio
async def test_close_failure_is_replayed_by_idempotent_close_task() -> None:
    class Broken(_Stream):
        async def aclose(self) -> None:
            self.close_count += 1
            raise RuntimeError("worker cleanup failed")

    owner = OpenedTurnStream(None, Broken([]))
    with pytest.raises(RuntimeError, match="worker cleanup failed"):
        await owner.aclose()
    with pytest.raises(RuntimeError, match="worker cleanup failed"):
        await owner.aclose()


@pytest.mark.asyncio
async def test_close_continues_after_first_iteration_failure() -> None:
    class BrokenFirst(_Stream):
        async def __anext__(self) -> str:
            self.next_count += 1
            raise RuntimeError("first iteration failed")

    stream = BrokenFirst([])
    owner = OpenedTurnStream(None, stream)
    with pytest.raises(RuntimeError, match="first iteration failed"):
        await owner.aclose()
    assert stream.close_count == 1


@pytest.mark.asyncio
async def test_nested_opened_stream_transfers_iteration_and_cleanup_ownership() -> None:
    stream = _Stream(["event"])
    marker = object()
    stream.outcome = marker
    inner = OpenedTurnStream(None, stream)

    async def open_stream() -> OpenedTurnStream:
        return inner

    outer = OpenedTurnStream(None, open_task=asyncio.create_task(open_stream()))
    assert await outer.wait_open() is outer
    assert outer._opened_owner is inner
    assert outer.outcome is marker

    assert await outer.__anext__() == "event"
    await outer.aclose()
    await outer.aclose()

    assert stream.next_count == 1
    assert stream.close_count == 1


@pytest.mark.asyncio
async def test_nested_opened_stream_wait_open_includes_inner_pending_open() -> None:
    gate = asyncio.Event()
    inner_run_id = uuid4()
    stream = _Stream(["event"], run_id=inner_run_id)

    async def open_inner() -> _Stream:
        await gate.wait()
        return stream

    inner = OpenedTurnStream(inner_run_id, open_task=asyncio.create_task(open_inner()))

    async def open_outer() -> OpenedTurnStream:
        return inner

    outer = OpenedTurnStream(None, open_task=asyncio.create_task(open_outer()))
    assert await outer.wait_open(timeout=0.001) is None
    assert inner._open_task is not None
    assert not inner._open_task.done()

    gate.set()
    assert await outer.wait_open() is outer
    assert outer.run_id == inner_run_id
    assert await outer.__anext__() == "event"
    await outer.aclose()
    assert stream.close_count == 1
