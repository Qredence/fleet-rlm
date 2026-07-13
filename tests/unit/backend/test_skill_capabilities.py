"""Skill capability composition through the public registry/resolver seam."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import dspy
import pytest

from fleet_rlm.rlm.budgets import RunBudget
from fleet_rlm.rlm.model_bundle import RLMModelBundle
from fleet_rlm.skills.capabilities import (
    CapabilityBudgetRequirements,
    CapabilityRegistry,
    CapabilityResolutionContext,
    CapabilityResolver,
    SkillSelection,
    TaskContract,
)
from fleet_rlm.skills.models import SkillCard


class _Selector:
    def __init__(self, selection: SkillSelection | BaseException) -> None:
        self.selection = selection

    async def select(self, **_kwargs: Any) -> SkillSelection:
        if isinstance(self.selection, BaseException):
            raise self.selection
        return self.selection


def _card(name: str, *, refs: tuple[str, ...] = (), contract: str | None = None) -> SkillCard:
    return SkillCard(
        id=uuid4(),
        name=name,
        description=f"{name} capability",
        scope="system",
        version="1.0.0",
        trust="system",
        affordances=("load",),
        resources_available=False,
        capability_refs=refs,
        task_contract_ref=contract,
    )


def _context(cards: tuple[SkillCard, ...], tools: tuple[Any, ...] = ()) -> CapabilityResolutionContext:
    return CapabilityResolutionContext(
        request="analyze the long document",
        models=RLMModelBundle(root_lm=object(), sub_lm=object()),
        budget=RunBudget(max_iterations=3, max_llm_calls=5, max_output_chars=1000),
        history=[],
        skill_cards=cards,
        tools=tools,
    )


@pytest.mark.asyncio
async def test_resolver_composes_plain_callable_dspy_tool_and_primary_contract() -> None:
    def base_tool() -> str:
        return "base"

    def analyze(value: str) -> str:
        return value.upper()

    lookup = dspy.Tool(lambda key: {"key": key}, name="lookup", desc="Lookup knowledge")

    class ReportSignature(dspy.Signature):
        request: str = dspy.InputField()
        findings: list[str] = dspy.OutputField()

    contract = TaskContract(
        id="report-v1",
        signature=ReportSignature,
        input_mapper=lambda context: {"request": context.request},
        output_serializer=lambda prediction: {"findings": list(prediction.findings)},
    )
    registry = CapabilityRegistry()
    registry.register(
        "analysis",
        tools=(analyze, lookup),
        knowledge=("Use evidence before conclusions.",),
        budget_requirements=CapabilityBudgetRequirements(min_iterations=2),
    )
    registry.register("report", task_contract=contract)

    primary = _card("long-context", refs=("analysis", "report"), contract="report")
    auxiliary = _card("knowledge", refs=("analysis",))
    selection = SkillSelection(
        selected_skill_ids=(primary.id, auxiliary.id),
        primary_skill_id=primary.id,
    )

    blueprint = await CapabilityResolver(registry, selector=_Selector(selection)).resolve(
        _context((primary, auxiliary), tools=(base_tool,))
    )

    assert [card.id for card in blueprint.activated_skills] == [primary.id, auxiliary.id]
    assert blueprint.signature is ReportSignature
    assert blueprint.task_contract is contract
    assert blueprint.knowledge == ("Use evidence before conclusions.",)
    assert [getattr(tool, "name", getattr(tool, "__name__", "")) for tool in blueprint.tools] == [
        "base_tool",
        "analyze",
        "lookup",
    ]


@pytest.mark.asyncio
async def test_resolver_falls_back_to_no_skills_on_selector_failure_or_more_than_four() -> None:
    cards = tuple(_card(f"skill-{index}") for index in range(5))
    context = _context(cards)
    failed = await CapabilityResolver(
        CapabilityRegistry(),
        selector=_Selector(RuntimeError("selector unavailable")),
    ).resolve(context)
    overflow = await CapabilityResolver(
        CapabilityRegistry(),
        selector=_Selector(SkillSelection(selected_skill_ids=tuple(card.id for card in cards))),
    ).resolve(context)

    assert failed.activated_skills == ()
    assert overflow.activated_skills == ()
    assert failed.tools == () == overflow.tools


@pytest.mark.asyncio
@pytest.mark.parametrize("selected_count", [0, 1, 4])
async def test_resolver_accepts_zero_one_or_four_authorized_skills(selected_count: int) -> None:
    cards = tuple(_card(f"skill-{index}") for index in range(4))
    selected = tuple(card.id for card in cards[:selected_count])
    blueprint = await CapabilityResolver(
        CapabilityRegistry(),
        selector=_Selector(SkillSelection(selected_skill_ids=selected)),
    ).resolve(_context(cards))

    assert tuple(card.id for card in blueprint.activated_skills) == selected


@pytest.mark.asyncio
async def test_resolver_rejects_unknown_or_conflicting_capability_references() -> None:
    registry = CapabilityRegistry()

    def first() -> str:
        return "first"

    def second() -> str:
        return "second"

    first.__name__ = "collision"
    second.__name__ = "collision"
    registry.register("one", tools=(first,))
    registry.register("two", tools=(second,))
    one = _card("one", refs=("one",))
    two = _card("two", refs=("two",))
    unknown = _card("unknown", refs=("missing",))

    conflict = await CapabilityResolver(
        registry,
        selector=_Selector(SkillSelection((one.id, two.id), one.id)),
    ).resolve(_context((one, two)))
    missing = await CapabilityResolver(
        registry,
        selector=_Selector(SkillSelection((unknown.id,), unknown.id)),
    ).resolve(_context((unknown,)))

    assert conflict.activated_skills == ()
    assert missing.activated_skills == ()


@pytest.mark.asyncio
async def test_resolver_rejects_untrusted_selected_skill() -> None:
    card = _card("untrusted")
    card = SkillCard(
        id=card.id,
        name=card.name,
        description=card.description,
        scope=card.scope,
        version=card.version,
        trust="untrusted",
        affordances=card.affordances,
        resources_available=card.resources_available,
    )
    blueprint = await CapabilityResolver(
        CapabilityRegistry(),
        selector=_Selector(SkillSelection((card.id,), card.id)),
    ).resolve(_context((card,)))

    assert blueprint.activated_skills == ()


@pytest.mark.asyncio
async def test_resolver_falls_back_before_execution_on_input_adapter_conflict() -> None:
    class TypedSignature(dspy.Signature):
        request: str = dspy.InputField()
        answer: str = dspy.OutputField()

    contract = TaskContract(
        id="typed",
        signature=TypedSignature,
        input_mapper=lambda context: {"request": context.request},
        output_serializer=lambda prediction: {"answer": prediction.answer},
    )
    registry = CapabilityRegistry()
    registry.register(
        "typed",
        task_contract=contract,
        input_adapters=(lambda _context: {"request": "collision"},),
    )
    card = _card("typed", refs=("typed",), contract="typed")
    blueprint = await CapabilityResolver(
        registry,
        selector=_Selector(SkillSelection((card.id,), card.id)),
    ).resolve(_context((card,)))

    assert blueprint.activated_skills == ()


def test_registry_rejects_reserved_tools_and_unbounded_knowledge() -> None:
    registry = CapabilityRegistry()

    def print() -> None:  # noqa: A001 - intentionally exercises the DSPy reserved name
        return None

    with pytest.raises(ValueError, match="reserved"):
        registry.register("reserved", tools=(print,))
    with pytest.raises(ValueError, match="bounds"):
        registry.register("too-large", knowledge=("x" * 4_001,))
