"""Resolve optional Skill capability packages into one Turn blueprint."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Awaitable

import dspy

from fleet_rlm.files.workspace_models import DENO_WORKSPACE_CAPABILITY, WorkspaceCapabilityMetadata
from fleet_rlm.rlm.dspy_contract import RLMOptions
from fleet_rlm.rlm.model_bundle import RLMModelBundle
from fleet_rlm.rlm.signature import FleetRLMSignature
from fleet_rlm.rlm.tool_observer import ToolEventView
from fleet_rlm.skills.models import SkillCard, SkillRecord

ToolRegistrationInput = Callable[..., Any] | dspy.Tool
InputAdapter = Callable[["CapabilityResolutionContext"], Awaitable[Mapping[str, Any]]]
OutputValidator = Callable[[Mapping[str, Any]], None]
_RESERVED_TOOL_NAMES = frozenset({"llm_query", "llm_query_batched", "SUBMIT", "print"})
_MAX_SELECTED_SKILLS = 4
_MAX_PRELOADED_INSTRUCTION_BYTES = 128 * 1024


@dataclass(frozen=True, slots=True)
class CapabilityResolutionContext:
    """Bounded immutable input for optional Skill capability composition."""

    request: str
    history: Any
    models: RLMModelBundle
    options: RLMOptions
    skill_cards: tuple[SkillCard, ...] = ()
    selected_skills: tuple[SkillRecord, ...] = ()
    attachments: tuple[Any, ...] = ()
    tools: tuple[dspy.Tool, ...] = ()
    tool_event_views: Mapping[str, ToolEventView] = field(default_factory=lambda: MappingProxyType({}))
    workspace: WorkspaceCapabilityMetadata = DENO_WORKSPACE_CAPABILITY
    deadline: float | None = None


class SkillComposedFleetRLMSignature(FleetRLMSignature):
    """Default task contract extended only when selected Skills add knowledge."""

    capability_knowledge: list[str] = dspy.InputField(
        desc="Bounded host-registered knowledge contributed by activated Skills"
    )


@dataclass(frozen=True, slots=True)
class CapabilityRLMRequirements:
    """Minimum native RLM options required to activate a capability safely."""

    min_iterations: int = 0
    min_llm_calls: int = 0
    min_output_chars: int = 0

    def validate(self, context: CapabilityResolutionContext) -> None:
        options = context.options
        if (
            options.max_iterations < self.min_iterations
            or options.max_llm_calls < self.min_llm_calls
            or options.max_output_chars < self.min_output_chars
        ):
            raise ValueError("RLM Options do not satisfy selected capability")


@dataclass(frozen=True, slots=True)
class TaskContract:
    """Registered typed DSPy task contract; never supplied as Skill code."""

    id: str
    schema_version: str
    signature: type[dspy.Signature]
    input_mapper: InputAdapter
    text_field: str
    validator: OutputValidator | None = None


async def _default_input_mapper(_context: CapabilityResolutionContext) -> Mapping[str, Any]:
    return {}


DEFAULT_TASK_CONTRACT = TaskContract(
    id="fleet.default",
    schema_version="1",
    signature=FleetRLMSignature,
    input_mapper=_default_input_mapper,
    text_field="answer",
)


def _validate_task_contract(contract: TaskContract) -> None:
    field = getattr(contract.signature, "fields", {}).get(contract.text_field)
    extra = getattr(field, "json_schema_extra", None) if field is not None else None
    if (
        field is None
        or not isinstance(extra, dict)
        or extra.get("__dspy_field_type") != "output"
        or field.annotation is not str
        or not field.is_required()
    ):
        raise ValueError("task contract text field must be a required non-optional string output")


@dataclass(frozen=True, slots=True)
class CapabilityRegistration:
    id: str
    tools: tuple[dspy.Tool, ...] = ()
    tool_event_views: Mapping[str, ToolEventView] = field(default_factory=lambda: MappingProxyType({}))
    task_contract: TaskContract | None = None
    input_adapters: tuple[InputAdapter, ...] = ()
    validators: tuple[OutputValidator, ...] = ()
    knowledge: tuple[str, ...] = ()
    rlm_requirements: CapabilityRLMRequirements = CapabilityRLMRequirements()


class CapabilityRegistry:
    """Host-owned registry of executable and typed capability references."""

    def __init__(self) -> None:
        self._items: dict[str, CapabilityRegistration] = {}

    def register(
        self,
        capability_id: str,
        *,
        tools: Sequence[ToolRegistrationInput] = (),
        tool_event_views: Mapping[str, ToolEventView] | None = None,
        task_contract: TaskContract | None = None,
        input_adapters: Sequence[InputAdapter] = (),
        validators: Sequence[OutputValidator] = (),
        knowledge: Sequence[str] = (),
        rlm_requirements: CapabilityRLMRequirements | None = None,
    ) -> CapabilityRegistration:
        key = capability_id.strip()
        if not key or len(key) > 128 or key in self._items:
            raise ValueError("capability id is empty or already registered")
        tool_values = tuple(tool if isinstance(tool, dspy.Tool) else dspy.Tool(tool) for tool in tools)
        _validate_tools(tool_values)
        view_values = _validate_event_views(tool_values, tool_event_views or {})
        knowledge_values = tuple(str(item) for item in knowledge)
        if any(len(item) > 4_000 for item in knowledge_values) or sum(map(len, knowledge_values)) > 16_000:
            raise ValueError("capability knowledge exceeds host bounds")
        if task_contract is not None:
            _validate_task_contract(task_contract)
            if not inspect.iscoroutinefunction(task_contract.input_mapper):
                raise ValueError("task contract input mapper must be async")
        if any(not inspect.iscoroutinefunction(adapter) for adapter in input_adapters):
            raise ValueError("capability input adapters must be async")
        item = CapabilityRegistration(
            id=key,
            tools=tool_values,
            tool_event_views=view_values,
            task_contract=task_contract,
            input_adapters=tuple(input_adapters),
            validators=tuple(validators),
            knowledge=knowledge_values,
            rlm_requirements=rlm_requirements or CapabilityRLMRequirements(),
        )
        self._items[key] = item
        return item

    def get(self, capability_id: str) -> CapabilityRegistration | None:
        return self._items.get(capability_id)


@dataclass(frozen=True, slots=True)
class TurnCapabilityBlueprint:
    skill_cards: tuple[SkillCard, ...] = ()
    activated_skills: tuple[SkillCard, ...] = ()
    preloaded_skill_markdown: tuple[str, ...] = ()
    tools: tuple[dspy.Tool, ...] = ()
    tool_event_views: Mapping[str, ToolEventView] = field(default_factory=lambda: MappingProxyType({}))
    task_contract: TaskContract | None = None
    input_values: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    validators: tuple[OutputValidator, ...] = ()
    knowledge: tuple[str, ...] = ()
    workspace: WorkspaceCapabilityMetadata = DENO_WORKSPACE_CAPABILITY

    def __post_init__(self) -> None:
        _validate_tools(self.tools)
        object.__setattr__(self, "tool_event_views", _validate_event_views(self.tools, self.tool_event_views))

    @property
    def signature(self) -> type[dspy.Signature]:
        if self.task_contract is not None:
            base = self.task_contract.signature
            if "skill_cards" not in getattr(base, "fields", {}):
                base = base.append(
                    "skill_cards",
                    dspy.InputField(desc="Authorized Skill Card metadata only (no instruction bodies)"),
                    list[dict],
                )
        elif self.knowledge:
            base = SkillComposedFleetRLMSignature
        else:
            base = FleetRLMSignature
        if not self.preloaded_skill_markdown:
            return base
        instructions = base.instructions
        skill_instructions = "\n\n".join(self.preloaded_skill_markdown)
        return base.with_instructions(f"{instructions}\n\n{skill_instructions}")


def _tool_name(tool: dspy.Tool) -> str:
    return str(tool.name)


def _validate_tools(tools: Sequence[dspy.Tool]) -> None:
    seen: set[str] = set()
    for tool in tools:
        if not isinstance(tool, dspy.Tool):
            raise TypeError("Turn capabilities must contain only dspy.Tool objects")
        name = _tool_name(tool)
        if not name.isidentifier() or name in _RESERVED_TOOL_NAMES:
            raise ValueError("capability tool name is invalid or reserved")
        if name in seen:
            raise ValueError("capability tool name is duplicate")
        seen.add(name)


def _validate_event_views(
    tools: Sequence[dspy.Tool],
    views: Mapping[str, ToolEventView],
) -> Mapping[str, ToolEventView]:
    names = {_tool_name(tool) for tool in tools}
    values = dict(views)
    if any(name not in names for name in values):
        raise ValueError("tool event view names an unavailable Tool")
    if any(not isinstance(view, ToolEventView) for view in values.values()):
        raise TypeError("tool event views must contain ToolEventView values")
    return MappingProxyType(values)


class CapabilityResolver:
    """Compose explicit Skill selections without using a model preflight gate."""

    def __init__(
        self,
        registry: CapabilityRegistry,
        *,
        max_selected_skills: int = 4,
    ) -> None:
        self._registry = registry
        self._max_selected = min(_MAX_SELECTED_SKILLS, max(0, int(max_selected_skills)))

    async def resolve(self, context: CapabilityResolutionContext) -> TurnCapabilityBlueprint:
        return await self._compose(context)

    async def _compose(self, context: CapabilityResolutionContext) -> TurnCapabilityBlueprint:
        cards = tuple(context.skill_cards or ())
        base = TurnCapabilityBlueprint(
            skill_cards=cards,
            tools=tuple(context.tools or ()),
            tool_event_views=context.tool_event_views,
            workspace=context.workspace,
        )
        selected_records = tuple(context.selected_skills or ())
        if not selected_records:
            return base
        await self._check_deadline(context)
        selected_ids = tuple(record.id for record in selected_records)
        if len(selected_ids) > self._max_selected or len(set(selected_ids)) != len(selected_ids):
            raise ValueError("too many selected Skills")
        by_id = {card.id: card for card in cards}
        selected = tuple(
            by_id.get(
                record.id,
                SkillCard(
                    id=record.id,
                    name=record.name,
                    description=record.description,
                    scope=record.scope,
                    version=record.version,
                    trust=record.trust,
                    affordances=record.affordances,
                    resources_available=record.resources_available,
                    capability_refs=record.capability_refs,
                    task_contract_ref=record.task_contract_ref,
                ),
            )
            for record in selected_records
        )
        if any(card.trust == "untrusted" for card in selected):
            raise ValueError("untrusted Skill cannot activate host capabilities")
        skill_markdown = tuple(record.skill_markdown for record in selected_records)
        if sum(len(value.encode("utf-8")) for value in skill_markdown) > _MAX_PRELOADED_INSTRUCTION_BYTES:
            raise ValueError("preloaded Skill instructions exceed host bounds")
        registrations: list[CapabilityRegistration] = []
        seen_capabilities: set[str] = set()
        for card in selected:
            for ref in card.capability_refs:
                if ref in seen_capabilities:
                    continue
                registration = self._registry.get(ref)
                if registration is None:
                    raise ValueError("Skill references an unknown capability")
                registrations.append(registration)
                seen_capabilities.add(ref)
        for registration in registrations:
            registration.rlm_requirements.validate(context)

        tools: list[dspy.Tool] = []
        seen_tools: dict[str, dspy.Tool] = {}
        for tool in (*tuple(context.tools or ()), *(tool for item in registrations for tool in item.tools)):
            name = _tool_name(tool)
            if not name:
                raise ValueError("capability tool has no valid name")
            previous = seen_tools.get(name)
            if previous is not None:
                raise ValueError("capability tool name conflict")
            seen_tools[name] = tool
            tools.append(tool)

        event_views = dict(context.tool_event_views)
        for registration in registrations:
            for name, view in registration.tool_event_views.items():
                if name in event_views:
                    raise ValueError("capability tool event view conflict")
                event_views[name] = view

        task_contract: TaskContract | None = None
        task_contract_refs = tuple(dict.fromkeys(card.task_contract_ref for card in selected if card.task_contract_ref))
        if len(task_contract_refs) > 1:
            raise ValueError("selected Skills require conflicting task contracts")
        if task_contract_refs:
            registration = self._registry.get(task_contract_refs[0])
            if registration is None or registration.task_contract is None:
                raise ValueError("selected Skill task contract is unavailable")
            task_contract = registration.task_contract

        bound_inputs: dict[str, Any] = {}
        if task_contract is not None:
            bound_inputs.update(dict(await task_contract.input_mapper(context)))
            await self._check_deadline(context)
        for registration in registrations:
            for adapter in registration.input_adapters:
                for key, value in dict(await adapter(context)).items():
                    if key in bound_inputs:
                        raise ValueError("capability input adapter field conflict")
                    bound_inputs[key] = value
                await self._check_deadline(context)

        blueprint = TurnCapabilityBlueprint(
            skill_cards=cards,
            activated_skills=selected,
            preloaded_skill_markdown=skill_markdown,
            tools=tuple(tools),
            tool_event_views=event_views,
            task_contract=task_contract,
            input_values=MappingProxyType(bound_inputs),
            validators=tuple(validator for item in registrations for validator in item.validators),
            knowledge=tuple(value for item in registrations for value in item.knowledge),
            workspace=context.workspace,
        )
        signature_fields = getattr(blueprint.signature, "fields", {})
        if any(key not in signature_fields for key in bound_inputs):
            raise ValueError("capability input adapter targets an unknown Signature field")
        return blueprint

    @staticmethod
    async def _check_deadline(context: CapabilityResolutionContext) -> None:
        await asyncio.sleep(0)
        if context.deadline is not None and asyncio.get_running_loop().time() >= context.deadline:
            raise TimeoutError("Turn preparation timed out")
