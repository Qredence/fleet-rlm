"""Prepared-context RLM runner execution contract."""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from uuid import uuid4

import dspy
import pytest


@pytest.mark.asyncio
async def test_runner_uses_supported_async_call_and_returns_typed_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fleet_rlm.chat.session_context import SessionContextManifest
    from fleet_rlm.files.workspace_models import WorkspaceCapabilityMetadata
    from fleet_rlm.rlm.context import RLMExecutionContext, RLMExecutionSpec
    from fleet_rlm.rlm.dspy_contract import RLMOptions
    from fleet_rlm.rlm.events import RLMCode, RLMOutput, StepFinished, StepStarted
    from fleet_rlm.rlm.runner import RLMRunner
    from fleet_rlm.sessions.models import TurnAccess
    from fleet_rlm.skills.models import SkillCard

    class Capabilities:
        spec = RLMExecutionSpec(
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
    skill_id = uuid4()
    main_thread = threading.get_ident()
    contexts: list[dict[str, object]] = []
    original_context = dspy.context
    global_adapter = dspy.settings.adapter

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
    Capabilities.spec = RLMExecutionSpec(
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
    assert len(contexts) == 1
    assert contexts[0]["lm"] is context.models.root_lm
    assert contexts[0]["track_usage"] is True
    adapter = contexts[0]["adapter"]
    assert type(adapter) is dspy.JSONAdapter
    assert adapter.use_native_function_calling is True
    assert dspy.settings.adapter is global_adapter


@pytest.mark.asyncio
async def test_runner_validates_host_metadata_before_provider_execution() -> None:
    from fleet_rlm.chat.session_context import SessionContextManifest, TurnPreview
    from fleet_rlm.rlm.context import RLMExecutionContext
    from fleet_rlm.rlm.dspy_contract import RLMOptions
    from fleet_rlm.rlm.runner import RLMRunner
    from fleet_rlm.sessions.models import TurnAccess

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

    class Capabilities:
        spec = SimpleNamespace(tools=(), tool_event_views={}, skill_cards=(), signature=None, workspace=None)

        def drain_public_details(self):
            return ()

        def drain_artifact_candidates(self):
            return ()

    async def not_cancelled() -> bool:
        return False

    malformed_context = SessionContextManifest(
        "not-a-uuid",  # type: ignore[arg-type]
        -1,
        0,
        (TurnPreview(0, "system", "malformed"),),  # type: ignore[arg-type]
    )
    context = RLMExecutionContext(
        uuid4(),
        uuid4(),
        TurnAccess(uuid4(), uuid4()),
        "validate me",
        malformed_context,
        SimpleNamespace(root_lm=object(), sub_lm=object()),
        RLMOptions(),
        asyncio.get_running_loop().time() + 10,
        None,
        (),
        Capabilities(),
        not_cancelled,
        (),
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
    from fleet_rlm.chat.deno_run_environment import DenoPreparedCapabilities
    from fleet_rlm.chat.session_context import SessionContextManifest
    from fleet_rlm.rlm.context import RLMExecutionContext, RLMExecutionSpec
    from fleet_rlm.rlm.dspy_contract import RLMOptions
    from fleet_rlm.rlm.runner import RLMRunner
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

    capabilities = DenoPreparedCapabilities(spec, files=Files(), skills=skill_host)

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
