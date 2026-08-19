"""Private worker execution contracts."""

from __future__ import annotations

import threading
from collections.abc import Mapping
from contextvars import ContextVar
from types import SimpleNamespace
from typing import cast

import pytest

from fleet_rlm.rlm.context import RLMExecutionContext
from fleet_rlm.rlm.worker_execution import WorkerOwnership, start_rlm_worker


@pytest.mark.asyncio
async def test_worker_handle_propagates_context_and_hides_thread_details() -> None:
    execution_marker: ContextVar[str | None] = ContextVar("execution_marker", default=None)
    execution_marker.set("turn-context")
    main_thread = threading.get_ident()
    ownership = WorkerOwnership()
    context = cast(RLMExecutionContext, SimpleNamespace())
    observations: list[tuple[object, object, Mapping[str, object]]] = []

    async def execute(rlm: object, received_context: RLMExecutionContext, kwargs: Mapping[str, object]) -> str:
        observations.append((rlm, received_context, kwargs))
        assert execution_marker.get() == "turn-context"
        assert threading.get_ident() != main_thread
        return f"answer:{kwargs['value']}"

    worker = start_rlm_worker(
        rlm=object(),
        context=context,
        kwargs={"value": "sample"},
        ownership=ownership,
        execute=execute,
    )

    await worker.wait_until_done()

    assert worker.result() == "answer:sample"
    assert len(observations) == 1
    assert observations[0][1] is context
    await ownership.wait_owned()
