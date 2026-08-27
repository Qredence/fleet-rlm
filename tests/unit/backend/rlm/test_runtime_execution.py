"""Prepared-context RLM runner execution contract."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import dspy
import pytest


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
    from fleet_rlm.rlm.events import DetailRelay, RLMOutput, StepFinished, StepStarted

    relay = DetailRelay(maxsize=1)
    relay.publish(RLMOutput("queued", 1))
    relay.publish(StepStarted(1))
    relay.publish(RLMOutput("dropped", 1))
    assert await relay.get() == RLMOutput("queued", 1)
    relay.publish(StepFinished(1))

    assert relay.drain() == [StepStarted(1), StepFinished(1)]
    assert relay.overflowed is True


@pytest.mark.asyncio
async def test_runner_uses_native_path_for_plain_greeting() -> None:
    from fleet_rlm.chat.session_context import SessionContextManifest
    from fleet_rlm.rlm.program import RLMOptions
    from fleet_rlm.rlm.runtime import (
        ExecutionRuntime,
        RLMExecutionContext,
        RLMRunner,
        RunIdentity,
        SessionView,
    )
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

        def create(self, **_kwargs):
            self.created = True
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
            interpreter=None,
            cancellation_requested=not_cancelled,
        ),
        capabilities=EmptyCapabilities(),
    )

    factory = Factory()
    stream = RLMRunner(factory=factory).stream(context)
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
    assert stream.outcome is not None and stream.outcome.succeeded
    assert stream.outcome.prediction is not None
    assert stream.outcome.prediction.display_text == "Hi! How can I help you today?"
    assert stream.outcome.usage["iterations"] == 1


@pytest.mark.asyncio
async def test_runner_uses_supported_async_call_and_returns_typed_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify supported asynchronous execution produces a typed successful outcome.

    The outcome includes events, usage metrics, configured tools, and execution tracing.
    """
    from fleet_rlm.chat.session_context import SessionContextManifest
    from fleet_rlm.rlm.events import RLMCode, RLMOutput, StepFinished, StepStarted
    from fleet_rlm.rlm.program import RLMOptions
    from fleet_rlm.rlm.runtime import (
        ExecutionRuntime,
        RLMExecutionContext,
        RLMExecutionSpec,
        RLMRunner,
        RunIdentity,
        SessionView,
    )
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
    stream = RLMRunner(factory=factory).stream(context)
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
    assert stream.outcome.prediction.display_text == "42"
    assert stream.outcome.prediction.outputs == {"answer": "42"}
    assert stream.outcome.succeeded
    assert factory.options is context.execution.options
    assert isinstance(factory.tools[0], dspy.Tool)
    assert stream.outcome.usage["iterations"] == 1
    assert stream.outcome.usage["observed_lm_usage"] == {"root": {"prompt_tokens": 4, "completion_tokens": 2}}
    assert set(stream.outcome.usage) == {"iterations", "observed_lm_usage", "duration_ms"}
    assert len(contexts) == 1
    assert contexts[0]["lm"] is context.execution.models.root_lm
    assert contexts[0]["track_usage"] is True
    adapter = contexts[0]["adapter"]
    assert isinstance(adapter, dspy.JSONAdapter)
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
    from fleet_rlm.chat.session_context import SessionContextManifest
    from fleet_rlm.rlm.program import (
        AttachmentContextCapsule,
        AttachmentContextEntry,
        RLMOptions,
    )
    from fleet_rlm.rlm.runtime import (
        ExecutionRuntime,
        RLMExecutionContext,
        RLMRunner,
        RunIdentity,
        SessionView,
    )
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

    stream = RLMRunner(factory=Factory()).stream(context)
    _events = [event async for event in stream]

    assert stream.outcome is not None and stream.outcome.succeeded


@pytest.mark.asyncio
async def test_runner_validates_host_metadata_before_provider_execution() -> None:
    from fleet_rlm.chat.session_context import SessionContextManifest, TurnPreview
    from fleet_rlm.rlm.program import RLMOptions
    from fleet_rlm.rlm.runtime import (
        ExecutionRuntime,
        RLMExecutionContext,
        RLMRunner,
        RunIdentity,
        SessionView,
    )
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
    stream = RLMRunner(factory=factory).stream(context)
    _events = [event async for event in stream]

    assert factory.program.acall_calls == 0
    assert stream.outcome is not None
    assert stream.outcome.terminal_status == "failed"
    assert stream.outcome.public_error_message == "Turn failed"


@pytest.mark.asyncio
async def test_runner_loads_two_skills_reads_python_resource_and_completes_submit() -> None:
    from fleet_rlm.chat.capability_preparation import PreparedHostCapabilities
    from fleet_rlm.chat.session_context import SessionContextManifest
    from fleet_rlm.rlm.program import RLMOptions
    from fleet_rlm.rlm.runtime import (
        ExecutionRuntime,
        RLMExecutionContext,
        RLMExecutionSpec,
        RLMRunner,
        RunIdentity,
        SessionView,
    )
    from fleet_rlm.sessions.models import TurnAccess
    from fleet_rlm.skills.catalog import SkillCatalog
    from fleet_rlm.skills.models import SkillCard, SkillDefinition, SkillResource
    from fleet_rlm.skills.tools import SkillToolHost

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
            interpreter=None,
            cancellation_requested=not_cancelled,
        ),
        capabilities=capabilities,
    )
    stream = RLMRunner(factory=Factory()).stream(context)
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
    assert stream.outcome.prediction.display_text == "progressive completion"
