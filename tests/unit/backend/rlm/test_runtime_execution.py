"""RLM runtime execution, run-scoped reuse, and execution-context contracts.

* ``test_runtime_execution.py``: Behavior contracts for runtime execution.
* ``test_session_runtime_reuse.py``: Run-scoped DSPy program contracts for retained broker execution.
* ``test_execution_context.py``: Ready-to-run immutable RLM context contract.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock
from uuid import uuid4

import dspy
import pytest

from fleet_rlm.rlm.execution import (
    ExecutionRuntime,
    RLMExecutionContext,
    RLMRunner,
    RunIdentity,
    RunToolGuards,
    SessionView,
    WorkerOwnership,
    start_rlm_worker,
)
from fleet_rlm.rlm.ownership import OwnedEffect
from fleet_rlm.rlm.program import RLMModelBundle, RLMOptions
from fleet_rlm.sessions.context import SessionContextManifest
from fleet_rlm.sessions.models import TurnAccess
from tests.support.role_lm import placeholder_bundle
from tests.unit.backend.rlm.fakes import EmptyCapabilities


# --- from test_runtime_execution.py -----------------------------------
@pytest.mark.asyncio
async def test_detail_relay_keeps_1024_ordinary_events_and_lifecycle_details() -> None:
    from fleet_rlm.rlm.events import (
        MAX_DETAIL_EVENTS,
        DetailRelay,
        RLMOutput,
        SkillLoaded,
    )

    relay = DetailRelay()
    for index in range(MAX_DETAIL_EVENTS):
        relay.publish(RLMOutput(f"detail-{index}", index))
    relay.publish(SkillLoaded("skill-1", "benchmark", "1.0.0"))
    relay.publish(RLMOutput("dropped", MAX_DETAIL_EVENTS + 1))

    details = relay.drain()

    assert sum(isinstance(detail, RLMOutput) for detail in details) == MAX_DETAIL_EVENTS
    assert any(isinstance(detail, SkillLoaded) for detail in details)
    assert relay.overflowed is True


@pytest.mark.asyncio
async def test_detail_relay_retains_step_lifecycle_when_ordinary_queue_is_full() -> None:
    from fleet_rlm.rlm.events import ChildProgress, DetailRelay, RLMOutput, StepFinished, StepStarted

    relay = DetailRelay(maxsize=1)
    relay.publish(RLMOutput("queued", 1))
    relay.publish(StepStarted(1))
    relay.publish(ChildProgress("child-1", "Inspect references", "running", 1, cleanup_state="pending"))
    relay.publish(RLMOutput("dropped", 1))
    assert await relay.get() == RLMOutput("queued", 1)
    relay.publish(StepFinished(1))

    assert relay.drain() == [
        StepStarted(1),
        ChildProgress("child-1", "Inspect references", "running", 1, cleanup_state="pending"),
        StepFinished(1),
    ]
    assert relay.overflowed is True


@pytest.mark.asyncio
async def test_runner_uses_native_path_for_plain_greeting() -> None:
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
    from tests.unit.backend.rlm.fakes import EmptyCapabilities

    class Program:
        async def acall(self, **_kwargs):
            return dspy.Prediction(
                answer="Hi! How can I help you today?",
                trajectory=[
                    {
                        "reasoning": "Answer the greeting.",
                        "code": "SUBMIT(answer='Hi! How can I help you today?')",
                        "output": "FINAL: {'answer': 'Hi! How can I help you today?'}",
                    }
                ],
            )

    class Factory:
        created = False
        host_tool_dispatch = None

        def create(self, **kwargs):
            self.created = True
            self.host_tool_dispatch = kwargs["host_tool_dispatch"]
            return Program()

    async def not_cancelled() -> bool:
        return False

    context = RLMExecutionContext(
        identity=RunIdentity(run_id=uuid4(), session_id=uuid4(), access=TurnAccess(uuid4(), uuid4())),
        session=SessionView(
            request="  Hi!  ",
            session_context=SessionContextManifest(uuid4(), 0, 0, ()),
            attachments=(),
            preparation_notices=(),
        ),
        execution=ExecutionRuntime(
            models=SimpleNamespace(root_lm=object(), sub_lm=object()),
            options=RLMOptions(),
            deadline=asyncio.get_running_loop().time() + 10,
            interpreter=SimpleNamespace(),
            cancellation_requested=not_cancelled,
        ),
        capabilities=EmptyCapabilities(),
    )

    factory = Factory()
    stream = RLMRunner(program_builder=factory.create).stream(context)
    events = [event async for event in stream]

    assert [event.kind for event in events] == [
        "run.started",
        "status",
        "step.started",
        "rlm.reasoning",
        "rlm.code",
        "rlm.output",
        "step.finished",
    ]
    assert factory.created
    assert factory.host_tool_dispatch is False
    assert stream.outcome is not None and stream.outcome.succeeded
    assert stream.outcome.prediction is not None
    assert stream.outcome.prediction.answer == "Hi! How can I help you today?"
    assert stream.outcome.usage["iterations"] == 1


@pytest.mark.asyncio
async def test_runner_uses_supported_async_call_and_returns_typed_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify supported asynchronous execution produces a typed successful outcome.

    The outcome includes events, usage metrics, configured tools, and execution tracing.
    """
    from fleet_rlm.rlm.events import RLMCode, RLMOutput, StepFinished, StepStarted
    from fleet_rlm.rlm.execution import (
        ExecutionRuntime,
        RLMExecutionContext,
        RLMExecutionSpec,
        RLMRunner,
        RunIdentity,
        SessionView,
    )
    from fleet_rlm.rlm.program import RLMOptions
    from fleet_rlm.sessions.context import SessionContextManifest
    from fleet_rlm.sessions.models import TurnAccess
    from fleet_rlm.skills.models import SkillCard
    from fleet_rlm.workspace.models import WorkspaceCapabilityMetadata
    from tests.unit.backend.rlm.fakes import EmptyCapabilities

    class Factory:
        options = None
        tools = None

        def create(self, **kwargs):
            assert "observer" not in kwargs
            self.options = kwargs["options"]
            self.tools = kwargs["tools"]
            factory = self

            class Program:
                async def acall(self, **call_kwargs):
                    assert call_kwargs["request"] == "answer"
                    assert call_kwargs["skill_cards"] == [
                        {
                            "id": str(skill_id),
                            "name": "long-context",
                            "description": "Analyze long inputs",
                            "scope": "system",
                            "version": "2.0.0",
                            "trust": "system",
                            "affordances": [],
                            "resources_available": True,
                        }
                    ]
                    assert call_kwargs["session_context"]["workspace"] == {
                        "available": True,
                        "root": ".",
                        "instructions": "Use durable workspace tools.",
                    }
                    assert threading.get_ident() != main_thread
                    interpreter.observer(StepStarted(1))
                    interpreter.observer(RLMCode("answer = helper(value='sample')", 1))
                    assert factory.tools[0](value="sample") == "done:sample"
                    interpreter.observer(RLMOutput("FINAL submitted", 1))
                    interpreter.observer(StepFinished(1, 1))
                    prediction = dspy.Prediction(
                        answer="42",
                        trajectory=[
                            {
                                "reasoning": "Use the registered helper.",
                                "code": "answer = helper(value='sample')",
                                "output": "FINAL: {'answer': '42'}",
                            }
                        ],
                    )
                    prediction.set_lm_usage({"root": {"prompt_tokens": 4, "completion_tokens": 2}})
                    return prediction

            return Program()

    class Interpreter:
        observer = None
        fleet_host_tool_dispatch_available = True

        def bind_observer(self, observer, *, max_chars):
            assert max_chars == RLMOptions().max_output_chars
            self.observer = observer

    def helper(value: str) -> str:
        return f"done:{value}"

    async def not_cancelled():
        return False

    factory = Factory()
    interpreter = Interpreter()
    capabilities = EmptyCapabilities(
        spec=RLMExecutionSpec(
            workspace=WorkspaceCapabilityMetadata(True, ".", "Use durable workspace tools."),
        )
    )
    skill_id = uuid4()
    main_thread = threading.get_ident()
    contexts: list[dict[str, object]] = []
    phase_spans: list[tuple[str, dict[str, object]]] = []
    original_context = dspy.context
    global_adapter = dspy.settings.adapter

    def tracked_context(**kwargs):
        contexts.append(kwargs)
        return original_context(**kwargs)

    @contextmanager
    def tracked_phase_span(name: str, *, inputs: dict[str, object]) -> Iterator[SimpleNamespace]:
        """
        Record a phase span and provide an object for recording its outputs.

        Parameters:
            name (str): Name of the phase span.
            inputs (dict[str, object]): Inputs associated with the phase span.

        Yields:
            SimpleNamespace: Object with a no-op `set_outputs` method.
        """
        phase_spans.append((name, inputs))
        yield SimpleNamespace(set_outputs=lambda _outputs: None)

    adapter_contexts = []

    def stock_adapter(adapter_context):
        adapter_contexts.append(adapter_context)
        return dspy.JSONAdapter()

    monkeypatch.setattr(dspy, "context", tracked_context)
    monkeypatch.setattr("fleet_rlm.rlm.events.turn_phase_span", tracked_phase_span)
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
            interpreter=interpreter,
            cancellation_requested=not_cancelled,
        ),
        capabilities=capabilities,
    )
    stream = RLMRunner(program_builder=factory.create, _adapter_factory=stock_adapter).stream(context)
    capabilities.spec = RLMExecutionSpec(
        skill_cards=(
            SkillCard(
                skill_id,
                "long-context",
                "Analyze long inputs",
                "2.0.0",
                True,
            ),
        ),
        tools=(dspy.Tool(helper),),
        workspace=WorkspaceCapabilityMetadata(True, ".", "Use durable workspace tools."),
    )
    events = [event async for event in stream]

    assert [event.kind for event in events] == [
        "run.started",
        "status",
        "step.started",
        "tool.started",
        "tool.completed",
        "rlm.code",
        "rlm.output",
        "step.finished",
        "rlm.reasoning",
    ]
    assert stream.outcome is not None
    assert stream.outcome.prediction is not None
    assert stream.outcome.prediction.answer == "42"
    assert stream.outcome.succeeded
    assert factory.options is context.execution.options
    assert isinstance(factory.tools[0], dspy.Tool)
    assert stream.outcome.usage["iterations"] == 1
    assert stream.outcome.usage["observed_lm_usage"] == {"root": {"prompt_tokens": 4, "completion_tokens": 2}}
    assert set(stream.outcome.usage) == {
        "iterations",
        "observed_lm_usage",
        "duration_ms",
        "recursive_call_count",
        "delegation_metrics",
    }
    assert len(contexts) == 1
    assert contexts[0]["lm"] is context.execution.models.root_lm
    assert contexts[0]["track_usage"] is True
    adapter = contexts[0]["adapter"]
    assert adapter_contexts == [context]
    assert type(adapter) is dspy.JSONAdapter
    assert adapter.use_native_function_calling is True
    assert dspy.settings.adapter is global_adapter
    assert phase_spans == [
        (
            "RLM.execute",
            {
                "max_iters": context.execution.options.max_iters,
                "max_llm_calls": context.execution.options.max_llm_calls,
                "max_output_chars": context.execution.options.max_output_chars,
            },
        )
    ]


def test_runner_uses_stock_json_adapter_without_protocol_salvage() -> None:
    adapter = dspy.JSONAdapter()

    assert type(adapter) is dspy.JSONAdapter
    assert adapter.use_native_function_calling is True


@pytest.mark.asyncio
async def test_runner_passes_prepared_attachment_context_to_rlm() -> None:
    from fleet_rlm.rlm.execution import (
        ExecutionRuntime,
        RLMExecutionContext,
        RLMRunner,
        RunIdentity,
        SessionView,
    )
    from fleet_rlm.rlm.program import (
        AttachmentContextCapsule,
        AttachmentContextEntry,
        RLMOptions,
    )
    from fleet_rlm.sessions.context import SessionContextManifest
    from fleet_rlm.sessions.models import TurnAccess
    from tests.unit.backend.rlm.fakes import EmptyCapabilities

    class Program:
        async def acall(self, **call_kwargs):
            assert call_kwargs["attachments"] is attachment_context
            return dspy.Prediction(answer="ok", trajectory=[])

    class Factory:
        def create(self, **_kwargs):
            return Program()

    async def not_cancelled() -> bool:
        return False

    attachment_context = AttachmentContextCapsule(
        (
            AttachmentContextEntry(
                attachment_id=uuid4(),
                filename="notes.txt",
                content_type="text/plain",
                byte_size=3,
                checksum_sha256="a" * 64,
                sandbox_path="/home/daytona/run/notes.txt",
            ),
        ),
        mount_root="/home/daytona/run",
    )
    context = RLMExecutionContext(
        identity=RunIdentity(run_id=uuid4(), session_id=uuid4(), access=TurnAccess(uuid4(), uuid4())),
        session=SessionView(
            request="use context",
            session_context=SessionContextManifest(uuid4(), 0, 0, ()),
            attachments=(),
            preparation_notices=(),
            attachment_context=attachment_context,
        ),
        execution=ExecutionRuntime(
            models=SimpleNamespace(root_lm=object(), sub_lm=object()),
            options=RLMOptions(),
            deadline=asyncio.get_running_loop().time() + 10,
            interpreter=None,
            cancellation_requested=not_cancelled,
        ),
        capabilities=EmptyCapabilities(),
    )

    stream = RLMRunner(program_builder=Factory().create).stream(context)
    _events = [event async for event in stream]

    assert stream.outcome is not None and stream.outcome.succeeded


@pytest.mark.asyncio
async def test_runner_validates_host_metadata_before_provider_execution() -> None:
    from fleet_rlm.rlm.execution import (
        ExecutionRuntime,
        RLMExecutionContext,
        RLMRunner,
        RunIdentity,
        SessionView,
    )
    from fleet_rlm.rlm.program import RLMOptions
    from fleet_rlm.sessions.context import SessionContextManifest, TurnPreview
    from fleet_rlm.sessions.models import TurnAccess
    from tests.unit.backend.rlm.fakes import EmptyCapabilities

    class Program:
        acall_calls = 0

        async def acall(self, **_kwargs):
            self.acall_calls += 1
            return dspy.Prediction(answer="must not execute")

    class Factory:
        def __init__(self) -> None:
            self.program = Program()

        def create(self, **_kwargs):
            return self.program

    async def not_cancelled() -> bool:
        return False

    malformed_context = SessionContextManifest(
        "not-a-uuid",  # type: ignore[arg-type]
        -1,
        0,
        (TurnPreview(0, "system", "malformed"),),  # type: ignore[arg-type]
    )
    context = RLMExecutionContext(
        identity=RunIdentity(run_id=uuid4(), session_id=uuid4(), access=TurnAccess(uuid4(), uuid4())),
        session=SessionView(
            request="validate me", session_context=malformed_context, attachments=(), preparation_notices=()
        ),
        execution=ExecutionRuntime(
            models=SimpleNamespace(root_lm=object(), sub_lm=object()),
            options=RLMOptions(),
            deadline=asyncio.get_running_loop().time() + 10,
            interpreter=None,
            cancellation_requested=not_cancelled,
        ),
        capabilities=EmptyCapabilities(
            spec=SimpleNamespace(tools=(), tool_event_views={}, skill_cards=(), signature=None, workspace=None)
        ),
    )
    factory = Factory()
    stream = RLMRunner(program_builder=factory.create).stream(context)
    _events = [event async for event in stream]

    assert factory.program.acall_calls == 0
    assert stream.outcome is not None
    assert stream.outcome.terminal_status == "failed"
    assert stream.outcome.public_error_message == "Turn failed"


@pytest.mark.asyncio
async def test_runner_loads_two_skills_reads_python_resource_and_completes_submit() -> None:
    from fleet_rlm.rlm.execution import (
        ExecutionRuntime,
        RLMExecutionContext,
        RLMExecutionSpec,
        RLMRunner,
        RunIdentity,
        SessionView,
    )
    from fleet_rlm.rlm.program import RLMOptions
    from fleet_rlm.sessions.context import SessionContextManifest
    from fleet_rlm.sessions.models import TurnAccess
    from fleet_rlm.skills.catalog import SkillCatalog
    from fleet_rlm.skills.models import SkillCard, SkillDefinition, SkillResource
    from fleet_rlm.skills.tools import SkillToolHost
    from fleet_rlm.turns.preparation import PreparedHostCapabilities

    user_id, workspace_id = uuid4(), uuid4()
    first = SkillDefinition(
        SkillCard(uuid4(), "first-skill", "First progressive Skill.", "1.0.0", True),
        "Load the helper script.",
        {
            "scripts/helper.py": SkillResource(
                "scripts/helper.py", "text/x-python", "def produce_answer():\n    return 'progressive completion'\n"
            )
        },
    )
    second = SkillDefinition(
        SkillCard(uuid4(), "second-skill", "Second progressive Skill.", "1.0.0", False),
        "Confirm the answer.",
    )
    catalog = SkillCatalog((first, second))
    skill_host = SkillToolHost(catalog)
    spec = RLMExecutionSpec(
        skill_cards=catalog.cards(),
        tools=skill_host.as_tools(),
        tool_event_views=skill_host.event_views(),
    )

    class Files:
        def drain_public_events(self):
            return []

    capabilities = PreparedHostCapabilities(
        spec,
        files=Files(),
        skills=skill_host,
        close_files=False,
        artifact_candidates=False,
    )

    class Factory:
        def create(self, **kwargs):
            tools = {str(tool.name): tool for tool in kwargs["tools"]}

            class Program:
                async def acall(self, **call_kwargs):
                    assert len(call_kwargs["skill_cards"]) == 2
                    assert tools["load_skill"](skill_id=str(first.card.id))["ok"] is True
                    assert tools["load_skill"](skill_id=str(second.card.id))["ok"] is True
                    resource = tools["read_skill_resource"](
                        skill_id=str(first.card.id),
                        resource_path="scripts/helper.py",
                    )
                    namespace: dict[str, object] = {}
                    exec(str(resource["content"]), namespace)
                    answer = namespace["produce_answer"]()
                    return dspy.Prediction(
                        answer=answer,
                        trajectory=[{"code": "SUBMIT(answer=answer)", "output": "FINAL submitted"}],
                    )

            return Program()

    async def not_cancelled() -> bool:
        return False

    context = RLMExecutionContext(
        identity=RunIdentity(run_id=uuid4(), session_id=uuid4(), access=TurnAccess(user_id, workspace_id)),
        session=SessionView(
            request="complete progressively",
            session_context=SessionContextManifest(uuid4(), 0, 0, ()),
            attachments=(),
            preparation_notices=(),
        ),
        execution=ExecutionRuntime(
            models=SimpleNamespace(root_lm=object(), sub_lm=object()),
            options=RLMOptions(),
            deadline=asyncio.get_running_loop().time() + 10,
            interpreter=SimpleNamespace(fleet_host_tool_dispatch_available=True),
            cancellation_requested=not_cancelled,
        ),
        capabilities=capabilities,
    )
    stream = RLMRunner(program_builder=Factory().create).stream(context)
    events = [event async for event in stream]
    kinds = [event.kind for event in events]

    first_started = kinds.index("tool.started")
    assert kinds[first_started : first_started + 4] == [
        "tool.started",
        "skill.activated",
        "skill.loaded",
        "tool.completed",
    ]
    second_started = kinds.index("tool.started", first_started + 1)
    assert kinds[second_started : second_started + 4] == [
        "tool.started",
        "skill.activated",
        "skill.loaded",
        "tool.completed",
    ]
    assert kinds.count("skill.activated") == 2
    assert kinds.count("skill.loaded") == 2
    assert stream.outcome is not None and stream.outcome.succeeded
    assert stream.outcome.prediction is not None
    assert stream.outcome.prediction.answer == "progressive completion"


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


@pytest.mark.asyncio
@pytest.mark.parametrize("fails", [False, True])
async def test_worker_ownership_preserves_drain_result_for_all_callers(fails: bool) -> None:
    ownership = WorkerOwnership()
    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    release = threading.Event()
    calls: list[str] = []
    first_error = RuntimeError("first waiter failed")

    def first_waiter() -> None:
        calls.append("first")
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(timeout=5)
        if fails:
            raise first_error

    def second_waiter() -> None:
        calls.append("second")
        if fails:
            raise ValueError("second waiter failed")

    async def wait_and_capture() -> Exception | None:
        try:
            await ownership.wait_owned()
        except Exception as exc:
            return exc
        return None

    ownership.add_blocking_waiter(first_waiter)
    ownership.add_blocking_waiter(second_waiter)
    ownership.add_completion_callback(lambda: calls.append("completed"))
    initial = asyncio.create_task(wait_and_capture())
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        concurrent = asyncio.create_task(wait_and_capture())
        await asyncio.sleep(0)
        assert not concurrent.done()
    finally:
        release.set()

    results = await asyncio.gather(initial, concurrent)
    results.extend([await wait_and_capture(), await wait_and_capture()])
    assert all(result is (first_error if fails else None) for result in results)
    assert calls == ["first", "second", "completed"]
    ownership.add_completion_callback(lambda: calls.append("late callback"))
    assert calls == ["first", "second", "completed", "late callback"]


# --- from test_session_runtime_reuse.py -------------------------------
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
            models=placeholder_bundle(),
            options=RLMOptions(),
            interpreter=interpreter,
            cancellation_requested=_not_cancelled,
            deadline=10**12,
        ),
        capabilities=cast(Any, EmptyCapabilities()),
    )


@pytest.mark.asyncio
async def test_runner_ignores_adapter_summary_failure_after_success() -> None:
    class SummaryFailureError(RuntimeError):
        pass

    class Adapter:
        def wrap_up_summary(self) -> dict[str, object]:
            return {}

        def repair_summary(self) -> dict[str, object]:
            raise SummaryFailureError("optional repair diagnostics failed")

    session_id, workspace_id = uuid4(), uuid4()
    runner = RLMRunner(program_builder=_Factory().create, _adapter_factory=lambda _context: Adapter())
    stream = runner.stream(
        _context(
            session_id=session_id,
            workspace_id=workspace_id,
            run_id=uuid4(),
            interpreter=_Interpreter(),
            request="answer",
            history=dspy.History(messages=[]),
        )
    )
    _ = [event async for event in stream]

    assert stream.outcome is not None and stream.outcome.succeeded


@pytest.mark.asyncio
async def test_runner_preserves_execution_error_when_adapter_summaries_fail(caplog) -> None:
    class OriginalFailureError(RuntimeError):
        pass

    class SummaryFailureError(RuntimeError):
        pass

    original = OriginalFailureError("RLM invocation failed")

    class Adapter:
        def wrap_up_summary(self) -> dict[str, object]:
            raise SummaryFailureError("wrap-up summary failed")

        def repair_summary(self) -> dict[str, object]:
            raise SummaryFailureError("repair summary failed")

    class Program:
        async def acall(self, **_kwargs: object) -> dspy.Prediction:
            raise original

    class Factory:
        def create(self, **_kwargs: object) -> Program:
            return Program()

    runner = RLMRunner(program_builder=Factory().create, _adapter_factory=lambda _context: Adapter())
    stream = runner.stream(
        _context(
            session_id=uuid4(),
            workspace_id=uuid4(),
            run_id=uuid4(),
            interpreter=_Interpreter(),
            request="answer",
            history=dspy.History(messages=[]),
        )
    )
    _ = [event async for event in stream]

    failures = [record for record in caplog.records if record.exc_info is not None]
    assert stream.outcome is not None and stream.outcome.terminal_status == "failed"
    assert any(record.exc_info[1] is original for record in failures)


@pytest.mark.asyncio
async def test_sequential_runs_use_fresh_programs_and_committed_history() -> None:
    session_id, workspace_id = uuid4(), uuid4()
    interpreter = _Interpreter()
    factory = _Factory()
    runner = RLMRunner(program_builder=factory.create)
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


# --- from test_execution_context.py -----------------------------------
def test_execution_context_is_immutable_and_contains_prepared_runner_inputs() -> None:
    from fleet_rlm.rlm.execution import (
        ExecutionRuntime,
        RLMExecutionContext,
        RunIdentity,
        SessionView,
    )
    from fleet_rlm.rlm.program import RLMOptions
    from fleet_rlm.sessions.context import SessionContextManifest, TurnPreview
    from fleet_rlm.sessions.models import TurnAccess

    session_id = uuid4()
    context = RLMExecutionContext(
        identity=RunIdentity(
            run_id=uuid4(), session_id=session_id, access=TurnAccess(user_id=uuid4(), workspace_id=uuid4())
        ),
        session=SessionView(
            request="inspect",
            session_context=SessionContextManifest(
                session_id,
                3,
                1,
                (TurnPreview(1, "user", "prior"),),
            ),
            attachments=(),
            preparation_notices=(),
        ),
        execution=ExecutionRuntime(
            models=RLMModelBundle(root_lm=MagicMock(), sub_lm=MagicMock()),
            options=RLMOptions(),
            deadline=123.0,
            interpreter=SimpleNamespace(execute=lambda code: code),
            cancellation_requested=lambda: False,
        ),
        capabilities=SimpleNamespace(),
    )

    assert context.identity.session_id == session_id
    assert context.session.request == "inspect"
    assert context.execution.deadline == 123.0
    assert context.session.attachments == ()
    assert not hasattr(context, "settings")
    assert not hasattr(context, "turn_store")
    with pytest.raises(FrozenInstanceError):
        context.session.request = "changed"  # type: ignore[misc]


def test_child_progress_is_a_bounded_snapshot() -> None:
    from fleet_rlm.rlm.events import ChildProgress

    detail = ChildProgress("run:call-2", "Search official docs", "completed", 1200, "Found two sources", "complete")
    assert detail.kind == "child.progress"
    assert detail.child_id == "run:call-2"
    with pytest.raises(ValueError, match="elapsed_ms"):
        ChildProgress("c1", "Task", "failed", -1)
    with pytest.raises(ValueError, match="task_label"):
        ChildProgress("c1", " ", "failed", 1)


def test_event_recorder_wraps_typed_details_in_an_immutable_ordered_envelope() -> None:
    from fleet_rlm.rlm.events import EventRecorder, RunStarted, TextDelta

    run_id = uuid4()
    session_id = uuid4()
    recorder = EventRecorder(run_id=run_id, session_id=session_id)

    first = recorder.record(RunStarted(delivery="live"))
    second = recorder.record(TextDelta(text="hello"))

    assert first.kind == "run.started"
    assert first.sequence == 1
    assert second.sequence == 2
    assert second.detail == TextDelta(text="hello")
    assert first.run_id == second.run_id == run_id
    assert first.session_id == second.session_id == session_id
    with pytest.raises(FrozenInstanceError):
        second.sequence = 3  # type: ignore[misc]


def test_event_recorder_rejects_second_or_post_terminal_details() -> None:
    from fleet_rlm.rlm.events import EventRecorder, EventSequenceError, RunCompleted, TextDelta

    recorder = EventRecorder(run_id=uuid4(), session_id=uuid4())
    recorder.record(RunCompleted(checkpoint_version=3, delivery="live"))

    with pytest.raises(EventSequenceError):
        recorder.record(TextDelta(text="late"))


@pytest.mark.parametrize(
    "prompt",
    ["Explain README.md and https://example.com/report."],
)
def test_read_only_prose_never_seeds_workspace_mutation_obligations(prompt: str) -> None:
    del prompt
    assert RunToolGuards().integrity.unresolved == ()


def test_failed_reads_do_not_create_integrity_obligations() -> None:
    guards = RunToolGuards()

    guards.failed("read_workspace_text", {"path": "README.md"})
    guards.failed("read_project_text", {"path": "fleet-rlm/review.md"})

    assert guards.integrity.unresolved == ()


def test_mutations_remain_scoped_to_their_actual_target() -> None:
    guards = RunToolGuards()

    guards.failed("write_workspace_text", {"path": "notes/report.md", "content": "draft"})
    guards.completed("write_workspace_text", {"path": "notes/other.md", "content": "done"}, {"ok": True})

    assert guards.integrity.unresolved == ("session_workspace:notes/report.md",)


def test_successful_append_edit_delete_and_publish_settle_mutations() -> None:
    guards = RunToolGuards()
    mutations = (
        ("append_workspace_text", {"path": "notes/a.md", "content": "x"}),
        ("edit_workspace_text", {"path": "notes/b.md", "old": "x", "new": "y"}),
        ("delete_workspace_path", {"path": "notes/c.md"}),
        ("publish_workspace_artifact", {"path": "notes/d.md", "kind": "markdown"}),
        ("edit_project_text", {"path": "fleet-rlm/e.md", "old": "x", "new": "y"}),
        ("delete_project_path", {"path": "fleet-rlm/f.md"}),
    )

    for tool_name, arguments in mutations:
        guards.failed(tool_name, arguments)
        guards.completed(tool_name, arguments, {"ok": True})

    assert guards.integrity.unresolved == ()


def test_identical_tool_results_warn_once_without_terminally_failing_a_turn() -> None:
    guards = RunToolGuards()
    arguments = {"offset": 22, "limit": 5}
    eof = {"next_offset": None, "done": True, "messages": []}

    assert guards.completed("read_session_history", arguments, eof) is None
    assert guards.completed("read_session_history", arguments, eof) == "repeated tool call produced no progress"
    assert guards.completed("read_session_history", arguments, eof) is None


@pytest.mark.asyncio
async def test_owned_effect_preserves_success_and_repeated_settlement() -> None:
    effect = OwnedEffect.start(asyncio.sleep(0, result="ok"))

    first = await effect.settle()
    second = await effect.settle()

    assert first.done is True
    assert first.pending is False
    assert first.timed_out is False
    assert first.result() == "ok"
    assert second.result() == "ok"


@pytest.mark.asyncio
async def test_owned_effect_preserves_failure_on_repeated_settlement() -> None:
    async def fail() -> str:
        raise ValueError("owned effect failed")

    effect = OwnedEffect.start(fail())

    with pytest.raises(ValueError, match="owned effect failed"):
        await effect.settle()
    with pytest.raises(ValueError, match="owned effect failed"):
        await effect.settle()


@pytest.mark.asyncio
async def test_waiter_cancellation_does_not_cancel_owned_effect() -> None:
    release = asyncio.Event()

    async def work() -> str:
        await release.wait()
        return "settled"

    effect = OwnedEffect.start(work())
    waiter = asyncio.create_task(effect.settle())
    await asyncio.sleep(0)
    waiter.cancel()
    await asyncio.sleep(0)

    assert waiter.done() is False
    assert effect.done() is False

    release.set()
    settled = await waiter
    assert settled.result() == "settled"
    assert effect.done() is True


# --- Runtime Cancellation & Drain Contracts ---


@pytest.mark.asyncio
async def test_runner_returns_promptly_and_retains_blocking_worker_for_cleanup() -> None:
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
    from tests.unit.backend.rlm.fakes import EmptyCapabilities

    entered = threading.Event()
    release = threading.Event()
    cancel_requested = False

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
        capabilities=EmptyCapabilities(),
    )
    stream = RLMRunner(program_builder=Factory().create).stream(context)

    async def consume_all() -> None:
        async for _event in stream:
            pass

    consume = asyncio.create_task(consume_all())
    assert await asyncio.to_thread(entered.wait, 2)

    cancel_requested = True
    deadline = asyncio.get_running_loop().time() + 2
    while not consume.done() and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.01)
    assert consume.done(), "caller delivery must not wait for the non-cancellable worker"
    assert stream.outcome is not None
    assert stream.outcome.terminal_status == "cancelled"

    release.set()
    await asyncio.wait_for(stream.wait_owned(), timeout=2)


@pytest.mark.asyncio
async def test_runner_transfers_blocking_worker_after_caller_cancellation() -> None:
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
    from tests.unit.backend.rlm.fakes import EmptyCapabilities

    entered = threading.Event()
    release = threading.Event()

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
        capabilities=EmptyCapabilities(),
    )
    stream = RLMRunner(program_builder=Factory().create).stream(context)

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


@pytest.mark.asyncio
async def test_runner_close_drains_active_owners_and_rejects_new_streams() -> None:
    from fleet_rlm.rlm.execution import RLMRunner, RunTerminalError

    runner = RLMRunner()
    runner.stream(object())
    owner = next(iter(runner._active_ownerships))
    release = asyncio.Event()

    async def wait_owned() -> None:
        await release.wait()

    owner.wait_owned = wait_owned  # type: ignore[method-assign]
    with pytest.raises(TimeoutError, match="did not settle"):
        await runner.aclose(drain_seconds=0.01)
    with pytest.raises(RunTerminalError, match="closed"):
        runner.stream(object())
    release.set()
    await runner.aclose(drain_seconds=1)
    assert not runner._active_ownerships


# --- Runtime Outcomes & Usage Contracts ---


@pytest.mark.asyncio
async def test_runner_retains_prediction_usage_when_typed_output_is_invalid() -> None:
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
    from tests.unit.backend.rlm.fakes import EmptyCapabilities

    class Factory:
        def create(self, **_kwargs):
            class Program:
                async def acall(self, **_call_kwargs):
                    prediction = dspy.Prediction(
                        answer="",
                        trajectory=[
                            {"reasoning": "step one", "code": "x=1", "output": "1"},
                            {"reasoning": "step two", "code": "SUBMIT()", "output": "FINAL submitted"},
                        ],
                    )
                    prediction.set_lm_usage({"root": {"prompt_tokens": 9, "completion_tokens": 3}})
                    return prediction

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
        capabilities=EmptyCapabilities(),
    )
    stream = RLMRunner(program_builder=Factory().create).stream(context)
    _ = [event async for event in stream]

    from fleet_rlm.rlm.result import project_outcome_prediction

    assert stream.outcome is not None
    assert stream.outcome.succeeded
    projected = project_outcome_prediction(stream.outcome)
    assert not projected.succeeded
    assert projected.public_error_message == "Turn output is invalid"
    assert stream.outcome.usage["iterations"] == 2
    assert stream.outcome.usage["observed_lm_usage"] == {
        "root": {"prompt_tokens": 9, "completion_tokens": 3},
    }


@pytest.mark.asyncio
async def test_runner_reports_turn_output_too_large_for_oversized_answer() -> None:
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
    from tests.unit.backend.rlm.fakes import EmptyCapabilities

    class Factory:
        def create(self, **_kwargs):
            class Program:
                async def acall(self, **_call_kwargs):
                    prediction = dspy.Prediction(
                        answer="x" * 200,
                        trajectory=[
                            {
                                "reasoning": "submit long",
                                "code": "SUBMIT(answer=answer)",
                                "output": "FINAL submitted",
                            },
                        ],
                    )
                    prediction.set_lm_usage({"root": {"prompt_tokens": 2, "completion_tokens": 1}})
                    return prediction

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
            options=RLMOptions(max_output_chars=32, max_final_output_chars=32),
            deadline=asyncio.get_running_loop().time() + 10,
            interpreter=None,
            cancellation_requested=not_cancelled,
        ),
        capabilities=EmptyCapabilities(),
    )
    stream = RLMRunner(program_builder=Factory().create).stream(context)
    _ = [event async for event in stream]

    from fleet_rlm.rlm.result import project_outcome_prediction

    assert stream.outcome is not None
    assert stream.outcome.succeeded
    projected = project_outcome_prediction(stream.outcome)
    assert not projected.succeeded
    assert projected.public_error_message == "Turn output is too large"


@pytest.mark.asyncio
async def test_runner_emits_preloaded_skill_events_before_later_output_failure() -> None:
    from fleet_rlm.rlm.events import SkillActivated, SkillLoaded
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
    from tests.unit.backend.rlm.fakes import EmptyCapabilities

    class Capabilities(EmptyCapabilities):
        def __init__(self) -> None:
            super().__init__()
            self.details = [
                SkillActivated("skill-id", "long-context", "2.0.0", "system", ("load",)),
                SkillLoaded("skill-id", "long-context", "2.0.0"),
            ]

        def drain_public_details(self):
            values = tuple(self.details)
            self.details.clear()
            return values

    class Factory:
        def create(self, **_kwargs):
            class Program:
                async def acall(self, **_call_kwargs):
                    return dspy.Prediction(answer="", trajectory=[])

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
    stream = RLMRunner(program_builder=Factory().create).stream(context)
    events = [event async for event in stream]

    assert [event.kind for event in events] == [
        "run.started",
        "status",
        "skill.activated",
        "skill.loaded",
    ]
    assert stream.outcome is not None
    from fleet_rlm.rlm.result import project_outcome_prediction

    assert project_outcome_prediction(stream.outcome).terminal_status == "failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_status", ["cancelled", "timeout"])
async def test_runner_emits_preloaded_skill_events_before_cancel_or_timeout(terminal_status: str) -> None:
    from fleet_rlm.rlm.events import SkillActivated, SkillLoaded
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
    from tests.unit.backend.rlm.fakes import EmptyCapabilities

    class Capabilities(EmptyCapabilities):
        def __init__(self) -> None:
            super().__init__()
            self.details = [
                SkillActivated("skill-id", "long-context", "2.0.0", "system", ("load",)),
                SkillLoaded("skill-id", "long-context", "2.0.0"),
            ]

        def drain_public_details(self):
            values = tuple(self.details)
            self.details.clear()
            return values

    class Factory:
        def create(self, **_kwargs):
            class Program:
                async def acall(self, **_call_kwargs):
                    return dspy.Prediction(answer="late", trajectory=[])

            return Program()

    async def cancellation_probe() -> bool:
        return terminal_status == "cancelled"

    loop = asyncio.get_running_loop()
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
            deadline=loop.time() - 1 if terminal_status == "timeout" else loop.time() + 10,
            interpreter=None,
            cancellation_requested=cancellation_probe,
        ),
        capabilities=Capabilities(),
    )
    stream = RLMRunner(program_builder=Factory().create).stream(context)
    events = [event async for event in stream]

    assert [event.kind for event in events][:4] == [
        "run.started",
        "status",
        "skill.activated",
        "skill.loaded",
    ]
    assert stream.outcome is not None
    assert stream.outcome.terminal_status == terminal_status


@pytest.mark.asyncio
async def test_stream_closed_before_iteration_synthesizes_cancelled_outcome() -> None:
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
    from tests.unit.backend.rlm.fakes import EmptyCapabilities

    class Factory:
        def create(self, **_kwargs):
            raise AssertionError("worker factory must not run when the stream is closed early")

    async def not_cancelled() -> bool:
        return False

    loop = asyncio.get_running_loop()
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
            deadline=loop.time() + 10,
            interpreter=None,
            cancellation_requested=not_cancelled,
        ),
        capabilities=EmptyCapabilities(),
    )
    stream = RLMRunner(program_builder=Factory().create).stream(context)
    await stream.aclose()

    # Closing before any iteration must not raise IndexError: synthesize a
    # cancelled outcome matching the GeneratorExit path in ``_generate``.
    assert stream.outcome is not None
    assert stream.outcome.terminal_status == "cancelled"
    assert stream.outcome.public_error_message == "Turn cancelled"
    assert stream.outcome.usage == {"iterations": 0, "observed_lm_usage": {}, "duration_ms": 0}


def test_outcome_usage_with_delegation_commits_to_usage_part() -> None:
    from types import SimpleNamespace

    from fleet_rlm.rlm.recursion import DelegationMetrics
    from fleet_rlm.rlm.result import observed_usage
    from fleet_rlm.sessions.committed_turn import UsagePart

    metrics = DelegationMetrics()
    metrics.record_lm_call("root", 0)
    metrics.record_delegated_input_bytes(64)
    prediction = SimpleNamespace(trajectory=[], get_lm_usage=lambda: {})
    usage = observed_usage(
        prediction,
        duration_ms=7,
        lms=(SimpleNamespace(model="m", history=[{"usage": {"prompt_tokens": 8, "completion_tokens": 2}}]),),
        delegation={"recursive_call_count": 1, "delegation_metrics": metrics.snapshot().as_dict()},
    )

    part = UsagePart(value=usage)

    assert part.value["observed_lm_usage"]["m"]["input_tokens"] == 8
    assert part.value["delegation_metrics"]["delegated_input_bytes"] == 64
    assert tuple(part.value["delegation_metrics"]["lm_call_counts"]) == (
        {"role": "root", "recursive_depth": 0, "count": 1},
    )
    assert part.value["recursive_call_count"] == 1
