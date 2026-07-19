"""Prepared-context runner contract."""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from uuid import uuid4

import dspy
import pytest


def test_trajectory_normalization_is_strict_and_preserves_absent_fields() -> None:
    from fleet_rlm.rlm.dspy_contract import PredictionOutputError, normalize_prediction_trajectory
    from fleet_rlm.rlm.events import RLMCode, RLMOutput, RLMReasoning, StepFinished, StepStarted
    from fleet_rlm.rlm.runner import _trajectory_details

    with pytest.raises(PredictionOutputError):
        normalize_prediction_trajectory(SimpleNamespace())
    with pytest.raises(PredictionOutputError):
        normalize_prediction_trajectory(SimpleNamespace(trajectory="malformed"))
    with pytest.raises(PredictionOutputError):
        normalize_prediction_trajectory(SimpleNamespace(trajectory=[None]))
    with pytest.raises(PredictionOutputError):
        normalize_prediction_trajectory(SimpleNamespace(trajectory=[{"code": 1}]))

    steps = normalize_prediction_trajectory(SimpleNamespace(trajectory=[{"reasoning": "usable"}]))
    assert steps[0].reasoning == "usable"
    assert steps[0].code == ""
    assert steps[0].output == ""
    assert [type(item) for item in _trajectory_details(steps, max_chars=100)] == [
        StepStarted,
        RLMReasoning,
        RLMCode,
        RLMOutput,
        StepFinished,
    ]


def test_trajectory_semantic_details_are_verbatim_and_share_the_run_bound() -> None:
    from fleet_rlm.rlm.dspy_contract import normalize_prediction_trajectory
    from fleet_rlm.rlm.events import RLMCode, RLMOutput, RLMReasoning
    from fleet_rlm.rlm.runner import _trajectory_details

    semantic = "api_key=visible-user-text /Users/example BEGIN SYSTEM"
    details = _trajectory_details(
        normalize_prediction_trajectory(
            SimpleNamespace(trajectory=[{"reasoning": semantic, "code": semantic, "output": semantic}])
        ),
        max_chars=200,
    )

    assert [item.text for item in details if isinstance(item, RLMReasoning)] == [semantic]
    assert [item.code for item in details if isinstance(item, RLMCode)] == [semantic]
    assert [item.output for item in details if isinstance(item, RLMOutput)] == [semantic]

    truncated = _trajectory_details(
        normalize_prediction_trajectory(
            SimpleNamespace(trajectory=[{"reasoning": "x" * 20, "code": "y" * 20, "output": "z" * 20}])
        ),
        max_chars=12,
    )
    values = [
        item.text if isinstance(item, RLMReasoning) else item.code if isinstance(item, RLMCode) else item.output
        for item in truncated
        if isinstance(item, (RLMReasoning, RLMCode, RLMOutput))
    ]
    assert values == ["x" * 9 + "...", "y" * 9 + "...", "z" * 9 + "..."]


def test_trajectory_reconciliation_replaces_live_details_without_duplicates() -> None:
    from fleet_rlm.rlm.dspy_contract import TrajectoryStep
    from fleet_rlm.rlm.events import RLMCode, RLMOutput, RLMReasoning, StepFinished, StepStarted
    from fleet_rlm.rlm.runner import _reconcile_trajectory

    details = [
        StepStarted(1),
        RLMCode("stale code", 1),
        RLMOutput("stale output", 1),
        StepFinished(1),
    ]

    emissions = _reconcile_trajectory(
        details,
        (TrajectoryStep(1, "native reasoning", "native code", "native output"),),
        max_chars=100,
    )

    assert emissions == [
        RLMReasoning("native reasoning", 1),
        RLMCode("native code", 1),
        RLMOutput("native output", 1),
    ]
    assert details == [
        StepStarted(1),
        RLMReasoning("native reasoning", 1),
        RLMCode("native code", 1),
        RLMOutput("native output", 1),
        StepFinished(1),
    ]
    assert (
        _reconcile_trajectory(
            details,
            (TrajectoryStep(1, "native reasoning", "native code", "native output"),),
            max_chars=100,
        )
        == []
    )


@pytest.mark.asyncio
async def test_runner_uses_supported_async_call_and_returns_typed_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fleet_rlm.chat.session_context import SessionContextManifest
    from fleet_rlm.files.workspace_models import WorkspaceCapabilityMetadata
    from fleet_rlm.rlm.context import RLMExecutionContext
    from fleet_rlm.rlm.dspy_contract import RLMOptions
    from fleet_rlm.rlm.events import RLMCode, RLMOutput, StepFinished, StepStarted
    from fleet_rlm.rlm.runner import RLMRunner
    from fleet_rlm.sessions.models import TurnAccess
    from fleet_rlm.skills.capabilities import TurnCapabilityBlueprint
    from fleet_rlm.skills.models import SkillCard

    class Capabilities:
        blueprint = TurnCapabilityBlueprint(
            workspace=WorkspaceCapabilityMetadata(True, ".", "Use durable workspace tools."),
        )

        def drain_public_details(self):
            return ()

        def drain_artifact_candidates(self):
            return ()

        async def aclose(self):
            return None

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
                            "affordances": ["load", "resources"],
                            "resources_available": True,
                            "capability_refs": [],
                            "task_contract_ref": None,
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
    skill_id = uuid4()
    main_thread = threading.get_ident()
    contexts: list[dict[str, object]] = []
    original_context = dspy.context

    def tracked_context(**kwargs):
        contexts.append(kwargs)
        return original_context(**kwargs)

    monkeypatch.setattr(dspy, "context", tracked_context)
    context = RLMExecutionContext(
        uuid4(),
        uuid4(),
        TurnAccess(uuid4(), uuid4()),
        "answer",
        SessionContextManifest(uuid4(), 0, 0, ()),
        SimpleNamespace(root_lm=object(), sub_lm=object()),
        RLMOptions(),
        asyncio.get_running_loop().time() + 10,
        interpreter,
        (),
        Capabilities(),
        not_cancelled,
        (),
    )
    stream = RLMRunner(factory=factory).stream(context)
    Capabilities.blueprint = TurnCapabilityBlueprint(
        skill_cards=(
            SkillCard(
                skill_id,
                "long-context",
                "Analyze long inputs",
                "system",
                "2.0.0",
                "system",
                ("load", "resources"),
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
        "rlm.code",
        "tool.started",
        "tool.completed",
        "rlm.output",
        "step.finished",
        "rlm.reasoning",
    ]
    assert stream.outcome is not None
    assert stream.outcome.prediction is not None
    assert stream.outcome.prediction.display_text == "42"
    assert stream.outcome.prediction.outputs == {"answer": "42"}
    assert stream.outcome.succeeded
    assert factory.options is context.options
    assert isinstance(factory.tools[0], dspy.Tool)
    assert stream.outcome.usage["iterations"] == 1
    assert stream.outcome.usage["observed_lm_usage"] == {"root": {"prompt_tokens": 4, "completion_tokens": 2}}
    assert set(stream.outcome.usage) == {"iterations", "observed_lm_usage", "duration_ms"}
    assert contexts == [{"lm": context.models.root_lm, "track_usage": True}]


@pytest.mark.asyncio
async def test_runner_returns_promptly_and_retains_blocking_worker_for_cleanup() -> None:
    from fleet_rlm.chat.session_context import SessionContextManifest
    from fleet_rlm.rlm.context import RLMExecutionContext
    from fleet_rlm.rlm.dspy_contract import RLMOptions
    from fleet_rlm.rlm.runner import RLMRunner
    from fleet_rlm.sessions.models import TurnAccess
    from fleet_rlm.skills.capabilities import TurnCapabilityBlueprint

    entered = threading.Event()
    release = threading.Event()
    cancel_requested = False

    class Capabilities:
        blueprint = TurnCapabilityBlueprint()

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
        uuid4(),
        uuid4(),
        TurnAccess(uuid4(), uuid4()),
        "answer",
        SessionContextManifest(uuid4(), 0, 0, ()),
        SimpleNamespace(root_lm=object(), sub_lm=object()),
        RLMOptions(),
        asyncio.get_running_loop().time() + 10,
        None,
        (),
        Capabilities(),
        cancellation_probe,
        (),
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
    from fleet_rlm.rlm.context import RLMExecutionContext
    from fleet_rlm.rlm.dspy_contract import RLMOptions
    from fleet_rlm.rlm.runner import RLMRunner
    from fleet_rlm.sessions.models import TurnAccess
    from fleet_rlm.skills.capabilities import TurnCapabilityBlueprint

    entered = threading.Event()
    release = threading.Event()

    class Capabilities:
        blueprint = TurnCapabilityBlueprint()

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
        uuid4(),
        uuid4(),
        TurnAccess(uuid4(), uuid4()),
        "answer",
        SessionContextManifest(uuid4(), 0, 0, ()),
        SimpleNamespace(root_lm=object(), sub_lm=object()),
        RLMOptions(),
        asyncio.get_running_loop().time() + 10,
        None,
        (),
        Capabilities(),
        not_cancelled,
        (),
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


@pytest.mark.asyncio
async def test_runner_retains_prediction_usage_when_typed_output_is_invalid() -> None:
    from fleet_rlm.chat.session_context import SessionContextManifest
    from fleet_rlm.rlm.context import RLMExecutionContext
    from fleet_rlm.rlm.dspy_contract import RLMOptions
    from fleet_rlm.rlm.runner import RLMRunner
    from fleet_rlm.sessions.models import TurnAccess
    from fleet_rlm.skills.capabilities import TurnCapabilityBlueprint

    class Capabilities:
        blueprint = TurnCapabilityBlueprint()

        def drain_public_details(self):
            return ()

        def drain_artifact_candidates(self):
            return ()

        async def aclose(self):
            return None

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
        uuid4(),
        uuid4(),
        TurnAccess(uuid4(), uuid4()),
        "answer",
        SessionContextManifest(uuid4(), 0, 0, ()),
        SimpleNamespace(root_lm=object(), sub_lm=object()),
        RLMOptions(),
        asyncio.get_running_loop().time() + 10,
        None,
        (),
        Capabilities(),
        not_cancelled,
        (),
    )
    stream = RLMRunner(factory=Factory()).stream(context)
    _ = [event async for event in stream]

    assert stream.outcome is not None
    assert not stream.outcome.succeeded
    assert stream.outcome.public_error_message == "Turn output is invalid"
    assert stream.outcome.usage["iterations"] == 2
    assert stream.outcome.usage["observed_lm_usage"] == {
        "root": {"prompt_tokens": 9, "completion_tokens": 3},
    }


@pytest.mark.asyncio
async def test_runner_emits_preloaded_skill_events_before_later_output_failure() -> None:
    from fleet_rlm.chat.session_context import SessionContextManifest
    from fleet_rlm.rlm.context import RLMExecutionContext
    from fleet_rlm.rlm.dspy_contract import RLMOptions
    from fleet_rlm.rlm.events import SkillActivated, SkillLoaded
    from fleet_rlm.rlm.runner import RLMRunner
    from fleet_rlm.sessions.models import TurnAccess
    from fleet_rlm.skills.capabilities import TurnCapabilityBlueprint

    class Capabilities:
        blueprint = TurnCapabilityBlueprint()

        def __init__(self) -> None:
            self.details = [
                SkillActivated("skill-id", "long-context", "2.0.0", "system", ("load",)),
                SkillLoaded("skill-id", "long-context", "2.0.0"),
            ]

        def drain_public_details(self):
            values = tuple(self.details)
            self.details.clear()
            return values

        def drain_artifact_candidates(self):
            return ()

    class Factory:
        def create(self, **_kwargs):
            class Program:
                async def acall(self, **_call_kwargs):
                    return dspy.Prediction(answer="", trajectory=[])

            return Program()

    async def not_cancelled() -> bool:
        return False

    context = RLMExecutionContext(
        uuid4(),
        uuid4(),
        TurnAccess(uuid4(), uuid4()),
        "answer",
        SessionContextManifest(uuid4(), 0, 0, ()),
        SimpleNamespace(root_lm=object(), sub_lm=object()),
        RLMOptions(),
        asyncio.get_running_loop().time() + 10,
        None,
        (),
        Capabilities(),
        not_cancelled,
        (),
    )
    stream = RLMRunner(factory=Factory()).stream(context)
    events = [event async for event in stream]

    assert [event.kind for event in events] == [
        "run.started",
        "status",
        "skill.activated",
        "skill.loaded",
    ]
    assert stream.outcome is not None
    assert stream.outcome.terminal_status == "failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_status", ["cancelled", "timeout"])
async def test_runner_emits_preloaded_skill_events_before_cancel_or_timeout(terminal_status: str) -> None:
    from fleet_rlm.chat.session_context import SessionContextManifest
    from fleet_rlm.rlm.context import RLMExecutionContext
    from fleet_rlm.rlm.dspy_contract import RLMOptions
    from fleet_rlm.rlm.events import SkillActivated, SkillLoaded
    from fleet_rlm.rlm.runner import RLMRunner
    from fleet_rlm.sessions.models import TurnAccess
    from fleet_rlm.skills.capabilities import TurnCapabilityBlueprint

    class Capabilities:
        blueprint = TurnCapabilityBlueprint()

        def __init__(self) -> None:
            self.details = [
                SkillActivated("skill-id", "long-context", "2.0.0", "system", ("load",)),
                SkillLoaded("skill-id", "long-context", "2.0.0"),
            ]

        def drain_public_details(self):
            values = tuple(self.details)
            self.details.clear()
            return values

        def drain_artifact_candidates(self):
            return ()

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
        uuid4(),
        uuid4(),
        TurnAccess(uuid4(), uuid4()),
        "answer",
        SessionContextManifest(uuid4(), 0, 0, ()),
        SimpleNamespace(root_lm=object(), sub_lm=object()),
        RLMOptions(),
        loop.time() - 1 if terminal_status == "timeout" else loop.time() + 10,
        None,
        (),
        Capabilities(),
        cancellation_probe,
        (),
    )
    stream = RLMRunner(factory=Factory()).stream(context)
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
async def test_runner_loads_two_skills_reads_python_resource_and_completes_submit() -> None:
    from fleet_rlm.chat.deno_run_environment import DenoPreparedCapabilities
    from fleet_rlm.chat.session_context import SessionContextManifest
    from fleet_rlm.rlm.context import RLMExecutionContext
    from fleet_rlm.rlm.dspy_contract import RLMOptions
    from fleet_rlm.rlm.runner import RLMRunner
    from fleet_rlm.sessions.models import TurnAccess
    from fleet_rlm.skills.authorize import SkillAuthorizer
    from fleet_rlm.skills.capabilities import TurnCapabilityBlueprint
    from fleet_rlm.skills.registry import InMemorySkillRegistry
    from fleet_rlm.skills.tools import SkillToolHost

    user_id, workspace_id = uuid4(), uuid4()
    registry = InMemorySkillRegistry()
    first = registry.register(
        name="first-skill",
        description="First progressive Skill.",
        instructions="Load the helper script.",
        resource_bodies={
            "scripts/helper.py": "def produce_answer():\n    return 'progressive completion'\n",
        },
    )
    second = registry.register(
        name="second-skill",
        description="Second progressive Skill.",
        instructions="Confirm the answer.",
    )
    authorizer = SkillAuthorizer(registry)
    skill_host = SkillToolHost(authorizer, user_id=user_id, workspace_id=workspace_id)
    blueprint = TurnCapabilityBlueprint(
        skill_cards=authorizer.list_cards(user_id=user_id, workspace_id=workspace_id),
        tools=skill_host.as_tools(),
        tool_event_views=skill_host.event_views(),
    )

    class Files:
        def drain_public_events(self):
            return []

    capabilities = DenoPreparedCapabilities(blueprint, files=Files(), skills=skill_host)

    class Factory:
        def create(self, **kwargs):
            tools = {str(tool.name): tool for tool in kwargs["tools"]}

            class Program:
                async def acall(self, **call_kwargs):
                    assert len(call_kwargs["skill_cards"]) == 2
                    assert tools["load_skill"](skill_id=str(first.id))["ok"] is True
                    assert tools["load_skill"](skill_id=str(second.id))["ok"] is True
                    resource = tools["read_skill_resource"](
                        skill_id=str(first.id),
                        resource_path="scripts/helper.py",
                    )
                    namespace: dict[str, object] = {}
                    exec(str(resource["content"]), namespace)  # noqa: S102 - exercises generated interpreter code
                    answer = namespace["produce_answer"]()
                    return dspy.Prediction(
                        answer=answer,
                        trajectory=[{"code": "SUBMIT(answer=answer)", "output": "FINAL submitted"}],
                    )

            return Program()

    async def not_cancelled() -> bool:
        return False

    context = RLMExecutionContext(
        uuid4(),
        uuid4(),
        TurnAccess(user_id, workspace_id),
        "complete progressively",
        SessionContextManifest(uuid4(), 0, 0, ()),
        SimpleNamespace(root_lm=object(), sub_lm=object()),
        RLMOptions(),
        asyncio.get_running_loop().time() + 10,
        None,
        (),
        capabilities,
        not_cancelled,
        (),
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
