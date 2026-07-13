"""Resolve optional Skill capability packages into one Turn blueprint."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Protocol
from uuid import UUID

import dspy

from fleet_rlm.rlm.context import RLMTurnContext
from fleet_rlm.rlm.signature import FleetRLMSignature
from fleet_rlm.skills.models import SkillCard

RLMTool = Callable[..., Any] | dspy.Tool
InputAdapter = Callable[[RLMTurnContext], Mapping[str, Any]]
OutputValidator = Callable[[Mapping[str, Any]], None]
_RESERVED_TOOL_NAMES = frozenset({"llm_query", "llm_query_batched", "SUBMIT", "print"})


class SkillComposedFleetRLMSignature(FleetRLMSignature):
    """Default task contract extended only when selected Skills add knowledge."""

    capability_knowledge: list[str] = dspy.InputField(
        desc="Bounded host-registered knowledge contributed by activated Skills"
    )


@dataclass(frozen=True, slots=True)
class CapabilityBudgetRequirements:
    """Minimum host budget required to activate a capability safely."""

    min_iterations: int = 0
    min_llm_calls: int = 0
    min_output_chars: int = 0

    def validate(self, context: RLMTurnContext) -> None:
        budget = context.budget
        if (
            budget.max_iterations < self.min_iterations
            or budget.max_llm_calls < self.min_llm_calls
            or budget.max_output_chars < self.min_output_chars
        ):
            raise ValueError("Turn budget does not satisfy selected capability")


@dataclass(frozen=True, slots=True)
class TaskContract:
    """Registered typed DSPy task contract; never supplied as Skill code."""

    id: str
    signature: type[dspy.Signature]
    input_mapper: InputAdapter
    output_serializer: Callable[[Any], Mapping[str, Any]]
    schema_version: str = "1"
    validator: OutputValidator | None = None

    def serialize(self, prediction: Any) -> dict[str, Any]:
        value = dict(self.output_serializer(prediction))
        if self.validator is not None:
            self.validator(value)
        return value


@dataclass(frozen=True, slots=True)
class CapabilityRegistration:
    id: str
    tools: tuple[RLMTool, ...] = ()
    task_contract: TaskContract | None = None
    input_adapters: tuple[InputAdapter, ...] = ()
    validators: tuple[OutputValidator, ...] = ()
    knowledge: tuple[str, ...] = ()
    budget_requirements: CapabilityBudgetRequirements = CapabilityBudgetRequirements()


class CapabilityRegistry:
    """Host-owned registry of executable and typed capability references."""

    def __init__(self) -> None:
        self._items: dict[str, CapabilityRegistration] = {}

    def register(
        self,
        capability_id: str,
        *,
        tools: Sequence[RLMTool] = (),
        task_contract: TaskContract | None = None,
        input_adapters: Sequence[InputAdapter] = (),
        validators: Sequence[OutputValidator] = (),
        knowledge: Sequence[str] = (),
        budget_requirements: CapabilityBudgetRequirements | None = None,
    ) -> CapabilityRegistration:
        key = capability_id.strip()
        if not key or len(key) > 128 or key in self._items:
            raise ValueError("capability id is empty or already registered")
        tool_values = tuple(tools)
        for tool in tool_values:
            name = _tool_name(tool)
            if not name.isidentifier() or name in _RESERVED_TOOL_NAMES:
                raise ValueError("capability tool name is invalid or reserved")
        knowledge_values = tuple(str(item) for item in knowledge)
        if any(len(item) > 4_000 for item in knowledge_values) or sum(map(len, knowledge_values)) > 16_000:
            raise ValueError("capability knowledge exceeds host bounds")
        item = CapabilityRegistration(
            id=key,
            tools=tool_values,
            task_contract=task_contract,
            input_adapters=tuple(input_adapters),
            validators=tuple(validators),
            knowledge=knowledge_values,
            budget_requirements=budget_requirements or CapabilityBudgetRequirements(),
        )
        self._items[key] = item
        return item

    def get(self, capability_id: str) -> CapabilityRegistration | None:
        return self._items.get(capability_id)


@dataclass(frozen=True, slots=True)
class SkillSelection:
    selected_skill_ids: tuple[UUID, ...] = ()
    primary_skill_id: UUID | None = None


class SkillSelector(Protocol):
    async def select(
        self,
        *,
        request: str,
        history: Any,
        cards: tuple[SkillCard, ...],
        attachments: tuple[Any, ...],
        sub_lm: Any,
    ) -> SkillSelection: ...


class SkillSelectionSignature(dspy.Signature):
    """Select only useful authorized Skills; an empty selection is valid."""

    request: str = dspy.InputField()
    history: list[dict] = dspy.InputField()
    skill_cards: list[dict] = dspy.InputField()
    attachments: list[dict] = dspy.InputField()
    selected_skill_ids: list[str] = dspy.OutputField()
    primary_skill_id: str = dspy.OutputField(desc="Selected primary Skill UUID, or empty string")


class DSPySkillSelector:
    """Typed, bounded preflight selector using the configured Sub Model."""

    async def select(
        self,
        *,
        request: str,
        history: Any,
        cards: tuple[SkillCard, ...],
        attachments: tuple[Any, ...],
        sub_lm: Any,
    ) -> SkillSelection:
        from fleet_rlm.rlm.inputs import attachment_metadata, skill_card_metadata

        messages = list(getattr(history, "messages", history) or [])[-8:]
        predictor = dspy.Predict(SkillSelectionSignature)
        with dspy.settings.context(lm=sub_lm):
            result = await predictor.acall(
                request=request,
                history=[dict(item) for item in messages if isinstance(item, dict)],
                skill_cards=[skill_card_metadata(card) for card in cards],
                attachments=[attachment_metadata(item) for item in attachments],
            )
        selected: list[UUID] = []
        for raw in list(getattr(result, "selected_skill_ids", []) or []):
            selected.append(UUID(str(raw)))
        primary_raw = str(getattr(result, "primary_skill_id", "") or "").strip()
        return SkillSelection(
            selected_skill_ids=tuple(selected),
            primary_skill_id=UUID(primary_raw) if primary_raw else None,
        )


@dataclass(frozen=True, slots=True)
class TurnCapabilityBlueprint:
    activated_skills: tuple[SkillCard, ...] = ()
    tools: tuple[RLMTool, ...] = ()
    task_contract: TaskContract | None = None
    input_values: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    validators: tuple[OutputValidator, ...] = ()
    knowledge: tuple[str, ...] = ()

    @property
    def signature(self) -> type[dspy.Signature]:
        if self.task_contract is not None:
            return self.task_contract.signature
        if self.knowledge:
            return SkillComposedFleetRLMSignature
        return FleetRLMSignature


def _tool_name(tool: RLMTool) -> str:
    if isinstance(tool, dspy.Tool):
        return str(tool.name)
    return str(getattr(tool, "__name__", ""))


class CapabilityResolver:
    """Validate a selector result and compose a safe zero-to-four Skill blueprint."""

    def __init__(
        self,
        registry: CapabilityRegistry,
        *,
        selector: SkillSelector | None = None,
        max_selected_skills: int = 4,
    ) -> None:
        self._registry = registry
        self._selector = selector if selector is not None else DSPySkillSelector()
        self._max_selected = max(0, int(max_selected_skills))

    async def resolve(self, context: RLMTurnContext) -> TurnCapabilityBlueprint:
        cards = tuple(context.skill_cards or ())
        try:
            selection = await self._selector.select(
                request=context.request,
                history=context.history,
                cards=cards,
                attachments=tuple(context.attachments or ()),
                sub_lm=context.models.sub_lm,
            )
            return self._compose(context, cards, selection)
        except Exception:  # noqa: BLE001 - selector/capability failure degrades to no Skills
            return TurnCapabilityBlueprint(tools=tuple(context.tools or ()))

    def _compose(
        self,
        context: RLMTurnContext,
        cards: tuple[SkillCard, ...],
        selection: SkillSelection,
    ) -> TurnCapabilityBlueprint:
        selected_ids = tuple(dict.fromkeys(selection.selected_skill_ids))
        if len(selected_ids) > self._max_selected:
            raise ValueError("too many selected Skills")
        by_id = {card.id: card for card in cards}
        if any(skill_id not in by_id for skill_id in selected_ids):
            raise ValueError("selector returned an unauthorized Skill")
        if selection.primary_skill_id is not None and selection.primary_skill_id not in selected_ids:
            raise ValueError("primary Skill is not selected")

        selected = tuple(by_id[skill_id] for skill_id in selected_ids)
        if any(card.trust == "untrusted" for card in selected):
            raise ValueError("untrusted Skill cannot activate host capabilities")
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
            registration.budget_requirements.validate(context)

        tools: list[RLMTool] = []
        seen_tools: dict[str, RLMTool] = {}
        for tool in (*tuple(context.tools or ()), *(tool for item in registrations for tool in item.tools)):
            name = _tool_name(tool)
            if not name:
                raise ValueError("capability tool has no valid name")
            previous = seen_tools.get(name)
            if previous is not None:
                if previous is tool:
                    continue
                raise ValueError("capability tool name conflict")
            seen_tools[name] = tool
            tools.append(tool)

        task_contract: TaskContract | None = None
        if selection.primary_skill_id is not None:
            primary = by_id[selection.primary_skill_id]
            if primary.task_contract_ref:
                registration = self._registry.get(primary.task_contract_ref)
                if registration is None or registration.task_contract is None:
                    raise ValueError("primary Skill task contract is unavailable")
                task_contract = registration.task_contract

        bound_inputs: dict[str, Any] = {}
        if task_contract is not None:
            bound_inputs.update(dict(task_contract.input_mapper(context)))
        for registration in registrations:
            for adapter in registration.input_adapters:
                for key, value in dict(adapter(context)).items():
                    if key in bound_inputs:
                        raise ValueError("capability input adapter field conflict")
                    bound_inputs[key] = value

        blueprint = TurnCapabilityBlueprint(
            activated_skills=selected,
            tools=tuple(tools),
            task_contract=task_contract,
            input_values=MappingProxyType(bound_inputs),
            validators=tuple(validator for item in registrations for validator in item.validators),
            knowledge=tuple(value for item in registrations for value in item.knowledge),
        )
        signature_fields = getattr(blueprint.signature, "fields", {})
        if any(key not in signature_fields for key in bound_inputs):
            raise ValueError("capability input adapter targets an unknown Signature field")
        return blueprint
