"""DSPy 3.4.0 Daytona interpreter seam certification tests."""

from __future__ import annotations

import asyncio
import inspect
import threading
from collections.abc import Callable
from typing import Any

import dspy
import pytest

from fleet_rlm.daytona.errors import DaytonaAdapterError
from fleet_rlm.daytona.interpreter import (
    DAYTONA_EXECUTION_INSTRUCTIONS,
    BackendExecutionResult,
    DaytonaCodeInterpreter,
    InProcessInterpreterBackend,
    OutputCallback,
)
from fleet_rlm.rlm.program import (
    RLMOptions,
)
from tests.support.native_rlm import build_native_rlm_for_test


def _rlm(*, tools: list[Callable[..., Any]] | None = None, signature: str = "request -> answer: str") -> Any:
    return build_native_rlm_for_test(
        signature=signature,
        options=RLMOptions(max_iters=3, max_llm_calls=3, max_output_chars=1_000),
        tools=tools,
        verbose=False,
    )


def _tracked_interpreter_factory(created: list[DaytonaCodeInterpreter]) -> Callable[[], DaytonaCodeInterpreter]:
    def create() -> DaytonaCodeInterpreter:
        interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
        created.append(interpreter)
        return interpreter

    create.__dict__["execution_instructions"] = DAYTONA_EXECUTION_INSTRUCTIONS
    return create


class _OneAction:
    def __init__(self, code: str) -> None:
        self.code = code
        self.calls = 0

    async def acall(self, **_kwargs: Any) -> dspy.Prediction:
        self.calls += 1
        return dspy.Prediction(reasoning="perform the certified action", code=self.code)


@pytest.mark.asyncio
async def test_explicit_interpreter_factory_owns_lifecycle_and_prompt_metadata() -> None:
    from tests.support.native_rlm import in_process_interpreter_factory

    rlm = _rlm()
    provider = rlm._interpreter_factory

    assert inspect.signature(provider).parameters == {}
    assert provider is in_process_interpreter_factory
    assert provider.execution_instructions == DAYTONA_EXECUTION_INSTRUCTIONS
    assert isinstance(provider(), DaytonaCodeInterpreter)


def test_daytona_action_prompt_contains_each_runtime_fact_once() -> None:
    prompt = str(_rlm().generate_action.signature.instructions)
    facts = (
        "Python runs in the Daytona Sandbox",
        "Variables persist across actions in this Turn",
        "Print only short findings",
        "Host Tools are callable Python functions",
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

    interpreters: list[DaytonaCodeInterpreter] = []
    interpreter_factory = _tracked_interpreter_factory(interpreters)
    first = _rlm(tools=[old_tool])
    second = _rlm(tools=[new_tool])
    first_action = _OneAction("SUBMIT(answer=old_tool())")

    first.generate_action = first_action

    first_prediction = await first.acall(interpreter_factory=interpreter_factory, request="first")
    assert first_prediction.answer == "old"

    class _RemovedThenFresh:
        calls = 0

        async def acall(self, **_kwargs: Any) -> dspy.Prediction:
            self.calls += 1
            code = "old_tool()" if self.calls == 1 else "SUBMIT(answer=new_tool())"
            return dspy.Prediction(reasoning="refresh bindings", code=code)

    second_action = _RemovedThenFresh()
    second.generate_action = second_action
    second_prediction = await second.acall(interpreter_factory=interpreter_factory, request="second")

    assert second_prediction.answer == "new"
    assert calls == ["old", "new"]
    assert "old_tool" not in interpreters[1].tools


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
    interpreter_factory = _tracked_interpreter_factory([])
    first = _rlm(tools=[first_tool])
    second = _rlm(tools=[second_tool])
    first.generate_action = _OneAction("SUBMIT(answer=same_name())")
    second.generate_action = _OneAction("SUBMIT(answer=same_name())")

    assert (await first.acall(interpreter_factory=interpreter_factory, request="first")).answer == "first"
    assert (await second.acall(interpreter_factory=interpreter_factory, request="second")).answer == "second"

    assert calls == ["first", "second"]


@pytest.mark.asyncio
async def test_sequential_output_metadata_rejects_old_submit_shape() -> None:
    interpreter_factory = _tracked_interpreter_factory([])
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
    assert (await first.acall(interpreter_factory=interpreter_factory, request="first")).answer == "first"
    prediction = await second.acall(interpreter_factory=interpreter_factory, request="second")

    assert prediction.result == 7
    assert second.generate_action.calls == 2


@pytest.mark.asyncio
async def test_overlapping_native_calls_receive_separate_factory_owned_interpreters() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    class _BlockingAction:
        async def acall(self, **_kwargs: Any) -> dspy.Prediction:
            entered.set()
            await release.wait()
            return dspy.Prediction(reasoning="submit", code="SUBMIT(answer='first')")

    first_interpreters: list[DaytonaCodeInterpreter] = []
    second_interpreters: list[DaytonaCodeInterpreter] = []
    first = _rlm()
    second = _rlm()
    first.generate_action = _BlockingAction()
    second.generate_action = _OneAction("SUBMIT(answer='second')")
    first_task = asyncio.create_task(
        first.acall(interpreter_factory=_tracked_interpreter_factory(first_interpreters), request="first")
    )
    await entered.wait()

    second_prediction = await second.acall(
        interpreter_factory=_tracked_interpreter_factory(second_interpreters), request="second"
    )
    assert second_prediction.answer == "second"

    release.set()
    assert (await first_task).answer == "first"
    assert first_interpreters[0] is not second_interpreters[0]


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
        ) -> BackendExecutionResult:
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
    with pytest.raises(DaytonaAdapterError, match="already executing"):
        interpreter.tools.update({"overlap": lambda: "wrong"})
    with pytest.raises(DaytonaAdapterError, match="already executing"):
        interpreter.cleanup_run_scratch()
    assert not interpreter.tools

    release.set()
    worker.join(timeout=2)
    assert first_result == ["first"]
    assert interpreter.execute("_out = 'after-settlement'") == "after-settlement"
    interpreter.shutdown()


@pytest.mark.parametrize("execution_fails", [False, True])
@pytest.mark.parametrize("cleanup_failure", [None, "backend", "broker"])
def test_shutdown_during_execution_closes_after_settlement(execution_fails: bool, cleanup_failure: str | None) -> None:
    entered = threading.Event()
    release = threading.Event()
    closes: list[str] = []
    stops: list[bool] = []

    class BlockingBackend(InProcessInterpreterBackend):
        def run(
            self,
            code: str,
            variables: dict[str, object] | None = None,
            *,
            on_stdout: OutputCallback | None = None,
        ) -> BackendExecutionResult:
            entered.set()
            assert release.wait(5)
            if execution_fails:
                raise DaytonaAdapterError(message="execution failed", cause_type="InterpreterLifecycleError")
            return super().run(code, variables, on_stdout=on_stdout)

        def close(self) -> None:
            closes.append("backend")
            if cleanup_failure == "backend" and len(closes) == 1:
                raise RuntimeError("backend cleanup failed")
            self._broker.stop(strict=True)
            super().close()

    class Broker:
        def stop(self, *, strict: bool) -> None:
            assert strict
            stops.append(strict)
            if cleanup_failure == "broker" and len(stops) == 1:
                raise RuntimeError("broker cleanup failed")

    backend = BlockingBackend()
    backend._broker = Broker()
    interpreter = DaytonaCodeInterpreter(backend=backend)
    outcomes: list[Any] = []

    def execute() -> None:
        try:
            outcomes.append(interpreter.execute("_out = 'settled'"))
        except Exception as exc:
            outcomes.append(exc)

    worker = threading.Thread(target=execute)
    worker.start()
    try:
        assert entered.wait(5)
        for strict in (False, True, False):
            with pytest.raises(DaytonaAdapterError, match="already executing") as exc:
                interpreter.shutdown(strict_broker_cleanup=strict)
            assert exc.value.cause_type == "InterpreterReuseError"
        assert not backend.closed
        assert not closes
        assert not stops
    finally:
        release.set()
        worker.join(timeout=5)

    assert not worker.is_alive()
    assert closes == ["backend"]
    if cleanup_failure is not None:
        assert isinstance(outcomes[0], RuntimeError)
        assert str(outcomes[0]) == f"{cleanup_failure} cleanup failed"
        assert not backend.closed
    elif execution_fails:
        assert isinstance(outcomes[0], DaytonaAdapterError)
        assert str(outcomes[0]) == "execution failed"
        assert backend.closed
    else:
        assert outcomes == ["settled"]
        assert backend.closed
    interpreter.shutdown()
    assert backend.closed
    assert len(closes) == (2 if cleanup_failure else 1)
    assert len(stops) == (2 if cleanup_failure == "broker" else 1)
    with pytest.raises(DaytonaAdapterError, match="shut down"):
        interpreter.execute("_out = 'must not run'")


@pytest.mark.asyncio
async def test_cancelled_to_thread_execution_keeps_shutdown_request() -> None:
    entered = threading.Event()
    release = threading.Event()
    closed = threading.Event()

    class BlockingBackend(InProcessInterpreterBackend):
        def run(
            self,
            code: str,
            variables: dict[str, object] | None = None,
            *,
            on_stdout: OutputCallback | None = None,
        ) -> BackendExecutionResult:
            entered.set()
            assert release.wait(5)
            return super().run(code, variables, on_stdout=on_stdout)

        def close(self) -> None:
            super().close()
            closed.set()

    backend = BlockingBackend()
    interpreter = DaytonaCodeInterpreter(backend=backend)
    task = asyncio.create_task(asyncio.to_thread(interpreter.execute, "_out = 'settled'"))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        with pytest.raises(DaytonaAdapterError, match="already executing"):
            interpreter.shutdown()
        assert not backend.closed
    finally:
        release.set()
    assert await asyncio.to_thread(closed.wait, 5)
    assert backend.closed
    interpreter.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_phase", ["injection", "serialize", "setup", "execution"])
async def test_native_dspy_finalizes_invocation_after_failure(failure_phase: str) -> None:
    from fleet_rlm.rlm.output_contract import FleetOutputContract, OutputField

    class FailingInput(dspy.SandboxSerializable):
        def to_sandbox(self) -> bytes:
            if failure_phase == "serialize":
                raise ValueError("serialization failed")
            return b"payload"

        def sandbox_setup(self) -> str:
            return "raise ValueError('setup failed')"

        def sandbox_assignment(self, var_name: str, data_expr: str) -> str:
            return f"{var_name} = {data_expr}"

        def rlm_preview(self, max_chars: int = 500) -> str:
            return "failing input"[:max_chars]

    class FailingBackend(InProcessInterpreterBackend):
        def run(
            self,
            code: str,
            variables: dict[str, object] | None = None,
            *,
            on_stdout: OutputCallback | None = None,
        ) -> BackendExecutionResult:
            if failure_phase == "execution":
                raise DaytonaAdapterError(message="execution failed", cause_type="InterpreterLifecycleError")
            return super().run(code, variables, on_stdout=on_stdout)

    backend = FailingBackend()
    invocation = DaytonaCodeInterpreter(backend=backend)
    if failure_phase == "injection":
        invocation.bind_output_contract(FleetOutputContract((OutputField("different", True),)))
    rlm = _rlm()
    rlm.generate_action = _OneAction("SUBMIT(answer='never')")
    request = "ordinary" if failure_phase == "execution" else FailingInput()
    with pytest.raises(Exception, match=r"failed|output fields do not match"):
        await rlm.acall(interpreter_factory=lambda: invocation, request=request)
    assert backend.closed
    invocation.shutdown()


@pytest.mark.asyncio
async def test_runner_rejects_native_build_without_invocation_factory() -> None:
    """P2.3: the serving path requires an invocation-scoped interpreter factory.

    With the default native program builder, an interpreter that cannot
    supply ``new_invocation`` fails closed before the native builder is called.
    """
    import asyncio as _asyncio
    from types import SimpleNamespace
    from uuid import uuid4 as _uuid4

    from fleet_rlm.rlm.execution import (
        ExecutionRuntime,
        RLMExecutionContext,
        RLMRunner,
        RunIdentity,
        SessionView,
    )
    from fleet_rlm.rlm.program import RLMOptions
    from fleet_rlm.sessions.context import SessionContextManifest
    from fleet_rlm.sessions.models import TurnAccess
    from tests.rlm.fakes import EmptyCapabilities

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


@pytest.mark.asyncio
@pytest.mark.parametrize("later_failure", [False, True])
async def test_root_records_attachment_accesses_from_the_created_invocation(later_failure: bool, tmp_path: Any) -> None:
    """Accesses come from the factory-created invocation, never the retained template."""
    import hashlib
    from types import SimpleNamespace
    from uuid import uuid4

    from fleet_rlm.rlm.execution import ExecutionRuntime, RLMExecutionContext, RLMRunner, RunIdentity, SessionView
    from fleet_rlm.rlm.program import AttachmentContextCapsule, AttachmentContextEntry
    from fleet_rlm.sessions.context import SessionContextManifest
    from fleet_rlm.sessions.models import TurnAccess
    from tests.rlm.fakes import EmptyCapabilities

    body = b"Fleet context"
    (tmp_path / "note.txt").write_bytes(body)
    attachment_id = uuid4()
    capsule = AttachmentContextCapsule(
        (
            AttachmentContextEntry(
                attachment_id,
                "note.txt",
                "text/plain",
                len(body),
                hashlib.sha256(body).hexdigest(),
                str(tmp_path / "note.txt"),
            ),
        ),
        mount_root=str(tmp_path),
    )
    created: list[DaytonaCodeInterpreter] = []

    class Program:
        def __init__(self, interpreter_factory: Callable[[], DaytonaCodeInterpreter]) -> None:
            self._factory = interpreter_factory

        async def acall(self, **_kwargs: Any) -> dspy.Prediction:
            for _ in range(2):
                invocation = self._factory()
                created.append(invocation)
                invocation.execute(
                    capsule.sandbox_assignment("attachments", "_raw_attachments"),
                    {"_raw_attachments": capsule.to_sandbox().decode("utf-8")},
                )
                invocation.shutdown()
            if later_failure:
                raise RuntimeError("later execution failed")
            return dspy.Prediction(answer="done")

    def build_program(**kwargs: Any) -> Program:
        return Program(kwargs["interpreter_factory"])

    class Capabilities(EmptyCapabilities):
        def __init__(self) -> None:
            super().__init__()
            self.recorded: list[tuple[str, ...]] = []

        def record_attachment_accesses(self, accesses: tuple[str, ...]) -> None:
            self.recorded.append(accesses)

    async def not_cancelled() -> bool:
        return False

    template = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
    capabilities = Capabilities()
    context = RLMExecutionContext(
        identity=RunIdentity(run_id=uuid4(), session_id=uuid4(), access=TurnAccess(uuid4(), uuid4())),
        session=SessionView(
            request="answer",
            session_context=SessionContextManifest(uuid4(), 0, 0, ()),
            attachments=(),
            attachment_context=capsule,
        ),
        execution=ExecutionRuntime(
            models=SimpleNamespace(root_lm=dspy.utils.DummyLM([]), sub_lm=dspy.utils.DummyLM([])),
            options=RLMOptions(),
            deadline=asyncio.get_running_loop().time() + 10,
            interpreter=template,
            cancellation_requested=not_cancelled,
        ),
        capabilities=capabilities,
    )

    _ = [event async for event in RLMRunner(program_builder=build_program).stream(context)]

    assert len(created) == 2 and template not in created
    assert capabilities.recorded == [(str(attachment_id), str(attachment_id))]
    assert template.drain_context_accesses() == ()
    assert all(invocation.drain_context_accesses() == () for invocation in created)


def test_wrap_and_is_final_output_round_trip() -> None:
    from dspy import FinalOutput

    from fleet_rlm.daytona.interpreter import is_final_output, wrap_final_output

    wrapped = wrap_final_output({"answer": "ok"})
    assert isinstance(wrapped, FinalOutput)
    assert wrapped.output == {"answer": "ok"}
    assert is_final_output(wrapped)
    assert not is_final_output("stdout")


def test_interpreter_types_use_dspy_public_namespace() -> None:
    import dspy
    from dspy import CodeInterpreter, FinalOutput

    assert CodeInterpreter is dspy.CodeInterpreter
    assert FinalOutput is dspy.FinalOutput


def test_copy_output_fields_defensive_copy() -> None:
    from fleet_rlm.daytona.interpreter import copy_output_fields

    fields = [{"name": "answer", "type": "str"}]
    copied = copy_output_fields(fields)
    assert copied == fields
    assert copied is not fields
    assert copy_output_fields(None) is None


def test_copy_output_fields_does_not_share_nested_metadata() -> None:
    from fleet_rlm.daytona.interpreter import copy_output_fields

    fields = [{"name": "answer", "metadata": {"description": "final answer"}}]
    copied = copy_output_fields(fields)

    assert copied is not None
    copied[0]["metadata"]["description"] = "changed"
    assert fields[0]["metadata"]["description"] == "final answer"


def test_output_metadata_cannot_mutate_invocation_through_aliases() -> None:
    from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, InProcessInterpreterBackend

    fields = [{"name": "answer", "type": "str", "metadata": {"description": "original"}}]
    interp = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend(), output_fields=fields)
    fields[0]["name"] = "changed"
    interp.execute("value = 41")
    snapshot = interp.output_fields
    assert snapshot is not None
    snapshot[0]["metadata"]["description"] = "changed"
    snapshot.append({"name": "extra"})
    assert interp.output_fields == [{"name": "answer", "type": "str", "metadata": {"description": "original"}}]
    assert interp.execute("SUBMIT(answer=str(value + 1))").output == {"answer": "42"}
    interp.shutdown()


def test_public_final_output_label_is_stable() -> None:
    from fleet_rlm.daytona.interpreter import PUBLIC_FINAL_OUTPUT_LABEL

    assert PUBLIC_FINAL_OUTPUT_LABEL == "FINAL submitted"
