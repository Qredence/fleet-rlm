"""DSPy 3.3.1 Daytona interpreter seam certification tests."""

from __future__ import annotations

import asyncio
import inspect
import threading
from collections.abc import Callable
from typing import Any

import dspy
import pytest

from fleet_rlm.daytona.errors import DaytonaAdapterError
from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, InProcessInterpreterBackend
from fleet_rlm.rlm.program import (
    RLMOptions,
    build_native_rlm,
)


def _rlm(*, tools: list[Callable[..., Any]] | None = None, signature: str = "request -> answer: str") -> Any:
    return build_native_rlm(
        signature=signature,
        options=RLMOptions(max_iters=3, max_llm_calls=3, max_output_chars=1_000),
        tools=tools,
        verbose=False,
    )


class _OneAction:
    def __init__(self, code: str) -> None:
        self.code = code
        self.calls = 0

    async def acall(self, **_kwargs: Any) -> dspy.Prediction:
        self.calls += 1
        return dspy.Prediction(reasoning="perform the certified action", code=self.code)


@pytest.mark.asyncio
async def test_daytona_provider_contract_is_zero_arg_metadata_only() -> None:
    from fleet_rlm.rlm.compat_3_3_1 import DAYTONA_EXECUTION_INSTRUCTIONS

    rlm = _rlm()
    provider = rlm._interpreter_factory

    assert inspect.signature(provider).parameters == {}
    assert provider.execution_instructions == DAYTONA_EXECUTION_INSTRUCTIONS
    assert provider.execution_instructions == DAYTONA_EXECUTION_INSTRUCTIONS

    with pytest.raises(Exception, match="caller-owned interpreter"):
        provider()


def test_daytona_action_prompt_contains_each_runtime_fact_once() -> None:
    from fleet_rlm.rlm.compat_3_3_1 import DAYTONA_EXECUTION_INSTRUCTIONS

    prompt = str(_rlm().generate_action.signature.instructions)
    facts = (
        "isolated Python",
        "namespace persists across actions in one invocation",
        "Host Tools are callable Python functions",
        "ordinary stdout is observable",
        "typed keyword `SUBMIT`",
    )

    assert prompt.count(DAYTONA_EXECUTION_INSTRUCTIONS) == 1
    lowered_prompt = prompt.lower()
    for fact in facts:
        assert lowered_prompt.count(fact.lower()) == 1, fact
    forbidden = ("pyodide", "deno", "javascript repl", "browser runtime", "package installation")
    assert all(word not in prompt.lower() for word in forbidden)


@pytest.mark.asyncio
async def test_sequential_reinjection_removes_old_tool_and_keeps_new_tool() -> None:
    calls: list[str] = []

    def old_tool() -> str:
        calls.append("old")
        return "old"

    def new_tool() -> str:
        calls.append("new")
        return "new"

    interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
    first = _rlm(tools=[old_tool])
    second = _rlm(tools=[new_tool])
    first_action = _OneAction("SUBMIT(answer=old_tool())")

    first.generate_action = first_action

    try:
        first_prediction = await first.acall(interpreter, request="first")
        assert first_prediction.answer == "old"

        class _RemovedThenFresh:
            calls = 0

            async def acall(self, **_kwargs: Any) -> dspy.Prediction:
                self.calls += 1
                code = "old_tool()" if self.calls == 1 else "SUBMIT(answer=new_tool())"
                return dspy.Prediction(reasoning="refresh bindings", code=code)

        second_action = _RemovedThenFresh()
        second.generate_action = second_action
        second_prediction = await second.acall(interpreter, request="second")
    finally:
        interpreter.shutdown()

    assert second_prediction.answer == "new"
    assert calls == ["old", "new"]
    assert "old_tool" not in interpreter.tools


@pytest.mark.asyncio
async def test_sequential_same_name_tool_closure_uses_only_new_binding() -> None:
    calls: list[str] = []

    def first_tool() -> str:
        calls.append("first")
        return "first"

    def second_tool() -> str:
        calls.append("second")
        return "second"

    first_tool.__name__ = "same_name"
    second_tool.__name__ = "same_name"
    interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
    first = _rlm(tools=[first_tool])
    second = _rlm(tools=[second_tool])
    first.generate_action = _OneAction("SUBMIT(answer=same_name())")
    second.generate_action = _OneAction("SUBMIT(answer=same_name())")

    try:
        assert (await first.acall(interpreter, request="first")).answer == "first"
        assert (await second.acall(interpreter, request="second")).answer == "second"
    finally:
        interpreter.shutdown()

    assert calls == ["first", "second"]


@pytest.mark.asyncio
async def test_sequential_output_metadata_rejects_old_submit_shape() -> None:
    interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
    first = _rlm(signature="request -> answer: str")
    second = _rlm(signature="request -> result: int")
    first.generate_action = _OneAction("SUBMIT(answer='first')")

    class _OldThenNew:
        calls = 0

        async def acall(self, **_kwargs: Any) -> dspy.Prediction:
            self.calls += 1
            code = "SUBMIT(answer='stale')" if self.calls == 1 else "SUBMIT(result=7)"
            return dspy.Prediction(reasoning="refresh output metadata", code=code)

    second.generate_action = _OldThenNew()
    try:
        assert (await first.acall(interpreter, request="first")).answer == "first"
        prediction = await second.acall(interpreter, request="second")
    finally:
        interpreter.shutdown()

    assert prediction.result == 7
    assert second.generate_action.calls == 2


@pytest.mark.asyncio
async def test_overlapping_native_acall_is_rejected_before_second_action_generation() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    class _BlockingAction:
        async def acall(self, **_kwargs: Any) -> dspy.Prediction:
            entered.set()
            await release.wait()
            return dspy.Prediction(reasoning="submit", code="SUBMIT(answer='first')")

    interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
    first = _rlm()
    second = _rlm()
    first.generate_action = _BlockingAction()
    second.generate_action = _OneAction("SUBMIT(answer='second')")
    first_task = asyncio.create_task(first.acall(interpreter, request="first"))
    await entered.wait()

    with pytest.raises(DaytonaAdapterError, match="already executing"):
        await second.acall(interpreter, request="second")

    release.set()
    assert (await first_task).answer == "first"
    interpreter.shutdown()


def test_overlapping_interpreter_reuse_is_rejected_until_settlement() -> None:
    entered = threading.Event()
    release = threading.Event()

    class BlockingBackend(InProcessInterpreterBackend):
        def run(
            self,
            code: str,
            variables: dict[str, object] | None = None,
            *,
            on_stdout: Callable[[str], None] | None = None,
        ) -> Any:
            entered.set()
            assert release.wait(2)
            return super().run(code, variables, on_stdout=on_stdout)

    interpreter = DaytonaCodeInterpreter(backend=BlockingBackend())
    first_result: list[Any] = []

    def run_first() -> None:
        first_result.append(interpreter.execute("_out = 'first'"))

    worker = threading.Thread(target=run_first)
    worker.start()
    assert entered.wait(2)

    with pytest.raises(DaytonaAdapterError, match="already executing"):
        interpreter.execute("_out = 'overlap'")

    release.set()
    worker.join(timeout=2)
    assert first_result == ["first"]
    assert interpreter.execute("_out = 'after-settlement'") == "after-settlement"
    interpreter.shutdown()


@pytest.mark.asyncio
async def test_runner_rejects_native_build_without_invocation_factory() -> None:
    """P2.3: the serving path never falls back to the rejecting contract.

    With the default native program builder, an interpreter that cannot
    supply ``new_invocation`` fails closed instead of receiving the
    metadata-only ``daytona_provider_contract`` factory.
    """
    import asyncio as _asyncio
    from types import SimpleNamespace
    from uuid import uuid4 as _uuid4

    from fleet_rlm.rlm.program import RLMOptions
    from fleet_rlm.rlm.runtime import (
        ExecutionRuntime,
        RLMExecutionContext,
        RLMRunner,
        RunIdentity,
        SessionView,
    )
    from fleet_rlm.sessions.context import SessionContextManifest
    from fleet_rlm.sessions.models import TurnAccess
    from tests.unit.backend.rlm.fakes import EmptyCapabilities

    class LegacyInterpreter:
        fleet_host_tool_dispatch_available = False

        def bind_observer(self, _observer, *, max_chars):
            del max_chars
            return None

    async def not_cancelled() -> bool:
        return False

    context = RLMExecutionContext(
        identity=RunIdentity(run_id=_uuid4(), session_id=_uuid4(), access=TurnAccess(_uuid4(), _uuid4())),
        session=SessionView(
            request="answer",
            session_context=SessionContextManifest(_uuid4(), 0, 0, ()),
            attachments=(),
            preparation_notices=(),
        ),
        execution=ExecutionRuntime(
            models=SimpleNamespace(root_lm=object(), sub_lm=object()),
            options=RLMOptions(),
            deadline=_asyncio.get_running_loop().time() + 10,
            interpreter=LegacyInterpreter(),
            cancellation_requested=not_cancelled,
        ),
        capabilities=EmptyCapabilities(),
    )
    stream = RLMRunner().stream(context)
    _ = [event async for event in stream]

    assert stream.outcome is not None
    assert not stream.outcome.succeeded
