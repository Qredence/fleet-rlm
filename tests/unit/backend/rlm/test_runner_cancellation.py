"""RLM runner worker ownership and cancellation."""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from uuid import uuid4

import dspy
import pytest


@pytest.mark.asyncio
async def test_runner_returns_promptly_and_retains_blocking_worker_for_cleanup() -> None:
    from fleet_rlm.chat.session_context import SessionContextManifest
    from fleet_rlm.rlm.context import (
        ExecutionRuntime,
        RLMExecutionContext,
        RLMExecutionSpec,
        RunIdentity,
        SessionView,
    )
    from fleet_rlm.rlm.dspy_contract import RLMOptions
    from fleet_rlm.rlm.runner import RLMRunner
    from fleet_rlm.sessions.models import TurnAccess

    entered = threading.Event()
    release = threading.Event()
    cancel_requested = False

    class Capabilities:
        spec = RLMExecutionSpec()

        def drain_public_details(self):
            return ()

        def drain_artifact_candidates(self):
            return ()

    class Factory:
        def create(self, **_kwargs):
            class Program:
                async def acall(self, **_call_kwargs):
                    entered.set()
                    while not release.is_set():
                        await asyncio.sleep(0.01)
                    return dspy.Prediction(answer="late", trajectory=[])

            return Program()

    async def cancellation_probe() -> bool:
        return cancel_requested

    context = RLMExecutionContext(
        identity=RunIdentity(run_id=uuid4(), session_id=uuid4(), access=TurnAccess(uuid4(), uuid4())),
        session=SessionView(
            request="answer",
            session_context=SessionContextManifest(uuid4(), 0, 0, ()),
            attachments=(),
            preparation_notices=(),
        ),
        execution=ExecutionRuntime(
            models=SimpleNamespace(root_lm=object(), sub_lm=object()),
            options=RLMOptions(),
            deadline=asyncio.get_running_loop().time() + 10,
            interpreter=None,
            cancellation_requested=cancellation_probe,
        ),
        capabilities=Capabilities(),
    )
    stream = RLMRunner(factory=Factory()).stream(context)

    async def consume_all() -> None:
        async for _event in stream:
            pass

    consume = asyncio.create_task(consume_all())
    assert await asyncio.to_thread(entered.wait, 2)

    cancel_requested = True
    await asyncio.sleep(0.3)
    assert consume.done(), "caller delivery must not wait for the non-cancellable worker"
    assert stream.outcome is not None
    assert stream.outcome.terminal_status == "cancelled"

    release.set()
    await asyncio.wait_for(stream.wait_owned(), timeout=2)


@pytest.mark.asyncio
async def test_runner_transfers_blocking_worker_after_caller_cancellation() -> None:
    from fleet_rlm.chat.session_context import SessionContextManifest
    from fleet_rlm.rlm.context import (
        ExecutionRuntime,
        RLMExecutionContext,
        RLMExecutionSpec,
        RunIdentity,
        SessionView,
    )
    from fleet_rlm.rlm.dspy_contract import RLMOptions
    from fleet_rlm.rlm.runner import RLMRunner
    from fleet_rlm.sessions.models import TurnAccess

    entered = threading.Event()
    release = threading.Event()

    class Capabilities:
        spec = RLMExecutionSpec()

        def drain_public_details(self):
            return ()

        def drain_artifact_candidates(self):
            return ()

    class Factory:
        def create(self, **_kwargs):
            class Program:
                async def acall(self, **_call_kwargs):
                    entered.set()
                    while not release.is_set():
                        await asyncio.sleep(0.01)
                    return dspy.Prediction(answer="late", trajectory=[])

            return Program()

    async def not_cancelled() -> bool:
        return False

    context = RLMExecutionContext(
        identity=RunIdentity(run_id=uuid4(), session_id=uuid4(), access=TurnAccess(uuid4(), uuid4())),
        session=SessionView(
            request="answer",
            session_context=SessionContextManifest(uuid4(), 0, 0, ()),
            attachments=(),
            preparation_notices=(),
        ),
        execution=ExecutionRuntime(
            models=SimpleNamespace(root_lm=object(), sub_lm=object()),
            options=RLMOptions(),
            deadline=asyncio.get_running_loop().time() + 10,
            interpreter=None,
            cancellation_requested=not_cancelled,
        ),
        capabilities=Capabilities(),
    )
    stream = RLMRunner(factory=Factory()).stream(context)

    async def consume_all() -> None:
        async for _event in stream:
            pass

    consume = asyncio.create_task(consume_all())
    assert await asyncio.to_thread(entered.wait, 2)
    consume.cancel()
    await asyncio.sleep(0.05)
    consume.cancel()
    await asyncio.sleep(0.05)
    with pytest.raises(asyncio.CancelledError):
        await consume

    release.set()
    await asyncio.wait_for(stream.wait_owned(), timeout=2)
