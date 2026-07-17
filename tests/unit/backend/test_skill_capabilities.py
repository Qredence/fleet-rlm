"""Skill capability composition through the public registry/resolver seam."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import dspy
import pytest

from fleet_rlm.rlm.dspy_contract import RLMOptions
from fleet_rlm.rlm.model_bundle import RLMModelBundle
from fleet_rlm.rlm.tool_observer import ToolEventView
from fleet_rlm.skills.capabilities import (
    CapabilityRegistry,
    CapabilityResolutionContext,
    CapabilityResolver,
    CapabilityRLMRequirements,
    TaskContract,
    TurnCapabilityBlueprint,
)
from fleet_rlm.skills.models import SkillCard, SkillRecord


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


def _record(card: SkillCard, *, markdown: str | None = None) -> SkillRecord:
    return SkillRecord(
        id=card.id,
        name=card.name,
        description=card.description,
        scope=card.scope,
        version=card.version,
        trust=card.trust,
        visibility="visible",
        workspace_id=None,
        affordances=card.affordances,
        resources_available=card.resources_available,
        instructions=f"Use {card.name}.",
        skill_markdown=markdown or f"---\nname: {card.name}\ndescription: {card.description}\n---\nUse it.",
        capability_refs=card.capability_refs,
        task_contract_ref=card.task_contract_ref,
    )


def _context(
    cards: tuple[SkillCard, ...],
    tools: tuple[Any, ...] = (),
    *,
    selected: tuple[SkillRecord, ...] = (),
) -> CapabilityResolutionContext:
    return CapabilityResolutionContext(
        request="analyze the long document",
        models=RLMModelBundle(root_lm=object(), sub_lm=object()),
        options=RLMOptions(max_iterations=3, max_llm_calls=5, max_output_chars=1000),
        history=[],
        skill_cards=cards,
        selected_skills=selected,
        tools=tools,
    )


@pytest.mark.asyncio
async def test_explicit_records_compose_tools_contract_and_immutable_instructions() -> None:
    def base_tool() -> str:
        return "base"

    def analyze(value: str) -> str:
        return value.upper()

    lookup = dspy.Tool(lambda key: {"key": key}, name="lookup", desc="Lookup knowledge")

    class ReportSignature(dspy.Signature):
        request: str = dspy.InputField()
        summary: str = dspy.OutputField()
        findings: list[str] = dspy.OutputField()

    async def report_inputs(context):
        return {"request": context.request}

    contract = TaskContract(
        id="report-v1",
        schema_version="1",
        signature=ReportSignature,
        input_mapper=report_inputs,
        text_field="summary",
    )
    registry = CapabilityRegistry()
    registry.register(
        "analysis",
        tools=(analyze, lookup),
        knowledge=("Use evidence before conclusions.",),
        rlm_requirements=CapabilityRLMRequirements(min_iterations=2),
    )
    registry.register("report", task_contract=contract)

    primary = _card("long-context", refs=("analysis", "report"), contract="report")
    auxiliary = _card("knowledge", refs=("analysis",))
    records = (_record(primary), _record(auxiliary))
    blueprint = await CapabilityResolver(registry).resolve(
        _context((primary, auxiliary), tools=(dspy.Tool(base_tool),), selected=records)
    )

    assert [card.id for card in blueprint.activated_skills] == [primary.id, auxiliary.id]
    assert blueprint.skill_cards == (primary, auxiliary)
    assert blueprint.signature is not ReportSignature
    assert blueprint.signature.instructions.startswith(ReportSignature.instructions)
    assert records[0].skill_markdown in blueprint.signature.instructions
    assert set(blueprint.signature.fields) == {"request", "skill_cards", "summary", "findings"}
    assert blueprint.task_contract is contract
    assert blueprint.knowledge == ("Use evidence before conclusions.",)
    assert [getattr(tool, "name", getattr(tool, "__name__", "")) for tool in blueprint.tools] == [
        "base_tool",
        "analyze",
        "lookup",
    ]


@pytest.mark.asyncio
async def test_ordinary_discovery_exposes_all_cards_without_calling_sub_model_or_host_capabilities() -> None:
    from fleet_rlm.files.workspace_models import WorkspaceCapabilityMetadata

    card = _card("skill", refs=("host",))
    registry = CapabilityRegistry()

    def host() -> str:
        return "host"

    registry.register("host", tools=(host,))
    cards = (card,)
    context = _context(cards)
    context = CapabilityResolutionContext(
        request=context.request,
        history=context.history,
        models=context.models,
        options=context.options,
        skill_cards=cards,
        selected_skills=(),
        attachments=context.attachments,
        tools=context.tools,
        workspace=WorkspaceCapabilityMetadata(True, ".", "durable"),
    )
    blueprint = await CapabilityResolver(registry).resolve(context)

    assert blueprint.skill_cards == cards
    assert blueprint.activated_skills == ()
    assert blueprint.preloaded_skill_markdown == ()
    assert blueprint.tools == ()
    assert blueprint.workspace == context.workspace


@pytest.mark.asyncio
async def test_async_input_mapper_is_cancelled_without_background_composition() -> None:
    import asyncio

    class ResultSignature(dspy.Signature):
        request: str = dspy.InputField()
        answer: str = dspy.OutputField()

    cancelled = asyncio.Event()

    async def blocking_mapper(_context):
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    registry = CapabilityRegistry()
    registry.register(
        "result",
        task_contract=TaskContract(
            id="result",
            schema_version="1",
            signature=ResultSignature,
            input_mapper=blocking_mapper,
            text_field="answer",
        ),
    )
    card = _card("result", refs=("result",), contract="result")

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(
            CapabilityResolver(registry).resolve(_context((card,), selected=(_record(card),))),
            timeout=0.01,
        )
    assert cancelled.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize("selected_count", [1, 4])
async def test_resolver_accepts_one_or_four_explicit_skills(selected_count: int) -> None:
    cards = tuple(_card(f"skill-{index}") for index in range(4))
    selected = tuple(_record(card) for card in cards[:selected_count])
    blueprint = await CapabilityResolver(CapabilityRegistry()).resolve(_context(cards, selected=selected))

    assert tuple(card.id for card in blueprint.activated_skills) == tuple(record.id for record in selected)


@pytest.mark.asyncio
async def test_resolver_rejects_more_than_four_explicit_skills_and_preload_overflow() -> None:
    cards = tuple(_card(f"skill-{index}") for index in range(5))
    with pytest.raises(ValueError, match="too many"):
        await CapabilityResolver(CapabilityRegistry()).resolve(
            _context(cards, selected=tuple(_record(card) for card in cards))
        )
    one = cards[0]
    with pytest.raises(ValueError, match="preloaded"):
        await CapabilityResolver(CapabilityRegistry()).resolve(
            _context((one,), selected=(_record(one, markdown="x" * (128 * 1024 + 1)),))
        )


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

    with pytest.raises(ValueError, match="conflict"):
        await CapabilityResolver(registry).resolve(_context((one, two), selected=(_record(one), _record(two))))
    with pytest.raises(ValueError, match="unknown"):
        await CapabilityResolver(registry).resolve(_context((unknown,), selected=(_record(unknown),)))


@pytest.mark.asyncio
async def test_optional_tool_conflict_and_invalid_base_abort() -> None:
    def base() -> str:
        return "base"

    def optional() -> str:
        return "optional"

    optional.__name__ = "base"
    base_tool = dspy.Tool(base)
    registry = CapabilityRegistry()
    registry.register("optional", tools=(optional,))
    card = _card("optional", refs=("optional",))

    with pytest.raises(ValueError, match="conflict"):
        await CapabilityResolver(registry).resolve(_context((card,), tools=(base_tool,), selected=(_record(card),)))

    with pytest.raises(TypeError, match="dspy.Tool"):
        await CapabilityResolver(CapabilityRegistry()).resolve(_context((), tools=(base,)))


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
    with pytest.raises(ValueError, match="untrusted"):
        await CapabilityResolver(CapabilityRegistry()).resolve(_context((card,), selected=(_record(card),)))


@pytest.mark.asyncio
async def test_resolver_rejects_input_adapter_conflict_before_execution() -> None:
    class TypedSignature(dspy.Signature):
        request: str = dspy.InputField()
        answer: str = dspy.OutputField()

    async def report_inputs(context):
        return {"request": context.request}

    async def async_adapter(_context):
        return {"request": "collision"}

    contract = TaskContract(
        id="typed",
        schema_version="1",
        signature=TypedSignature,
        input_mapper=report_inputs,
        text_field="answer",
    )
    registry = CapabilityRegistry()
    registry.register(
        "typed",
        task_contract=contract,
        input_adapters=(async_adapter,),
    )
    card = _card("typed", refs=("typed",), contract="typed")
    with pytest.raises(ValueError, match="conflict"):
        await CapabilityResolver(registry).resolve(_context((card,), selected=(_record(card),)))


def test_registry_rejects_reserved_tools_and_unbounded_knowledge() -> None:
    registry = CapabilityRegistry()

    def print() -> None:  # noqa: A001 - intentionally exercises the DSPy reserved name
        return None

    with pytest.raises(ValueError, match="reserved"):
        registry.register("reserved", tools=(print,))
    with pytest.raises(ValueError, match="bounds"):
        registry.register("too-large", knowledge=("x" * 4_001,))


def test_registry_normalizes_callables_and_blueprint_enforces_tool_only_unique_names() -> None:
    def lookup(value: int = 3) -> int:
        return value

    view = ToolEventView.metadata_only()
    registration = CapabilityRegistry().register(
        "lookup",
        tools=(lookup,),
        tool_event_views={"lookup": view},
    )

    assert len(registration.tools) == 1
    assert type(registration.tools[0]) is dspy.Tool
    assert registration.tools[0].name == "lookup"
    assert registration.tool_event_views == {"lookup": view}

    with pytest.raises(TypeError, match="dspy.Tool"):
        TurnCapabilityBlueprint(tools=(lookup,))
    with pytest.raises(ValueError, match="duplicate"):
        TurnCapabilityBlueprint(tools=(dspy.Tool(lookup), dspy.Tool(lookup)))


@pytest.mark.parametrize("text_field", ["missing", "request", "optional", "count"])
def test_registry_rejects_invalid_task_contract_text_field(text_field: str) -> None:
    class ResultSignature(dspy.Signature):
        request: str = dspy.InputField()
        answer: str = dspy.OutputField()
        optional: str | None = dspy.OutputField(default=None)
        count: int = dspy.OutputField()

    async def result_inputs(context):
        return {"request": context.request}

    contract = TaskContract(
        id="result",
        schema_version="1",
        signature=ResultSignature,
        input_mapper=result_inputs,
        text_field=text_field,
    )

    with pytest.raises(ValueError, match="text field"):
        CapabilityRegistry().register("result", task_contract=contract)
