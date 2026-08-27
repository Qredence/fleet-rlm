"""P45 Runner integration contracts for resident Session state."""

from __future__ import annotations

import threading
from typing import Any, cast
from uuid import uuid4

import dspy
import pytest

from fleet_rlm.chat.session_context import SessionContextManifest
from fleet_rlm.rlm.program import RLMModelBundle, RLMOptions
from fleet_rlm.rlm.runtime import (
    ExecutionRuntime,
    RLMExecutionContext,
    RLMRunner,
    RunIdentity,
    SessionView,
)
from fleet_rlm.rlm.session_runtime import SessionRLMRegistry
from fleet_rlm.sessions.models import TurnAccess
from tests.unit.backend.rlm.fakes import EmptyCapabilities


class _Interpreter:
    def __init__(self) -> None:
        self.tools: dict[str, object] = {}
        self.output_fields: dict[str, object] = {}
        self.namespace: dict[str, object] = {}
        self.close_calls = 0


class _Program:
    def __init__(self, thread_ids: list[int], interpreter: _Interpreter) -> None:
        self.thread_ids = thread_ids
        self.interpreter = interpreter
        self.calls = 0
        self.histories: list[dspy.History] = []

    async def acall(self, **kwargs: object) -> dspy.Prediction:
        self.calls += 1
        history = kwargs.get("history")
        assert type(history) is dspy.History
        self.histories.append(history)
        if self.calls == 1:
            self.interpreter.namespace["persisted_marker"] = "clean-turn"
        else:
            assert self.interpreter.namespace["persisted_marker"] == "clean-turn"
        self.thread_ids.append(threading.get_ident())
        return dspy.Prediction(answer=f"answer-{self.calls}", trajectory=[])


class _Factory:
    def __init__(self, thread_ids: list[int], interpreter: _Interpreter) -> None:
        self.thread_ids = thread_ids
        self.interpreter = interpreter
        self.programs: list[_Program] = []

    def create(self, **_kwargs: object) -> _Program:
        program = _Program(self.thread_ids, self.interpreter)
        self.programs.append(program)
        return program


def _context(session_id, workspace_id, interpreter, request: str, run_id, history: dspy.History) -> RLMExecutionContext:
    return RLMExecutionContext(
        identity=RunIdentity(run_id=run_id, session_id=session_id, access=TurnAccess(uuid4(), workspace_id)),
        session=SessionView(
            request=request,
            session_context=SessionContextManifest(session_id, 0, 0, ()),
            attachments=(),
            history=history,
        ),
        execution=ExecutionRuntime(
            models=RLMModelBundle(object(), object()),
            options=RLMOptions(),
            interpreter=interpreter,
            cancellation_requested=lambda: _not_cancelled(),
            deadline=10**12,
        ),
        capabilities=cast(Any, EmptyCapabilities()),
    )


async def _not_cancelled() -> bool:
    return False


@pytest.mark.asyncio
async def test_runner_preserves_an_explicit_empty_runtime_registry() -> None:
    registry = SessionRLMRegistry()
    runner = RLMRunner(runtime_registry=registry)
    assert runner._runtime_registry is registry
    await registry.shutdown()


@pytest.mark.asyncio
async def test_successful_sequential_streams_reuse_program_interpreter_and_thread() -> None:
    session_id, workspace_id = uuid4(), uuid4()
    interpreter = _Interpreter()
    thread_ids: list[int] = []
    factory = _Factory(thread_ids, interpreter)
    runner = RLMRunner(factory=factory)
    first_history = dspy.History(messages=[{"request": "prior", "answer": "stored"}])
    second_history = dspy.History(
        messages=[
            {"request": "prior", "answer": "stored"},
            {"request": "first", "answer": "answer-1"},
        ]
    )

    first = runner.stream(_context(session_id, workspace_id, interpreter, "first", uuid4(), first_history))
    _ = [event async for event in first]
    assert first.outcome is not None and first.outcome.succeeded
    first.mark_committed()
    await first.aclose()

    second = runner.stream(_context(session_id, workspace_id, interpreter, "second", uuid4(), second_history))
    _ = [event async for event in second]
    assert second.outcome is not None and second.outcome.succeeded
    second.mark_committed()
    await second.aclose()

    assert len(factory.programs) == 1
    assert factory.programs[0].calls == 2
    assert factory.programs[0].histories == [first_history, second_history]
    assert factory.programs[0].histories[0] is first_history
    assert factory.programs[0].histories[1] is second_history
    assert len(thread_ids) == 2
    assert thread_ids[0] == thread_ids[1]
    session_key = next(iter(runner._session_tool_registries))
    assert runner._session_tool_registries[session_key].active_run_id is None


@pytest.mark.asyncio
async def test_uncommitted_stream_taints_before_next_session_turn() -> None:
    session_id, workspace_id = uuid4(), uuid4()
    interpreter = _Interpreter()
    factory = _Factory([], interpreter)
    runner = RLMRunner(factory=factory)

    first = runner.stream(_context(session_id, workspace_id, interpreter, "first", uuid4(), dspy.History(messages=[])))
    _ = [event async for event in first]
    await first.aclose()

    second = runner.stream(
        _context(session_id, workspace_id, interpreter, "second", uuid4(), dspy.History(messages=[]))
    )
    _ = [event async for event in second]
    second.mark_committed()
    await second.aclose()

    assert len(factory.programs) == 2
