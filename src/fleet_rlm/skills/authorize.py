"""Deterministic, non-leaking host authorization for Skills."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from fleet_rlm.skills.cards import to_card
from fleet_rlm.skills.errors import SkillNotFoundError, SkillValidationError
from fleet_rlm.skills.models import SkillCard, SkillRecord, SkillSelectionRef
from fleet_rlm.skills.registry import InMemorySkillRegistry


class InvalidSkillSelectionError(SkillValidationError):
    """Generic explicit-selection failure safe for narrow API translation."""

    def __init__(self) -> None:
        super().__init__("invalid skill selection")


class SkillAuthorizer:
    """List and authorize Skills without exposing unauthorized records."""

    def __init__(self, registry: InMemorySkillRegistry) -> None:
        self._registry = registry

    def _scope_authorized(self, record: SkillRecord, *, workspace_id: UUID) -> bool:
        if record.scope == "system":
            return True
        return record.scope == "workspace" and record.workspace_id == workspace_id

    def is_authorized(
        self,
        skill_id: UUID,
        *,
        user_id: UUID,
        workspace_id: UUID,
        include_hidden: bool = False,
    ) -> bool:
        del user_id  # reserved for future membership checks
        record = self._registry.get(skill_id)
        if record is None or (record.visibility == "hidden" and not include_hidden):
            return False
        return self._scope_authorized(record, workspace_id=workspace_id)

    def authorize(
        self,
        skill_id: UUID,
        *,
        user_id: UUID,
        workspace_id: UUID,
    ) -> SkillCard:
        if not self.is_authorized(skill_id, user_id=user_id, workspace_id=workspace_id):
            raise SkillNotFoundError("skill not found")
        return to_card(self._registry.require(skill_id))

    def list_cards(self, *, user_id: UUID, workspace_id: UUID) -> tuple[SkillCard, ...]:
        cards = [
            to_card(self._registry.require(skill_id))
            for skill_id in self._registry.list_ids()
            if self.is_authorized(skill_id, user_id=user_id, workspace_id=workspace_id)
        ]
        cards.sort(key=lambda card: (card.name.lower(), str(card.id)))
        return tuple(cards)

    def get_record_if_authorized(
        self,
        skill_id: UUID,
        *,
        user_id: UUID,
        workspace_id: UUID,
        include_hidden: bool = False,
    ) -> SkillRecord:
        """Return a host record after scope and visibility authorization."""
        if not self.is_authorized(
            skill_id,
            user_id=user_id,
            workspace_id=workspace_id,
            include_hidden=include_hidden,
        ):
            raise SkillNotFoundError("skill not found")
        return self._registry.require(skill_id)

    def authorize_explicit(
        self,
        selection: SkillSelectionRef,
        *,
        user_id: UUID,
        workspace_id: UUID,
    ) -> SkillRecord:
        """Resolve one exact pinned selection, including explicit-only Skills."""
        try:
            record = self.get_record_if_authorized(
                selection.id,
                user_id=user_id,
                workspace_id=workspace_id,
                include_hidden=True,
            )
        except SkillNotFoundError as exc:
            raise InvalidSkillSelectionError from exc
        if record.version != selection.expected_version:
            raise InvalidSkillSelectionError
        return record

    def authorize_explicit_many(
        self,
        selections: Sequence[SkillSelectionRef],
        *,
        user_id: UUID,
        workspace_id: UUID,
        max_selections: int = 4,
    ) -> tuple[SkillRecord, ...]:
        """Resolve up to four unique exact selections or fail generically."""
        values = tuple(selections)
        ids = tuple(selection.id for selection in values)
        limit = min(4, max(0, int(max_selections)))
        if len(values) > limit or len(set(ids)) != len(ids):
            raise InvalidSkillSelectionError
        return tuple(
            self.authorize_explicit(selection, user_id=user_id, workspace_id=workspace_id) for selection in values
        )
