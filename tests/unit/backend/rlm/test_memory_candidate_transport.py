"""Runner transport for Run-scoped Memory Candidates."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import dspy
import pytest


def _context(*, drain_calls: list[int], returned_candidates=(), cancelled: bool = False):
    from fleet_rlm.chat.session_context import SessionContextManifest
    from fleet_rlm.rlm.program import RLMOptions
    from fleet_rlm.rlm.runtime import (
        ExecutionRuntime,
        RLMExecutionContext,
        RunIdentity,
        SessionView,
    )
    from fleet_rlm.sessions.models import TurnAccess
    from tests.unit.backend.rlm.fakes import EmptyCapabilities

    class Capabilities(EmptyCapabilities):
        def drain_memory_candidates(self):
            drain_calls.append(1)
            return returned_candidates

    async def cancellation_probe() -> bool:
        return cancelled

    return RLMExecutionContext(
        identity=RunIdentity(run_id=uuid4(), session_id=uuid4(), access=TurnAccess(uuid4(), uuid4())),
        session=SessionView(
            request="promote later",
            session_context=SessionContextManifest(uuid4(), 0, 0, ()),
            attachments=(),
            preparation_notices=(),
        ),
        execution=ExecutionRuntime(
            models=cast("Any", SimpleNamespace(root_lm=object(), sub_lm=object())),
            options=RLMOptions(),
            deadline=asyncio.get_running_loop().time() + 10,
            interpreter=None,
            cancellation_requested=cancellation_probe,
        ),
        capabilities=cast("Any", Capabilities()),
    )


@pytest.mark.asyncio
async def test_runner_attaches_drained_memory_candidates_only_to_completed_outcome() -> None:
    from fleet_rlm.files.memory_candidates import MemoryCandidate
    from fleet_rlm.rlm.runtime import RLMRunner

    candidate = MemoryCandidate(candidate_id="cand00000001", category="Project", learning="durable", byte_size=7)

    class Factory:
        def create(self, **_kwargs):
            class Program:
                async def acall(self, **_call_kwargs):
                    return dspy.Prediction(answer="done", trajectory=[])

            return Program()

    drains: list[int] = []
    context = _context(drain_calls=drains, returned_candidates=(candidate,))
    stream = RLMRunner(factory=Factory()).stream(context)

    _ = [event async for event in stream]

    assert stream.outcome is not None and stream.outcome.terminal_status == "completed"
    assert stream.outcome.memory_candidates == (candidate,)
    assert drains == [1]


@pytest.mark.asyncio
async def test_runner_discards_memory_candidates_on_execution_failure() -> None:
    from fleet_rlm.rlm.runtime import RLMRunner

    class Factory:
        def create(self, **_kwargs):
            raise RuntimeError("provider unavailable")

    drains: list[int] = []
    context = _context(drain_calls=drains)
    stream = RLMRunner(factory=Factory()).stream(context)

    _ = [event async for event in stream]

    assert stream.outcome is not None and stream.outcome.terminal_status == "failed"
    assert stream.outcome.memory_candidates == ()
    assert drains == [1]


def test_non_completed_outcome_rejects_memory_candidates() -> None:
    from fleet_rlm.files.memory_candidates import MemoryCandidate
    from fleet_rlm.rlm.result import RLMOutcome

    candidate = MemoryCandidate(candidate_id="cand00000001", category="Project", learning="durable", byte_size=7)

    for terminal in ("cancelled", "timeout", "failed"):
        with pytest.raises(ValueError, match="Memory Candidates"):
            RLMOutcome(
                terminal,  # type: ignore[arg-type]
                public_error_message="Turn failed",
                memory_candidates=(candidate,),
            )


@pytest.mark.asyncio
async def test_runner_discards_memory_candidates_when_execution_is_cancelled() -> None:
    from fleet_rlm.files.memory_candidates import MemoryCandidate
    from fleet_rlm.rlm.runtime import RLMRunner

    candidate = MemoryCandidate(candidate_id="cand00000001", category="Project", learning="durable", byte_size=7)
    drains: list[int] = []
    context = _context(drain_calls=drains, returned_candidates=(candidate,), cancelled=True)
    stream = RLMRunner(factory=SimpleNamespace(create=lambda **_kwargs: object())).stream(context)

    _ = [event async for event in stream]

    assert stream.outcome is not None and stream.outcome.terminal_status == "cancelled"
    assert stream.outcome.memory_candidates == ()
    assert drains == [1]
