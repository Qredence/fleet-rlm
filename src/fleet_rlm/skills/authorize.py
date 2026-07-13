"""Deterministic host authorization for SkillCards."""

from __future__ import annotations

from uuid import UUID

from fleet_rlm.skills.cards import to_card
from fleet_rlm.skills.errors import SkillNotFoundError
from fleet_rlm.skills.models import SkillCard
from fleet_rlm.skills.registry import InMemorySkillRegistry


class SkillAuthorizer:
    """List and authorize skills for a principal — never returns instructions."""

    def __init__(self, registry: InMemorySkillRegistry) -> None:
        self._registry = registry

    def is_authorized(
        self,
        skill_id: UUID,
        *,
        user_id: UUID,
        workspace_id: UUID,
    ) -> bool:
        del user_id  # reserved for future membership checks
        record = self._registry.get(skill_id)
        if record is None:
            return False
        if record.visibility == "hidden":
            return False
        if record.scope == "system":
            return True
        if record.scope == "workspace":
            return record.workspace_id == workspace_id
        return False

    def authorize(
        self,
        skill_id: UUID,
        *,
        user_id: UUID,
        workspace_id: UUID,
    ) -> SkillCard:
        if not self.is_authorized(skill_id, user_id=user_id, workspace_id=workspace_id):
            # Same client shape as missing — no existence leak.
            raise SkillNotFoundError("skill not found")
        record = self._registry.require(skill_id)
        return to_card(record)

    def list_cards(
        self,
        *,
        user_id: UUID,
        workspace_id: UUID,
    ) -> tuple[SkillCard, ...]:
        cards: list[SkillCard] = []
        for skill_id in self._registry.list_ids():
            if self.is_authorized(skill_id, user_id=user_id, workspace_id=workspace_id):
                record = self._registry.require(skill_id)
                cards.append(to_card(record))
        # Stable order by name then id
        cards.sort(key=lambda c: (c.name.lower(), str(c.id)))
        return tuple(cards)

    def get_record_if_authorized(
        self,
        skill_id: UUID,
        *,
        user_id: UUID,
        workspace_id: UUID,
    ):
        """Host-only: authorized full record for future load_skill (impl-14)."""
        if not self.is_authorized(skill_id, user_id=user_id, workspace_id=workspace_id):
            raise SkillNotFoundError("skill not found")
        return self._registry.require(skill_id)
