"""Integrated native typed-Prediction to Turn Commit contract."""

from __future__ import annotations

import asyncio
from types import MappingProxyType, SimpleNamespace
from uuid import uuid4

import dspy
import pytest


class ReportFinding(dspy.Signature):
    request: str = dspy.InputField()
    summary: str = dspy.OutputField()
    findings: list[dict[str, str]] = dspy.OutputField()


class _Capabilities:
    def __init__(self, blueprint) -> None:
        self.blueprint = blueprint

    def drain_public_details(self):
        return ()

    def drain_artifact_candidates(self):
        return ()

    async def aclose(self) -> None:
        return None


class _Factory:
    def __init__(self, prediction: dspy.Prediction) -> None:
        self.prediction = prediction

    def create(self, **_kwargs):
        factory = self

        class Program:
            async def acall(self, **kwargs):
                factory.kwargs = kwargs
                return factory.prediction

        return Program()


async def _run_prediction(prediction: dspy.Prediction, blueprint):
    from fleet_rlm.chat.session_context import SessionContextManifest
    from fleet_rlm.rlm.context import RLMExecutionContext
    from fleet_rlm.rlm.dspy_contract import RLMOptions
    from fleet_rlm.rlm.runner import RLMRunner
    from fleet_rlm.sessions.models import TurnAccess

    async def not_cancelled() -> bool:
        return False

    factory = _Factory(prediction)
    context = RLMExecutionContext(
        uuid4(),
        uuid4(),
        TurnAccess(uuid4(), uuid4()),
        "review this report",
        SessionContextManifest(uuid4(), 0, 0, ()),
        SimpleNamespace(root_lm=object(), sub_lm=object()),
        RLMOptions(),
        asyncio.get_running_loop().time() + 10,
        None,
        (),
        _Capabilities(blueprint),
        not_cancelled,
        (),
    )
    stream = RLMRunner(factory=factory).stream(context)
    _events = [event async for event in stream]
    assert stream.outcome is not None
    return stream.outcome, factory


@pytest.mark.asyncio
async def test_default_and_custom_signatures_commit_native_typed_predictions() -> None:
    from fleet_rlm.chat.turn_detail_policy import commit_success
    from fleet_rlm.skills.capabilities import DEFAULT_TASK_CONTRACT, TaskContract, TurnCapabilityBlueprint

    default_outcome, _ = await _run_prediction(
        dspy.Prediction(answer="hello", trajectory=[]),
        TurnCapabilityBlueprint(),
    )
    default_turn = commit_success(default_outcome, ())
    assert default_turn.text == "hello"
    assert default_turn.structured_result is None
    assert [part.type for part in default_turn.parts] == ["usage", "text"]
    assert default_outcome.prediction is not None
    assert default_outcome.prediction.schema_id == DEFAULT_TASK_CONTRACT.id

    contract = TaskContract(
        "fleet.report",
        "1",
        ReportFinding,
        lambda context: {"request": context.request},
        "summary",
    )
    custom_outcome, factory = await _run_prediction(
        dspy.Prediction(
            summary="Three findings",
            findings=[{"title": "First", "severity": "high"}],
            trajectory=[],
        ),
        TurnCapabilityBlueprint(
            task_contract=contract,
            input_values=MappingProxyType({"request": "review this report"}),
        ),
    )
    custom_turn = commit_success(custom_outcome, ())

    assert factory.kwargs == {"request": "review this report", "skill_cards": []}
    assert custom_turn.text == "Three findings"
    assert custom_turn.structured_result == {
        "summary": "Three findings",
        "findings": [{"title": "First", "severity": "high"}],
    }
    assert [part.type for part in custom_turn.parts] == ["usage", "structured_result", "text"]

    rejecting_contract = TaskContract(
        "fleet.rejecting-report",
        "1",
        ReportFinding,
        lambda context: {"request": context.request},
        "summary",
        lambda _outputs: (_ for _ in ()).throw(RuntimeError("private validator detail")),
    )
    rejected, _ = await _run_prediction(
        dspy.Prediction(summary="hidden", findings=[], trajectory=[]),
        TurnCapabilityBlueprint(
            task_contract=rejecting_contract,
            input_values=MappingProxyType({"request": "review this report"}),
        ),
    )
    assert rejected.terminal_status == "failed"
    assert rejected.prediction is None
    assert rejected.public_error_message == "Turn output is invalid"


@pytest.mark.asyncio
async def test_invalid_submit_repair_commits_only_the_final_typed_prediction() -> None:
    from fleet_rlm.chat.session_context import SessionContextManifest
    from fleet_rlm.chat.turn_detail_policy import commit_success
    from fleet_rlm.daytona.in_process import InProcessInterpreterBackend
    from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter
    from fleet_rlm.rlm.context import RLMExecutionContext
    from fleet_rlm.rlm.dspy_contract import RLMOptions
    from fleet_rlm.rlm.factory import RLMFactory
    from fleet_rlm.rlm.model_bundle import RLMModelBundle
    from fleet_rlm.rlm.runner import RLMRunner
    from fleet_rlm.sessions.models import TurnAccess
    from fleet_rlm.skills.capabilities import TurnCapabilityBlueprint

    class Actions:
        calls = 0

        async def acall(self, **_kwargs):
            self.calls += 1
            code = "SUBMIT(wrong='invalid')" if self.calls == 1 else "SUBMIT(answer='repaired')"
            return dspy.Prediction(reasoning="repair invalid typed submit", code=code)

    class Factory:
        def __init__(self) -> None:
            self.actions = Actions()

        def create(self, **kwargs):
            rlm = RLMFactory().create(**kwargs)
            rlm.generate_action = self.actions
            return rlm

    async def not_cancelled() -> bool:
        return False

    context = RLMExecutionContext(
        uuid4(),
        uuid4(),
        TurnAccess(uuid4(), uuid4()),
        "repair",
        SessionContextManifest(uuid4(), 0, 0, ()),
        RLMModelBundle(root_lm=object(), sub_lm=object()),
        RLMOptions(max_iterations=2),
        asyncio.get_running_loop().time() + 10,
        DaytonaCodeInterpreter(backend=InProcessInterpreterBackend()),
        (),
        _Capabilities(TurnCapabilityBlueprint()),
        not_cancelled,
        (),
    )
    factory = Factory()
    stream = RLMRunner(factory=factory).stream(context)
    _events = [event async for event in stream]

    assert stream.outcome is not None
    committed = commit_success(stream.outcome, ())
    assert factory.actions.calls == 2
    assert committed.text == "repaired"
    assert committed.structured_result is None
