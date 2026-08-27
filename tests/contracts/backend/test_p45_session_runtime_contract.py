"""P45 native Runner reuse contract with a persistent in-process interpreter."""

from __future__ import annotations

import time
from uuid import uuid4

import dspy
import pytest

from fleet_rlm.chat.session_context import SessionContextManifest
from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, InProcessInterpreterBackend
from fleet_rlm.rlm.context import ExecutionRuntime, RLMExecutionContext, RLMExecutionSpec, RunIdentity, SessionView
from fleet_rlm.rlm.dspy_contract import RLMOptions
from fleet_rlm.rlm.model_bundle import RLMModelBundle
from fleet_rlm.rlm.runner import RLMRunner
from fleet_rlm.rlm.session_runtime import SessionKey
from fleet_rlm.rlm.signature import FleetRLMSignature
from fleet_rlm.sessions.models import TurnAccess
from tests.unit.backend.rlm.fakes import EmptyCapabilities


def _models() -> RLMModelBundle:
    adapter = dspy.JSONAdapter()
    root = dspy.utils.DummyLM(
        [
            {"reasoning": "establish a persistent marker", "code": "p45_marker = 'retained'\nSUBMIT(answer='first')"},
            {
                "reasoning": "reuse the persistent marker",
                "code": "assert p45_marker == 'retained'\nSUBMIT(answer='second')",
            },
        ],
        adapter=adapter,
    )
    sub = dspy.utils.DummyLM([{"answer": "unused"}], adapter=adapter)
    return RLMModelBundle(root, sub)


def _context(
    *, session_id, workspace_id, interpreter, models: RLMModelBundle, request: str, history: dspy.History
) -> RLMExecutionContext:
    return RLMExecutionContext(
        identity=RunIdentity(
            run_id=uuid4(),
            session_id=session_id,
            access=TurnAccess(uuid4(), workspace_id),
        ),
        session=SessionView(
            request=request,
            session_context=SessionContextManifest(session_id, 0, 0, ()),
            attachments=(),
            history=history,
        ),
        execution=ExecutionRuntime(
            models=models,
            options=RLMOptions(max_iters=1, max_llm_calls=1, max_output_chars=1024),
            interpreter=interpreter,
            cancellation_requested=_never_cancelled,
            deadline=time.monotonic() + 30,
        ),
        capabilities=EmptyCapabilities(spec=RLMExecutionSpec(signature=FleetRLMSignature)),
    )


async def _never_cancelled() -> bool:
    return False


@pytest.mark.asyncio
async def test_native_runner_reuses_rlm_interpreter_and_persistent_namespace() -> None:
    session_id, workspace_id = uuid4(), uuid4()
    interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
    runner = RLMRunner()
    models = _models()
    key = SessionKey(str(workspace_id), str(session_id))
    try:
        first_history = dspy.History(messages=[])
        first = runner.stream(
            _context(
                session_id=session_id,
                workspace_id=workspace_id,
                interpreter=interpreter,
                models=models,
                request="first",
                history=first_history,
            )
        )
        _ = [event async for event in first]
        assert first.outcome is not None and first.outcome.succeeded
        first.mark_committed()
        await first.aclose()
        first_state = runner._runtime_registry.get(key)
        assert first_state is not None

        second_history = dspy.History(messages=[{"request": "first", "answer": "first"}])
        second = runner.stream(
            _context(
                session_id=session_id,
                workspace_id=workspace_id,
                interpreter=interpreter,
                models=models,
                request="second",
                history=second_history,
            )
        )
        _ = [event async for event in second]
        assert second.outcome is not None and second.outcome.succeeded
        second.mark_committed()
        await second.aclose()

        second_state = runner._runtime_registry.get(key)
        assert second_state is first_state
        assert second_state.rlm is first_state.rlm
        assert second_state.interpreter is interpreter
    finally:
        await runner._runtime_registry.shutdown()
