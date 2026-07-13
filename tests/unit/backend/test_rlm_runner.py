"""impl-02: RLMRunner streams RuntimeEvents with fakes."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from types import MappingProxyType
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from fleet_rlm.rlm.budgets import RLMBudget
from fleet_rlm.rlm.context import RLMTurnContext
from fleet_rlm.rlm.events import RuntimeEventKind
from fleet_rlm.rlm.model_bundle import RLMModelBundle
from fleet_rlm.rlm.observable import RLMDetail, RLMDetailKind
from fleet_rlm.rlm.runner import RLMRunner
from fleet_rlm.skills.capabilities import TurnCapabilityBlueprint
from fleet_rlm.skills.models import SkillCard


class _FakeLease:
    def __init__(self, interpreter: Any = None) -> None:
        self.interpreter = interpreter if interpreter is not None else MagicMock(name="interp")
        self.released = 0

    def release(self) -> None:
        self.released += 1


class _FakeRLM:
    """Stand-in module: exposes sub_lm and async aforward."""

    def __init__(
        self,
        *,
        answer: str = "done",
        sub_lm: Any = None,
        fail: BaseException | None = None,
        capture: dict[str, Any] | None = None,
        observer: Any = None,
    ) -> None:
        self.answer = answer
        self.sub_lm = sub_lm
        self.fail = fail
        self.capture = capture if capture is not None else {}
        self.observer = observer

    async def aforward(self, **kwargs: Any) -> Any:
        import dspy

        self.capture["request"] = kwargs.get("request")
        self.capture["kwargs"] = kwargs
        self.capture["settings_lm"] = getattr(dspy.settings, "lm", None)
        if self.observer is not None:
            self.observer(RLMDetail(RLMDetailKind.STEP_STARTED, {"step": 1}))
            await asyncio.sleep(0)
            self.observer(RLMDetail(RLMDetailKind.REASONING, {"step": 1, "text": "inspect"}))
            self.observer(RLMDetail(RLMDetailKind.STEP_FINISHED, {"step": 1}))
        if self.fail is not None:
            raise self.fail
        prediction = dspy.Prediction(answer=self.answer)
        return prediction


class _FakeFactory:
    def __init__(self, rlm: Any | None = None, *, maker: Any = None) -> None:
        self._rlm = rlm
        self._maker = maker
        self.created: list[Any] = []
        self.last_kwargs: dict[str, Any] = {}

    def create(self, **kwargs: Any) -> Any:
        self.last_kwargs = kwargs
        if self._maker is not None:
            instance = self._maker(**kwargs)
        else:
            instance = self._rlm
            if instance is not None:
                instance.observer = kwargs.get("observer")
        self.created.append(instance)
        return instance


def _context(
    *,
    lease: _FakeLease | None = None,
    root: Any | None = None,
    sub: Any | None = None,
    request: str = "hello",
) -> tuple[RLMTurnContext, _FakeLease]:
    lease = lease or _FakeLease()
    root = root if root is not None else MagicMock(name="root_lm")
    sub = sub if sub is not None else MagicMock(name="sub_lm")
    ctx = RLMTurnContext(
        run_id=uuid4(),
        session_id=uuid4(),
        user_id=uuid4(),
        workspace_id=uuid4(),
        request=request,
        models=RLMModelBundle(root_lm=root, sub_lm=sub),
        budget=RLMBudget(max_iterations=3, max_llm_calls=5, max_output_chars=1000),
        lease=lease,
    )
    return ctx, lease


class _Resolver:
    def __init__(self, blueprint: TurnCapabilityBlueprint) -> None:
        self.blueprint = blueprint

    async def resolve(self, _context: RLMTurnContext) -> TurnCapabilityBlueprint:
        return self.blueprint


async def _collect(runner: RLMRunner, context: RLMTurnContext) -> tuple[list[Any], Any]:
    stream = runner.stream(context)
    events = [event async for event in stream]
    return events, stream.outcome


@pytest.mark.asyncio
async def test_runner_emits_start_text_usage_and_one_terminal() -> None:
    sub = MagicMock(name="sub_lm")
    fake_rlm = _FakeRLM(answer="world", sub_lm=sub)
    factory = _FakeFactory(fake_rlm)
    ctx, lease = _context(sub=sub)

    events, outcome = await _collect(RLMRunner(factory=factory), ctx)
    kinds = [e.kind for e in events]

    assert kinds[0] == RuntimeEventKind.RUN_STARTED
    assert RuntimeEventKind.STATUS in kinds
    assert RuntimeEventKind.TEXT_DELTA not in kinds
    assert RuntimeEventKind.TEXT_COMPLETED not in kinds
    assert RuntimeEventKind.USAGE not in kinds
    assert RuntimeEventKind.RUN_COMPLETED not in kinds
    assert RuntimeEventKind.ERROR not in kinds
    assert outcome is not None
    assert outcome.terminal_status == "completed"
    assert outcome.assistant_text == "world"
    assert outcome.usage["iterations"] == 1
    assert outcome.usage["tool_calls"] == 0
    assert outcome.usage["sub_lm_calls"] == 0
    assert outcome.usage["iteration_limit"] == 3
    assert outcome.usage["sub_lm_call_limit"] == 5
    assert outcome.usage["estimated_cost"] is None
    assert lease.released == 0
    assert factory.last_kwargs["models"].sub_lm is sub
    assert factory.last_kwargs["interpreter"] is lease.interpreter


@pytest.mark.asyncio
async def test_runner_applies_root_lm_via_dspy_settings_context() -> None:
    root = MagicMock(name="root_lm")
    capture: dict[str, Any] = {}
    fake_rlm = _FakeRLM(answer="ok", capture=capture)
    ctx, _lease = _context(root=root)

    await _collect(RLMRunner(factory=_FakeFactory(fake_rlm)), ctx)

    assert capture["settings_lm"] is root
    assert capture["request"] == "hello"


@pytest.mark.asyncio
async def test_runner_sanitizes_failures_without_releasing_coordinator_owned_lease() -> None:
    secret = "api_key=sk-super-secret-value"
    fake_rlm = _FakeRLM(fail=RuntimeError(f"provider boom {secret} /Users/zoe/secret/path"))
    ctx, lease = _context()

    events, outcome = await _collect(RLMRunner(factory=_FakeFactory(fake_rlm)), ctx)
    kinds = [e.kind for e in events]

    assert RuntimeEventKind.ERROR not in kinds
    assert outcome is not None
    assert outcome.terminal_status == "failed"
    assert outcome.usage["iterations"] == 1
    assert outcome.usage["tool_call_limit"] == ctx.budget.max_tool_calls
    assert outcome.usage["root_model_profile"]
    assert outcome.usage["sub_model_profile"]
    message = outcome.public_error_message or ""
    assert "sk-super-secret-value" not in message
    assert "/Users/zoe" not in message
    assert lease.released == 0


@pytest.mark.asyncio
async def test_concurrent_runs_use_distinct_rlm_instances() -> None:
    instances: list[Any] = []

    def maker(**_kwargs: Any) -> _FakeRLM:
        rlm = _FakeRLM(answer=f"n{len(instances)}")
        instances.append(rlm)
        return rlm

    factory = _FakeFactory(maker=maker)
    runner = RLMRunner(factory=factory)
    ctx_a, lease_a = _context(request="a")
    ctx_b, lease_b = _context(request="b")

    results = await asyncio.gather(_collect(runner, ctx_a), _collect(runner, ctx_b))

    assert len(factory.created) == 2
    assert factory.created[0] is not factory.created[1]
    assert all(outcome is not None and outcome.terminal_status == "completed" for _events, outcome in results)
    assert lease_a.released == 0
    assert lease_b.released == 0


@pytest.mark.asyncio
async def test_sequences_strictly_increase_for_one_run() -> None:
    ctx, _ = _context()
    events, _outcome = await _collect(RLMRunner(factory=_FakeFactory(_FakeRLM())), ctx)
    sequences = [e.sequence for e in events]
    assert sequences == list(range(1, len(sequences) + 1))


@pytest.mark.asyncio
async def test_runner_activates_selected_skills_and_streams_details_before_text() -> None:
    card = SkillCard(
        id=uuid4(),
        name="long-context",
        description="Analyze large inputs",
        scope="system",
        version="1.0.0",
        trust="system",
        affordances=("analyze",),
        resources_available=True,
    )
    blueprint = TurnCapabilityBlueprint(activated_skills=(card,))
    context, _ = _context()
    context = replace(context, capability_resolver=_Resolver(blueprint))

    events, outcome = await _collect(RLMRunner(factory=_FakeFactory(_FakeRLM())), context)
    kinds = [event.kind for event in events]

    assert kinds.index(RuntimeEventKind.SKILL_ACTIVATED) < kinds.index(RuntimeEventKind.RLM_REASONING)
    assert RuntimeEventKind.RLM_REASONING in kinds
    assert outcome is not None
    assert any(part["kind"] == "rlm.reasoning" for part in outcome.detail_parts)


@pytest.mark.asyncio
async def test_runner_passes_registered_sandbox_serializable_to_typed_contract() -> None:
    import dspy

    from fleet_rlm.skills.capabilities import TaskContract

    class Corpus(dspy.SandboxSerializable):
        def sandbox_setup(self) -> str:
            return ""

        def to_sandbox(self) -> bytes:
            return b"one\ntwo"

        def sandbox_assignment(self, var_name: str, data_expr: str) -> str:
            return f"{var_name} = {data_expr}.decode()"

        def rlm_preview(self, max_chars: int = 500) -> str:
            return "Corpus with 2 lines"

    class CorpusSignature(dspy.Signature):
        request: str = dspy.InputField()
        corpus: Corpus = dspy.InputField()
        answer: str = dspy.OutputField()

    corpus = Corpus()
    contract = TaskContract(
        id="corpus-answer",
        signature=CorpusSignature,
        input_mapper=lambda context: {"request": context.request},
        output_serializer=lambda prediction: {
            "answer": prediction.answer,
            "provider": "api_key=should-not-persist /home/daytona/private",
        },
    )
    blueprint = TurnCapabilityBlueprint(
        task_contract=contract,
        input_values=MappingProxyType({"request": "hello", "corpus": corpus}),
    )
    capture: dict[str, Any] = {}
    context, _ = _context()
    context = replace(context, capability_resolver=_Resolver(blueprint))

    _events, outcome = await _collect(
        RLMRunner(factory=_FakeFactory(_FakeRLM(answer="typed", capture=capture))),
        context,
    )

    assert capture["kwargs"]["corpus"] is corpus
    assert outcome is not None
    assert outcome.structured_output == {
        "answer": "typed",
        "provider": "[redacted] [path]",
    }
    assert outcome.result_schema_id == "corpus-answer"
