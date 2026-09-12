"""Run-scoped DSPy program contracts for retained broker execution."""

from __future__ import annotations

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
from fleet_rlm.sessions.models import TurnAccess
from tests.unit.backend.rlm.fakes import EmptyCapabilities


class _Interpreter:
    def __init__(self) -> None:
        self.namespace: dict[str, object] = {}


class _Program:
    def __init__(self, programs: list[_Program], histories: list[dspy.History]) -> None:
        self._programs = programs
        self._histories = histories

    async def acall(self, **kwargs: object) -> dspy.Prediction:
        history = kwargs.get("history")
        assert type(history) is dspy.History
        self._histories.append(history)
        # A fresh program must not rely on Python state installed by a prior Run.
        assert "prior_run_marker" not in self.__dict__
        self.prior_run_marker = "run-local"
        return dspy.Prediction(answer=f"answer-{len(self._programs)}", trajectory=[])


class _Factory:
    def __init__(self) -> None:
        self.programs: list[_Program] = []
        self.histories: list[dspy.History] = []

    def create(self, **_kwargs: object) -> _Program:
        program = _Program(self.programs, self.histories)
        self.programs.append(program)
        return program


async def _not_cancelled() -> bool:
    return False


def _context(
    *, session_id, workspace_id, run_id, interpreter, request: str, history: dspy.History
) -> RLMExecutionContext:
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
            cancellation_requested=_not_cancelled,
            deadline=10**12,
        ),
        capabilities=cast(Any, EmptyCapabilities()),
    )


@pytest.mark.asyncio
async def test_sequential_runs_use_fresh_programs_and_committed_history() -> None:
    session_id, workspace_id = uuid4(), uuid4()
    interpreter = _Interpreter()
    factory = _Factory()
    runner = RLMRunner(factory=factory)
    first_history = dspy.History(messages=[{"request": "prior", "answer": "stored"}])
    second_history = dspy.History(
        messages=[
            {"request": "prior", "answer": "stored"},
            {"request": "first", "answer": "answer-1"},
        ]
    )

    for request, history in (("first", first_history), ("second", second_history)):
        stream = runner.stream(
            _context(
                session_id=session_id,
                workspace_id=workspace_id,
                run_id=uuid4(),
                interpreter=interpreter,
                request=request,
                history=history,
            )
        )
        _ = [event async for event in stream]
        assert stream.outcome is not None and stream.outcome.succeeded
        stream.mark_committed()
        await stream.aclose()

    assert len(factory.programs) == 2
    assert factory.histories == [first_history, second_history]
    await runner.aclose()


@pytest.mark.parametrize("variant", ["native-turn-scoped", "native", "unknown"])
def test_execution_context_rejects_unselected_runtime(variant: str) -> None:
    from dataclasses import replace

    context = _context(
        session_id=uuid4(),
        workspace_id=uuid4(),
        run_id=uuid4(),
        interpreter=_Interpreter(),
        request="request",
        history=dspy.History(messages=[]),
    )
    with pytest.raises(ValueError, match="only retained broker execution"):
        replace(context.execution, runtime_variant=variant)
